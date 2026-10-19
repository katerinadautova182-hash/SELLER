"""RRP controller driven by the canonical WB buyer price.

Safe defaults:
- only raises seller base price; never lowers it;
- target is RRP + configurable buffer (5% by default);
- no writes unless WB_REPRICE_APPLY=1;
- skips WB Club discounts and size-specific pricing for automatic writes;
- refuses a single correction larger than WB_REPRICE_MAX_STEP_PCT;
- re-reads buyer price immediately before a write.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from decimal import Decimal, ROUND_CEILING
from math import ceil

from ozon_agent.business_rules import box_rule, canonical_sku, is_clearance_sku, manual_rrp
from ozon_agent.catalog import normalize_sku
from ozon_agent.promo_policy import effective_rrp

from .buyer_price import BuyerPriceUnavailable, fetch_buyer_prices
from .price_check import fetch_catalog, load_rrp, send_message
from .sku_aliases import WB_RRP_ALIASES

UPLOAD_API = "https://discounts-prices-api.wildberries.ru/api/v2/upload/task"


def _truthy(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _reference_price(item: dict, rrp: dict[str, float]) -> tuple[float | None, str]:
    sku = str(item.get("vendorCode", "")).strip()
    if not sku or is_clearance_sku(sku):
        return None, "clearance_or_empty"

    reference = manual_rrp(sku)
    rule = "manual" if reference is not None else ""
    if reference is None:
        box = box_rule(sku)
        if box:
            base_sku, multiplier = box
            base = rrp.get(base_sku)
            reference = base * multiplier if base is not None else None
            rule = "box" if reference is not None else ""
        else:
            canonical = WB_RRP_ALIASES.get(normalize_sku(sku), canonical_sku(sku))
            reference = rrp.get(normalize_sku(canonical))
            if reference is not None:
                rule = "alias" if normalize_sku(canonical) != normalize_sku(sku) else "direct"

    if reference is None:
        return None, "missing"
    return float(effective_rrp(sku, reference, rrp)), rule


def _base_price(item: dict) -> int | None:
    prices = set()
    for size in item.get("sizes") or []:
        value = size.get("price")
        try:
            value = int(value)
        except (TypeError, ValueError):
            continue
        if value > 0:
            prices.add(value)
    if len(prices) != 1:
        return None
    return prices.pop()


def _has_size_specific_price(item: dict) -> bool:
    if item.get("editableSizePrice") is True:
        return True
    return any(
        isinstance(size, dict) and size.get("editableSizePrice") is True
        for size in (item.get("sizes") or [])
    )


def _club_discount(item: dict) -> int:
    values = []
    for raw in [item.get("clubDiscount")] + [
        size.get("clubDiscount") for size in (item.get("sizes") or []) if isinstance(size, dict)
    ]:
        try:
            values.append(int(raw or 0))
        except (TypeError, ValueError):
            pass
    return max(values or [0])


def proposed_base_price(
    current_base: int,
    observed_buyer: int,
    target_buyer: int,
) -> int:
    if min(current_base, observed_buyer, target_buyer) <= 0:
        raise ValueError("prices must be positive")
    if observed_buyer >= target_buyer:
        return current_base
    value = (
        Decimal(current_base)
        * Decimal(target_buyer)
        / Decimal(observed_buyer)
    ).quantize(Decimal("1"), rounding=ROUND_CEILING)
    return int(value)


def _upload_prices(token: str, changes: list[dict]) -> dict:
    payload = {
        "data": [
            {"nmID": int(row["nm_id"]), "price": int(row["proposed_base_price"])}
            for row in changes
        ]
    }
    request = urllib.request.Request(
        UPLOAD_API,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=35) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"WB price upload HTTP {exc.code}: {body}") from None
    if not isinstance(result, dict) or result.get("error"):
        raise RuntimeError("WB price upload returned error: " + str(result)[:500])
    return result


def analyze(items: list[dict], rrp: dict[str, float], buyer_prices: dict) -> list[dict]:
    buffer_pct = Decimal(os.getenv("WB_RRP_BUFFER_PCT", "5"))
    max_step_pct = Decimal(os.getenv("WB_REPRICE_MAX_STEP_PCT", "20"))
    expected_supplier = os.getenv("WB_EXPECTED_SUPPLIER_ID", "").strip()
    expected_supplier_id = int(expected_supplier) if expected_supplier.isdigit() else None

    rows = []
    for item in items:
        nm_raw = item.get("nmID")
        try:
            nm_id = int(nm_raw)
        except (TypeError, ValueError):
            continue
        sku = str(item.get("vendorCode", "")).strip()
        reference, rule = _reference_price(item, rrp)
        if reference is None:
            rows.append({"nm_id": nm_id, "sku": sku, "status": "SKIP_NO_RRP", "rrp_rule": rule})
            continue

        buyer = buyer_prices.get(nm_id)
        target = int(
            (Decimal(str(reference)) * (Decimal(100) + buffer_pct) / Decimal(100))
            .quantize(Decimal("1"), rounding=ROUND_CEILING)
        )
        if buyer is None:
            rows.append(
                {
                    "nm_id": nm_id,
                    "sku": sku,
                    "rrp": reference,
                    "target_buyer_price": target,
                    "status": "SKIP_NO_BUYER_PRICE",
                    "rrp_rule": rule,
                }
            )
            continue

        row = {
            "nm_id": nm_id,
            "sku": sku,
            "rrp": reference,
            "target_buyer_price": target,
            "buyer_price": buyer.wallet_price_rub,
            "public_price": buyer.public_price_rub,
            "wallet_discount_percent": buyer.wallet_discount_percent,
            "supplier_id": buyer.supplier_id,
            "option_id": buyer.option_id,
            "rrp_rule": rule,
            "source": buyer.source,
        }

        if expected_supplier_id and buyer.supplier_id != expected_supplier_id:
            row["status"] = "SKIP_SUPPLIER_MISMATCH"
            rows.append(row)
            continue
        if buyer.wallet_price_rub >= target:
            row["status"] = "OK"
            rows.append(row)
            continue
        if _has_size_specific_price(item):
            row["status"] = "REVIEW_SIZE_SPECIFIC_PRICE"
            rows.append(row)
            continue
        club_discount = _club_discount(item)
        if club_discount > 0:
            row["club_discount"] = club_discount
            row["status"] = "REVIEW_WB_CLUB"
            rows.append(row)
            continue

        base = _base_price(item)
        if base is None:
            row["status"] = "REVIEW_AMBIGUOUS_BASE_PRICE"
            rows.append(row)
            continue

        proposed = proposed_base_price(base, buyer.wallet_price_rub, target)
        step_pct = (Decimal(proposed) / Decimal(base) - Decimal(1)) * Decimal(100)
        row["current_base_price"] = base
        row["proposed_base_price"] = proposed
        row["change_pct"] = float(step_pct.quantize(Decimal("0.01")))
        if step_pct > max_step_pct:
            row["status"] = "REVIEW_STEP_TOO_LARGE"
        else:
            row["status"] = "READY"
        rows.append(row)
    return rows


def _format_message(rows: list[dict], applied: bool) -> str:
    ready = [r for r in rows if r.get("status") == "READY"]
    review = [r for r in rows if str(r.get("status", "")).startswith("REVIEW_")]
    low = [
        r for r in rows
        if isinstance(r.get("buyer_price"), int)
        and isinstance(r.get("target_buyer_price"), int)
        and r["buyer_price"] < r["target_buyer_price"]
    ]
    lines = [
        "WB — контроль РРЦ по цене покупателя",
        f"Проверено: {len(rows)}",
        f"Ниже цели РРЦ+буфер: {len(low)}",
        f"Готово к безопасной корректировке: {len(ready)}",
        f"Требует ручной проверки: {len(review)}",
        "Режим: " + ("ЗАПИСЬ ЦЕН" if applied else "DRY RUN"),
    ]
    for row in low[:25]:
        extra = ""
        if row.get("proposed_base_price"):
            extra = f" → база {row.get('current_base_price')}→{row['proposed_base_price']} ₽"
        lines.append(
            f"{row.get('sku') or row['nm_id']}: покупатель {row['buyer_price']} ₽, "
            f"цель {row['target_buyer_price']} ₽ [{row['status']}]{extra}"
        )
    if len(low) > 25:
        lines.append(f"... и ещё {len(low) - 25}")
    return "\n".join(lines)


def main() -> None:
    token = os.environ["WB_API_KEY"].strip()
    apply = _truthy("WB_REPRICE_APPLY", False)
    rrp = load_rrp()
    if not rrp:
        raise RuntimeError("RRP_MAP_B64 is empty; repricing is impossible safely")

    items = fetch_catalog(token)
    nm_ids = [item.get("nmID") for item in items if item.get("nmID") is not None]
    buyer_prices = fetch_buyer_prices(nm_ids)
    rows = analyze(items, rrp, buyer_prices)

    write_candidates = [row for row in rows if row.get("status") == "READY"]
    upload_result = None
    if apply and write_candidates:
        delay = max(1, int(os.getenv("WB_REPRICE_CONFIRM_SECONDS", "5")))
        time.sleep(delay)
        confirm = fetch_buyer_prices([row["nm_id"] for row in write_candidates])
        confirmed = []
        for row in write_candidates:
            second = confirm.get(row["nm_id"])
            if second is None:
                row["status"] = "ABORT_CONFIRMATION_UNAVAILABLE"
                continue
            if abs(second.wallet_price_rub - row["buyer_price"]) > 1:
                row["status"] = "ABORT_PRICE_CHANGED"
                row["confirmed_buyer_price"] = second.wallet_price_rub
                continue
            if second.wallet_price_rub >= row["target_buyer_price"]:
                row["status"] = "ABORT_ALREADY_OK"
                continue
            confirmed.append(row)

        if confirmed:
            upload_result = _upload_prices(token, confirmed)
            for row in confirmed:
                row["status"] = "SUBMITTED"

    os.makedirs("artifacts", exist_ok=True)
    report = {
        "mode": "apply" if apply else "dry-run",
        "buyer_price_definition": "public WB price + public unauthenticated WB Wallet; excludes personal discounts",
        "rrp_buffer_pct": os.getenv("WB_RRP_BUFFER_PCT", "5"),
        "max_step_pct": os.getenv("WB_REPRICE_MAX_STEP_PCT", "20"),
        "catalog_count": len(items),
        "buyer_prices_count": len(buyer_prices),
        "rows": rows,
        "upload_result": upload_result,
    }
    with open("artifacts/wb-rrp-control.json", "w", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)

    message = _format_message(rows, apply)
    print(message)
    telegram = os.getenv("TELEGRAM_WB_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_WB_CHAT_ID", "").strip()
    has_problem = any(
        row.get("status") not in {"OK", "SKIP_NO_RRP", "SKIP_NO_BUYER_PRICE"}
        for row in rows
    )
    if telegram and chat_id and (has_problem or apply):
        send_message(telegram, chat_id, message)


if __name__ == "__main__":
    try:
        main()
    except BuyerPriceUnavailable as exc:
        raise SystemExit("Buyer price unavailable; no price changes made: " + str(exc))
