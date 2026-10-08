"""Actionable live Ozon margin recommendations.

Uses verified seller_promo_price from /v1/product/prices/details plus current
commission/logistics/acquiring and private purchase cost x 1.35.

Zones:
- RED: margin < 0%; recommend minimum price to reach break-even.
- YELLOW: 0% <= margin < 20%; recommend minimum price to reach 20%.
- GREEN: margin >= 20%; no action.

No CSV or commercial artifact is uploaded. Telegram receives only action lines.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path

from .telegram import send_telegram
from .unit_economics_live import load_cost_map, resolve_purchase_cost

LANDED_FACTOR = 1.35


def classify(profit: float, margin_pct: float) -> str:
    if profit < 0:
        return "RED"
    if margin_pct < 20:
        return "YELLOW"
    return "GREEN"


def _required_effective_price(*, landed: float, commission_percent: float,
                              logistics: float, acquiring: float,
                              target_margin_percent: float) -> float | None:
    c = max(0.0, float(commission_percent or 0) / 100.0)
    t = float(target_margin_percent or 0) / 100.0
    denom = 1.0 - c - t
    if denom <= 0:
        return None
    return (float(landed) + float(logistics or 0) + float(acquiring or 0)) / denom


def build_recommendations(snapshot: dict, cost_map: dict[str, dict]) -> list[dict]:
    rows = []
    for item in snapshot.get("items", []):
        if item.get("eligibility") != "ELIGIBLE":
            continue

        offer_id = str(item.get("offer_id") or "").strip()
        if not offer_id:
            continue

        effective = float(item.get("seller_promo_price") or 0)
        base_price = float(item.get("current_price") or 0)
        customer_price = float(item.get("customer_price") or 0)
        if effective <= 0:
            rows.append({"offer_id": offer_id, "status": "PRICE_NOT_VERIFIED"})
            continue

        purchase_cost, source, verified = resolve_purchase_cost(offer_id, cost_map)
        if purchase_cost is None:
            rows.append({"offer_id": offer_id, "status": "MISSING_COST"})
            continue

        landed = float(purchase_cost) * LANDED_FACTOR
        commission_percent = float(item.get("commission_percent") or 0)
        logistics = float(item.get("logistics_rub") or 0)
        acquiring = float(item.get("acquiring_rub") or 0)

        commission = effective * commission_percent / 100.0
        profit = effective - commission - logistics - acquiring - landed
        margin_pct = profit / effective * 100.0
        status = classify(profit, margin_pct)

        row = {
            "offer_id": offer_id,
            "name": item.get("name") or "",
            "customer_price": customer_price,
            "base_price": base_price,
            "seller_promo_price": effective,
            "purchase_cost": float(purchase_cost),
            "landed_cost": landed,
            "commission_percent": commission_percent,
            "logistics_rub": logistics,
            "acquiring_rub": acquiring,
            "profit_rub": profit,
            "margin_percent": margin_pct,
            "status": status,
            "purchase_cost_source": source,
            "purchase_cost_verified": bool(verified),
        }

        if status in ("RED", "YELLOW"):
            target_margin = 0.0 if status == "RED" else 20.0
            required_effective = _required_effective_price(
                landed=landed,
                commission_percent=commission_percent,
                logistics=logistics,
                acquiring=acquiring,
                target_margin_percent=target_margin,
            )
            if required_effective:
                ratio = required_effective / effective
                target_base = base_price * ratio if base_price > 0 else required_effective
                target_customer = customer_price * ratio if customer_price > 0 else 0

                # Always round recommendations upward so the requested threshold is met.
                row.update({
                    "target_margin_percent": target_margin,
                    "target_seller_promo_price": math.ceil(required_effective),
                    "target_base_price": math.ceil(target_base),
                    "target_customer_price_estimate": math.ceil(target_customer) if target_customer > 0 else None,
                    "base_price_increase_rub": math.ceil(max(0.0, target_base - base_price)),
                    "base_price_increase_percent": max(0.0, (ratio - 1.0) * 100.0),
                })

        rows.append(row)

    rows.sort(key=lambda r: (
        {"RED": 0, "YELLOW": 1, "GREEN": 2}.get(r.get("status"), 9),
        float(r.get("margin_percent") or 0),
    ))
    return rows


def _rub(value) -> str:
    return f"{float(value):,.0f}".replace(",", " ") + " ₽"


def messages(rows: list[dict]) -> list[str]:
    red = [r for r in rows if r.get("status") == "RED" and r.get("target_base_price")]
    yellow = [r for r in rows if r.get("status") == "YELLOW" and r.get("target_base_price")]
    missing = [r for r in rows if r.get("status") == "MISSING_COST"]
    unverified = [r for r in rows if r.get("status") == "PRICE_NOT_VERIFIED"]

    blocks = [[
        "Ozon — что поднять по цене сейчас",
        f"🔴 красных: {len(red)} | 🟡 жёлтых: {len(yellow)}",
    ]]

    if red:
        b = ["🔴 Чтобы выйти из убытка:"]
        for r in red:
            b.append(
                f"• {r['offer_id']}: база {_rub(r['base_price'])} → {_rub(r['target_base_price'])} "
                f"(+{_rub(r['base_price_increase_rub'])}, +{r['base_price_increase_percent']:.1f}%)"
            )
        blocks.append(b)

    if yellow:
        b = ["🟡 Чтобы выйти на маржу ≥20%:"]
        for r in yellow:
            b.append(
                f"• {r['offer_id']}: база {_rub(r['base_price'])} → {_rub(r['target_base_price'])} "
                f"(+{_rub(r['base_price_increase_rub'])}, +{r['base_price_increase_percent']:.1f}%)"
            )
        blocks.append(b)

    if missing:
        blocks.append([f"⚠️ Без закупки: {len(missing)} SKU — цену по марже не рекомендую."])
    if unverified:
        blocks.append([f"⚠️ Без подтверждённой seller promo price: {len(unverified)} SKU."])

    result=[]
    current=""
    for block in blocks:
        for line in block:
            candidate = line if not current else current + "\n" + line
            if len(candidate) > 3900:
                result.append(current)
                current=line
            else:
                current=candidate
        current += "\n"
    if current.strip():
        result.append(current.strip())
    return result


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--snapshot", default="artifacts/ozon_catalog.json")
    args=ap.parse_args()

    snapshot=json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    cost_map=load_cost_map()
    if not cost_map:
        raise RuntimeError("PURCHASE_COST_MAP_B64 is empty")

    rows=build_recommendations(snapshot,cost_map)
    red=sum(r.get("status")=="RED" for r in rows)
    yellow=sum(r.get("status")=="YELLOW" for r in rows)
    green=sum(r.get("status")=="GREEN" for r in rows)
    missing=sum(r.get("status")=="MISSING_COST" for r in rows)
    unverified=sum(r.get("status")=="PRICE_NOT_VERIFIED" for r in rows)

    print(
        f"Live margin recommendations: red={red} yellow={yellow} green={green} "
        f"missing_cost={missing} price_not_verified={unverified}"
    )

    for msg in messages(rows):
        send_telegram(msg)

    return 0


if __name__=="__main__":
    raise SystemExit(main())
