"""Telegram price control for Ozon.

Signals:
- RED: customer price below RRP.
- YELLOW: customer price between RRP and RRP+5% (auto-managed silently).
- WHITE: anomalously high customer price versus this SKU's own recent history.

Clearance and out-of-stock SKUs are ignored.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from math import ceil
from pathlib import Path
from statistics import median

from .business_rules import is_clearance_sku, canonical_sku, manual_rrp, box_rule
from .catalog import normalize_sku
from .telegram import send_telegram

RRP_FACTOR = 1.05
PRICE_HISTORY_LIMIT = 28       # 7 days at the current 6-hour schedule
PRICE_HISTORY_MIN_SAMPLES = 4  # about one day before WHITE can trigger


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


def _high_price_rule(baseline: float) -> tuple[float, float]:
    """Return (relative threshold, absolute threshold) for WHITE signal."""
    if baseline <= 1500:
        return 0.35, 300.0
    if baseline <= 5000:
        return 0.30, 500.0
    if baseline <= 15000:
        return 0.25, 1000.0
    return 0.20, 2000.0


def detect_high_price_anomalies(snapshot: dict, price_history: dict[str, list[float]]) -> list[dict]:
    white = []
    for item in snapshot.get("items", []):
        offer_id = str(item.get("offer_id") or "").strip()
        if not offer_id:
            continue
        if item.get("eligibility") in ("CLEARANCE", "OUT_OF_STOCK") or is_clearance_sku(offer_id):
            continue
        price = item.get("customer_price")
        if not item.get("customer_price_verified") or price in (None, 0, 0.0):
            continue

        history = [
            float(v) for v in (price_history.get(offer_id) or [])
            if v not in (None, 0, 0.0)
        ][-PRICE_HISTORY_LIMIT:]
        if len(history) < PRICE_HISTORY_MIN_SAMPLES:
            continue

        baseline = float(median(history))
        current = float(price)
        rel_threshold, abs_threshold = _high_price_rule(baseline)
        delta = current - baseline
        delta_pct = (delta / baseline * 100.0) if baseline > 0 else 0.0

        if current > baseline * (1.0 + rel_threshold) and delta > abs_threshold:
            white.append({
                "offer_id": offer_id,
                "customer_price": current,
                "baseline_price": round(baseline, 2),
                "delta": round(delta, 2),
                "delta_pct": round(delta_pct, 1),
                "history_samples": len(history),
            })

    white.sort(key=lambda x: (-x["delta_pct"], -x["delta"], x["offer_id"]))
    return white


def update_price_history(snapshot: dict, price_history: dict[str, list[float]]) -> dict[str, list[float]]:
    updated = {
        str(k): [float(v) for v in (vals or []) if v not in (None, 0, 0.0)][-PRICE_HISTORY_LIMIT:]
        for k, vals in (price_history or {}).items()
    }
    for item in snapshot.get("items", []):
        offer_id = str(item.get("offer_id") or "").strip()
        if not offer_id:
            continue
        if item.get("eligibility") in ("CLEARANCE", "OUT_OF_STOCK") or is_clearance_sku(offer_id):
            continue
        price = item.get("customer_price")
        if not item.get("customer_price_verified") or price in (None, 0, 0.0):
            continue
        values = updated.setdefault(offer_id, [])
        values.append(float(price))
        updated[offer_id] = values[-PRICE_HISTORY_LIMIT:]
    return updated


def fingerprint(red: list[dict], yellow: list[dict], stats: dict, white: list[dict] | None = None) -> str:
    white = white or []
    canonical = {
        "red": [[v["offer_id"], round(v["customer_price"], 2), round(v["rrp"], 2)] for v in red],
        "white": [
            [v["offer_id"], round(v["customer_price"], 2), round(v["baseline_price"], 2)]
            for v in white
        ],
    }
    return hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def format_message(red: list[dict], yellow: list[dict], stats: dict, white: list[dict] | None = None) -> str:
    """Telegram report: RED RRP violations + WHITE high-price anomalies."""
    white = white or []
    lines = [
        "Ozon — контроль цен",
        f"🔴 Ниже РРЦ: {len(red)}",
        f"🟡 Осталось после автокоррекции: {len(yellow)}",
        f"⚪ Аномально высокая цена: {len(white)}",
    ]

    if red:
        lines.append("")
        lines.append("🔴 Ниже РРЦ")
        for v in red:
            p = f"{v['customer_price']:,.0f}".replace(",", " ")
            rrp = f"{v['rrp']:,.0f}".replace(",", " ")
            gap = f"{v['gap_to_rrp']:,.0f}".replace(",", " ")
            lines.append(f"• {v['offer_id']}: покупатель {p} ₽, РРЦ {rrp} ₽ → ниже на {gap} ₽")

    if yellow:
        lines.append("")
        lines.append("🟡 Осталось после автокоррекции")
        for v in yellow:
            p = f"{v['customer_price']:,.0f}".replace(",", " ")
            floor = f"{v['floor']:,.0f}".replace(",", " ")
            gap = f"{v['gap_to_floor']:,.0f}".replace(",", " ")
            lines.append(
                f"• {v['offer_id']}: покупатель {p} ₽, минимум {floor} ₽ → "
                f"не хватает {gap} ₽"
            )

    if white:
        lines.append("")
        lines.append("⚪ Аномально высокая цена")
        for v in white:
            p = f"{v['customer_price']:,.0f}".replace(",", " ")
            base = f"{v['baseline_price']:,.0f}".replace(",", " ")
            delta = f"{v['delta']:,.0f}".replace(",", " ")
            lines.append(
                f"• {v['offer_id']}: {p} ₽; обычно ≈ {base} ₽ → "
                f"+{delta} ₽ / +{v['delta_pct']:.1f}%"
            )

    if not red and not white:
        lines.append("")
        lines.append("Отклонений нет.")

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

    state_path = Path(args.state)
    previous = {}
    if state_path.exists():
        try:
            previous = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            previous = {}

    previous_history = previous.get("price_history") or {}

    red, yellow, stats = collect_violations(snapshot, rrp_map)
    white = detect_high_price_anomalies(snapshot, previous_history)
    fp = fingerprint(red, yellow, stats, white)

    updated_history = update_price_history(snapshot, previous_history)

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({
            "fingerprint": fp,
            "count": len(red) + len(yellow) + len(white),
            "price_history": updated_history,
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    if stats.get("rrp_missing_offer_ids"):
        print("RRP missing offer_ids: " + json.dumps(stats["rrp_missing_offer_ids"], ensure_ascii=False))

    previous_fp = previous.get("fingerprint")
    scheduled_report = os.getenv("GITHUB_EVENT_NAME", "").strip() == "schedule"
    force_report = os.getenv("FORCE_RED_REPORT", "").strip().upper() == "YES"

    if not scheduled_report and not force_report and fp == previous_fp:
        print(f"Price status unchanged (red={len(red)}; white={len(white)}); Telegram skipped.")
        return

    msg = format_message(red, yellow, stats, white)
    send_telegram(msg)
    print(
        f"Sent price report: red={len(red)}; white={len(white)}; "
        f"yellow={len(yellow)} auto-managed silently."
    )


if __name__ == "__main__":
    main()
