"""Read-only control of Yandex Market final public Yandex Pay ("green") prices.

Business rule:
- RED: public Yandex Pay price < RRP
- YELLOW: RRP <= public Yandex Pay price < RRP + 5%
- OK: public Yandex Pay price >= RRP + 5%

Promocodes, personal offers and ordinary seller prices are deliberately ignored.
If the public Yandex Pay price cannot be attributed safely, no violation is
claimed and the product is reported as UNVERIFIED.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import time
from math import ceil
from pathlib import Path

import requests

from ozon_agent.business_rules import (
    box_rule,
    canonical_sku,
    is_clearance_sku,
    manual_rrp,
)
from ozon_agent.catalog import normalize_sku
from ozon_agent.telegram import send_telegram
from .client import YandexMarketClient
from .storefront import fetch_pay_price

# Yandex-specific SKUs verified against price-list "Изменение цен номенклатуры за 08.10.2026".
# Preserve shared Ozon rules unchanged. Only previously unmapped Yandex SKU variants.
YANDEX_RRP_FROM_20261008 = {
    normalize_sku("A11"): 6780.0,
    normalize_sku("A13"): 20726.0,
    normalize_sku("AC81"): 157.0,
    normalize_sku("Fr-1040"): 10630.0,
    normalize_sku("MS 207 R"): 3751.0,  # 'гофре 207R' -> 207 ripple
    normalize_sku("MS 501 C"): 2635.0,
    normalize_sku("MS2011"): 535.0,
    normalize_sku("MS2417"): 642.0,
    normalize_sku("MS301"): 2331.0,  # price-list article 301
    normalize_sku("MS304"): 2530.0,  # price-list article 304
    normalize_sku("MS402"): 2375.0,  # price-list article 402
    normalize_sku("MS6958"): 884.0,
    normalize_sku("PB74"): 151.0,
}

RRP_FACTOR = 1.05
REGION_ID = int(os.getenv("YANDEX_MARKET_REGION_ID", "213"))


def load_rrp() -> dict[str, float]:
    raw = os.getenv("RRP_MAP_B64", "").strip()
    if not raw:
        return {}
    source = json.loads(base64.b64decode(raw).decode("utf-8"))
    if not isinstance(source, dict):
        raise ValueError("RRP_MAP_B64 must encode a JSON dictionary")
    return {
        normalize_sku(k): float(v)
        for k, v in source.items()
        if v not in (None, "")
    }


def resolve_rrp(offer_id: str, rrp_map: dict[str, float]) -> tuple[float | None, str]:
    value = manual_rrp(offer_id)
    if value is not None:
        return float(value), "manual"

    rule = box_rule(offer_id)
    if rule:
        base, multiplier = rule
        value = rrp_map.get(normalize_sku(base))
        return (
            float(value) * float(multiplier), "box"
        ) if value is not None else (None, "box_missing")

    canonical = canonical_sku(offer_id)
    canonical_override = manual_rrp(canonical)
    if canonical_override is not None:
        return float(canonical_override), "alias_manual"
    value = rrp_map.get(normalize_sku(canonical))
    if value is not None:
        return float(value), "alias" if normalize_sku(canonical) != normalize_sku(offer_id) else "direct"
    # Explicit fallback: the October 8 reference price list has these SKUs,
    # which are missing from the older shared encoded RRP map.
    value = YANDEX_RRP_FROM_20261008.get(normalize_sku(offer_id))
    if value is not None:
        return float(value), "yandex_price_list_20261008"
    return None, "missing"


def _b2c_url(row: dict) -> str | None:
    for item in row.get("showcaseUrls") or []:
        if str(item.get("showcaseType") or "").upper() == "B2C":
            url = str(item.get("showcaseUrl") or "").strip()
            if url:
                return url
    return None


def _catalog(client: YandexMarketClient) -> tuple[list[dict], list[str], list[dict]]:
    campaigns = client.campaigns()
    available = [c for c in campaigns if c.get("apiAvailability") == "AVAILABLE"]
    business_ids = sorted({
        int((c.get("business") or {}).get("id"))
        for c in available
        if (c.get("business") or {}).get("id")
    })
    if not business_ids:
        raise RuntimeError("No AVAILABLE Yandex Market campaigns/businesses found")

    seller_names = sorted({
        str(c.get("domain") or "").strip()
        for c in available if str(c.get("domain") or "").strip()
    })

    mappings: list[dict] = []
    seen: set[tuple[int, str]] = set()
    for business_id in business_ids:
        for row in client.offer_mappings(business_id):
            offer = row.get("offer") or {}
            offer_id = str(offer.get("offerId") or "").strip()
            if not offer_id:
                continue
            key = (business_id, offer_id)
            if key in seen:
                continue
            seen.add(key)
            mappings.append({"business_id": business_id, **row})
    return mappings, seller_names, campaigns


def classify(price: float, rrp: float) -> tuple[str, float]:
    floor = float(ceil(rrp * RRP_FACTOR))
    if price < rrp:
        return "RED", floor
    if price < floor:
        return "YELLOW", floor
    return "OK", floor


def build_rows(client: YandexMarketClient, rrp_map: dict[str, float]) -> tuple[list[dict], dict]:
    mappings, seller_names, campaigns = _catalog(client)
    rows: list[dict] = []
    session = requests.Session()

    max_products = int(os.getenv("YANDEX_MAX_PRODUCTS", "0") or 0)
    processed = 0

    for mapping in mappings:
        offer = mapping.get("offer") or {}
        offer_id = str(offer.get("offerId") or "").strip()
        name = str(offer.get("name") or "")
        base = {
            "offer_id": offer_id,
            "name": name,
            "business_id": mapping.get("business_id"),
            "card_status": offer.get("cardStatus"),
            "archived": bool(offer.get("archived")),
        }

        if is_clearance_sku(offer_id):
            rows.append({**base, "status": "CLEARANCE"})
            continue
        if base["archived"]:
            rows.append({**base, "status": "ARCHIVED"})
            continue

        rrp, rrp_rule = resolve_rrp(offer_id, rrp_map)
        if rrp is None:
            rows.append({**base, "status": "MISSING_RRP", "rrp_rule": rrp_rule})
            continue

        url = _b2c_url(mapping)
        if not url:
            rows.append({
                **base, "status": "NOT_ON_B2C_SHOWCASE",
                "rrp": rrp, "rrp_rule": rrp_rule,
            })
            continue

        if max_products and processed >= max_products:
            rows.append({
                **base, "status": "NOT_CHECKED_LIMIT",
                "rrp": rrp, "rrp_rule": rrp_rule, "showcase_url": url,
            })
            continue

        observed = fetch_pay_price(
            url, seller_names, region_id=REGION_ID, session=session
        )
        processed += 1
        if not observed.verified or observed.price is None:
            rows.append({
                **base,
                "status": "UNVERIFIED",
                "rrp": rrp,
                "rrp_rule": rrp_rule,
                "showcase_url": observed.source_url,
                "pay_price_status": observed.status,
                "pay_price_match_rule": observed.match_rule,
            })
            time.sleep(0.15)
            continue

        status, floor = classify(float(observed.price), float(rrp))
        rows.append({
            **base,
            "status": status,
            "rrp": rrp,
            "rrp_rule": rrp_rule,
            "green_floor": floor,
            "yandex_pay_price": float(observed.price),
            "showcase_url": observed.source_url,
            "pay_price_status": observed.status,
            "pay_price_match_rule": observed.match_rule,
            "matched_seller_name": observed.seller_name,
            "gap_to_rrp": round(float(rrp) - float(observed.price), 2),
            "gap_to_floor": round(float(floor) - float(observed.price), 2),
        })
        time.sleep(0.15)

    stats = {
        "region_id": REGION_ID,
        "seller_names": seller_names,
        "campaigns": [
            {
                "id": c.get("id"),
                "domain": c.get("domain"),
                "placementType": c.get("placementType"),
                "apiAvailability": c.get("apiAvailability"),
                "businessId": (c.get("business") or {}).get("id"),
            }
            for c in campaigns
        ],
        "catalog_rows": len(mappings),
        "verified": sum(1 for r in rows if r.get("status") in ("RED", "YELLOW", "OK")),
        "red": sum(1 for r in rows if r.get("status") == "RED"),
        "yellow": sum(1 for r in rows if r.get("status") == "YELLOW"),
        "ok": sum(1 for r in rows if r.get("status") == "OK"),
        "unverified": sum(1 for r in rows if r.get("status") == "UNVERIFIED"),
        "missing_rrp": sum(1 for r in rows if r.get("status") == "MISSING_RRP"),
    }
    return rows, stats


def report(rows: list[dict], stats: dict) -> str:
    red = [r for r in rows if r.get("status") == "RED"]
    yellow = [r for r in rows if r.get("status") == "YELLOW"]
    unverified = [r for r in rows if r.get("status") == "UNVERIFIED"]

    lines = [
        "Яндекс Маркет — цена с картой Яндекс Пэй",
        f"Регион контроля: {stats['region_id']} (Москва)",
        f"Подтверждено витринных цен: {stats['verified']}",
        f"🔴 Ниже РРЦ: {len(red)}",
        f"🟡 Ниже РРЦ+5%: {len(yellow)}",
        f"⚪ Не удалось подтвердить Pay-цену: {len(unverified)}",
        "",
        "Промокоды и персональные скидки НЕ учитываются.",
    ]

    for title, subset in (("🔴 Ниже РРЦ", red), ("🟡 Между РРЦ и РРЦ+5%", yellow)):
        if subset:
            lines.extend(("", title))
            for row in subset[:30]:
                price = f"{row['yandex_pay_price']:,.0f}".replace(",", " ")
                rrp = f"{row['rrp']:,.0f}".replace(",", " ")
                floor = f"{row['green_floor']:,.0f}".replace(",", " ")
                lines.append(
                    f"• {row['offer_id']}: Пэй {price} ₽ / РРЦ {rrp} ₽ / цель {floor} ₽"
                )
            if len(subset) > 30:
                lines.append(f"... и ещё {len(subset) - 30}")

    if unverified:
        lines.extend(("", "⚪ Pay-цена не подтверждена (первые 15)"))
        for row in unverified[:15]:
            lines.append(
                f"• {row['offer_id']}: {row.get('pay_price_status') or 'UNKNOWN'}"
            )

    if stats["verified"] == 0:
        lines.extend(("", "КРИТИЧЕСКАЯ ОШИБКА: ни одна цена Яндекс Пэй не подтверждена. Контроль РРЦ НЕ ВЫПОЛНЕН."))
    elif not red and not yellow:
        lines.extend(("", "Нарушений среди подтвержденных цен с картой Пэй нет."))

    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="artifacts/yandex-pay-price-audit.json")
    args = ap.parse_args()

    rrp_map = load_rrp()
    if not rrp_map:
        raise RuntimeError("RRP_MAP_B64 is not configured")

    client = YandexMarketClient()
    rows, stats = build_rows(client, rrp_map)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"stats": stats, "items": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    message = report(rows, stats)
    print(message)
    send_telegram(message)

    # Never signal success when there was no actual storefront verification.
    # A successful catalog API call is NOT a successful Pay price audit.
    if stats["verified"] == 0:
        print("ERROR: zero verified Yandex Pay storefront prices; monitoring unavailable.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
