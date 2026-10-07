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

from .business_rules import is_clearance_sku
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


def collect_violations(snapshot: dict, rrp_map: dict[str, float]) -> tuple[list[dict], dict]:
    violations = []
    stats = {
        "total": 0,
        "verified": 0,
        "clearance_ignored": 0,
        "rrp_missing": 0,
        "checked": 0,
    }
    for item in snapshot.get("items", []):
        stats["total"] += 1
        offer_id = str(item.get("offer_id") or "").strip()
        if item.get("customer_price_verified"):
            stats["verified"] += 1
        if is_clearance_sku(offer_id):
            stats["clearance_ignored"] += 1
            continue
        rrp = rrp_map.get(normalize_sku(offer_id))
        if rrp is None:
            stats["rrp_missing"] += 1
            continue
        price = item.get("customer_price")
        if not item.get("customer_price_verified") or price in (None, 0, 0.0):
            continue
        stats["checked"] += 1
        floor = float(ceil(rrp * RRP_FACTOR))
        price = float(price)
        if price < floor:
            violations.append({
                "offer_id": offer_id,
                "customer_price": price,
                "rrp": rrp,
                "floor": floor,
                "gap": round(floor - price, 2),
            })
    violations.sort(key=lambda x: (-x["gap"], x["offer_id"]))
    return violations, stats


def fingerprint(violations: list[dict]) -> str:
    canonical = [
        [v["offer_id"], round(v["customer_price"], 2), round(v["floor"], 2)]
        for v in violations
    ]
    return hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def format_message(violations: list[dict], stats: dict) -> str:
    lines = [f"Ozon — нарушения цены: {len(violations)}"]
    lines.append(f"Проверено по РРЦ: {stats['checked']} SKU. Уценка УЦ исключена: {stats['clearance_ignored']}.")
    lines.append("")
    for v in violations:
        p = f"{v['customer_price']:,.0f}".replace(",", " ")
        floor = f"{v['floor']:,.0f}".replace(",", " ")
        gap = f"{v['gap']:,.0f}".replace(",", " ")
        lines.append(f"🔴 {v['offer_id']}: покупатель {p} ₽, минимум {floor} ₽ → поднять минимум на {gap} ₽")
    if stats["rrp_missing"]:
        lines.append("")
        lines.append(f"⚠️ Без сопоставленного РРЦ: {stats['rrp_missing']} SKU.")
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

    violations, stats = collect_violations(snapshot, rrp_map)
    fp = fingerprint(violations)
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
        json.dumps({"fingerprint": fp, "count": len(violations)}, ensure_ascii=False),
        encoding="utf-8",
    )

    if fp == previous_fp:
        print(f"Price violations unchanged ({len(violations)}); Telegram skipped.")
        return

    if not violations:
        if previous_count > 0:
            send_telegram("✅ Ozon — нарушения РРЦ устранены. Сейчас активных нарушений по обычным товарам нет.")
        else:
            print("No violations; Telegram skipped.")
        return

    msg = format_message(violations, stats)
    send_telegram(msg)
    print(f"Sent {len(violations)} changed price violations.")


if __name__ == "__main__":
    main()
