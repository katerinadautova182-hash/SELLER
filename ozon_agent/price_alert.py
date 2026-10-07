"""Send Telegram only when the set of real RRP violations changes.

RRP data is supplied through RRP_MAP_B64 GitHub Secret.
Clearance SKUs containing "УЦ" are intentionally ignored.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from math import ceil
from pathlib import Path

from .business_rules import is_clearance_sku, canonical_sku, manual_rrp, box_rule
from .catalog import normalize_sku
from .telegram import send_telegram

RRP_FACTOR = 1.05


def load_rrp_map() -> dict[str, float]:
    raw = os.getenv("RRP_MAP_B64", "").strip()
    if not raw:
        return {}
    decoded = base64.b64decode(raw).decode("utf-8")
    source = json.loads(decoded)
    return {normalize_sku(k): float(v) for k, v in source.items() if v not in (None, "")}


def collect_violations(snapshot: dict, rrp_map: dict[str, float]) -> tuple[list[dict], list[dict], dict]:
    red = []
    yellow = []
    stats = {
        "total": 0,
        "verified": 0,
        "clearance_ignored": 0,
        "rrp_missing": 0,
        "rrp_missing_offer_ids": [],
        "checked": 0,
        "price_not_verified": 0,
        "unverified_offer_ids": [],
    }
    for item in snapshot.get("items", []):
        stats["total"] += 1
        offer_id = str(item.get("offer_id") or "").strip()
        if item.get("customer_price_verified"):
            stats["verified"] += 1
        if item.get("eligibility") in ("CLEARANCE", "OUT_OF_STOCK") or is_clearance_sku(offer_id):
            continue
        reference_sku = canonical_sku(offer_id)
        rrp = manual_rrp(offer_id)
        if rrp is None:
            rule = box_rule(offer_id)
            if rule:
                base_sku, multiplier = rule
                base_rrp = rrp_map.get(base_sku)
                rrp = (base_rrp * multiplier) if base_rrp is not None else None
            else:
                rrp = rrp_map.get(normalize_sku(reference_sku))
        if rrp is None:
            stats["rrp_missing"] += 1
            stats["rrp_missing_offer_ids"].append(offer_id)
            continue
        price = item.get("customer_price")
        if not item.get("customer_price_verified") or price in (None, 0, 0.0):
            stats["price_not_verified"] += 1
            stats["unverified_offer_ids"].append(offer_id)
            continue
        stats["checked"] += 1
        floor = float(ceil(rrp * RRP_FACTOR))
        price = float(price)
        row = {
            "offer_id": offer_id,
            "customer_price": price,
            "rrp": rrp,
            "floor": floor,
            "gap_to_floor": round(floor - price, 2),
            "gap_to_rrp": round(rrp - price, 2),
        }
        if price < rrp:
            red.append(row)
        elif price < floor:
            yellow.append(row)
    red.sort(key=lambda x: (-x["gap_to_rrp"], x["offer_id"]))
    yellow.sort(key=lambda x: (-x["gap_to_floor"], x["offer_id"]))
    stats["unverified_offer_ids"].sort()
    stats["rrp_missing_offer_ids"].sort()
    return red, yellow, stats


def fingerprint(red: list[dict], yellow: list[dict], stats: dict) -> str:
    canonical = {
        "red": [[v["offer_id"], round(v["customer_price"], 2), round(v["rrp"], 2)] for v in red],
        "yellow": [[v["offer_id"], round(v["customer_price"], 2), round(v["floor"], 2)] for v in yellow],
        "price_not_verified": stats.get("unverified_offer_ids", []),
        "rrp_missing": stats.get("rrp_missing_offer_ids", []),
    }
    return hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def format_message(red: list[dict], yellow: list[dict], stats: dict) -> str:
    lines = [f"Ozon — контроль цены"]
    lines.append(f"🔴 Ниже РРЦ: {len(red)}")
    lines.append(f"🟡 От РРЦ до РРЦ+5%: {len(yellow)}")
    lines.append(
        f"Проверено: {stats['checked']} SKU. "
        f"Цена не подтверждена после повторов: {stats['price_not_verified']}."
    )
    if red:
        lines.append("")
        lines.append("🔴 Критично — ниже РРЦ")
        for v in red:
            p = f"{v['customer_price']:,.0f}".replace(",", " ")
            rrp = f"{v['rrp']:,.0f}".replace(",", " ")
            gap = f"{v['gap_to_rrp']:,.0f}".replace(",", " ")
            lines.append(f"• {v['offer_id']}: покупатель {p} ₽, РРЦ {rrp} ₽ → ниже на {gap} ₽")
    if yellow:
        lines.append("")
        lines.append("🟡 Жёлтая зона — РРЦ ≤ цена < РРЦ+5%")
        for v in yellow:
            p = f"{v['customer_price']:,.0f}".replace(",", " ")
            floor = f"{v['floor']:,.0f}".replace(",", " ")
            gap = f"{v['gap_to_floor']:,.0f}".replace(",", " ")
            lines.append(f"• {v['offer_id']}: покупатель {p} ₽, зелёная зона от {floor} ₽ → не хватает {gap} ₽")
    if stats["price_not_verified"]:
        lines.append("")
        lines.append("⚠️ Не удалось подтвердить цену покупателя после повторных запросов:")
        for offer_id in stats["unverified_offer_ids"][:40]:
            lines.append(f"• {offer_id}")
        if len(stats["unverified_offer_ids"]) > 40:
            lines.append(f"… ещё {len(stats['unverified_offer_ids']) - 40} SKU")
    if stats["rrp_missing"]:
        lines.append("")
        lines.append(f"⚠️ Без сопоставленного РРЦ: {stats['rrp_missing']} SKU")
        for offer_id in stats["rrp_missing_offer_ids"][:40]:
            lines.append(f"• {offer_id}")
        if len(stats["rrp_missing_offer_ids"]) > 40:
            lines.append(f"… ещё {len(stats['rrp_missing_offer_ids']) - 40} SKU")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", default="artifacts/ozon_catalog.json")
    ap.add_argument("--state", default=".state/last_price_alert.json")
    args = ap.parse_args()

    rrp_map = load_rrp_map()
    if not rrp_map:
        print("RRP_MAP_B64 is not configured; price alert skipped.")
        return

    with open(args.snapshot, "r", encoding="utf-8") as f:
        snapshot = json.load(f)

    red, yellow, stats = collect_violations(snapshot, rrp_map)
    if stats.get("rrp_missing_offer_ids"):
        print("RRP missing offer_ids: " + json.dumps(stats["rrp_missing_offer_ids"], ensure_ascii=False))
    fp = fingerprint(red, yellow, stats)
    state_path = Path(args.state)
    previous = {}
    if state_path.exists():
        try:
            previous = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            previous = {}

    previous_fp = previous.get("fingerprint")
    previous_count = int(previous.get("count") or 0)

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({"fingerprint": fp, "count": len(red) + len(yellow)}, ensure_ascii=False),
        encoding="utf-8",
    )

    if fp == previous_fp:
        print(f"Price status unchanged (red={len(red)}, yellow={len(yellow)}); Telegram skipped.")
        return

    if not red and not yellow and not stats["price_not_verified"] and not stats["rrp_missing"]:
        if previous_count > 0:
            send_telegram("✅ Ozon — нарушения РРЦ устранены. Сейчас активных нарушений по обычным товарам нет.")
        else:
            print("No violations; Telegram skipped.")
        return

    msg = format_message(red, yellow, stats)
    send_telegram(msg)
    print(f"Sent changed price status: red={len(red)}, yellow={len(yellow)}.")


if __name__ == "__main__":
    main()
