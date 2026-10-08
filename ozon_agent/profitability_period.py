"""Monthly realized Ozon profitability by SKU.

Inputs:
- realized SKU finance from finance_by_sku.py;
- delivered/returned units from /v1/analytics/data;
- private purchase-cost map from PURCHASE_COST_MAP_B64.

No commercial CSV is sent to the user. Telegram receives only actionable
price recommendations for red/yellow profitability zones.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

from ozon_export.client import OzonClient
from .business_rules import is_clearance_sku
from .quantity_analytics import fetch_units_by_sku
from .telegram import send_telegram
from .unit_economics_live import load_cost_map, resolve_purchase_cost

LANDED_COST_FACTOR = 1.35


def _base_offer_for_clearance(offer_id: str) -> str:
    raw = str(offer_id or "").strip()
    upper = raw.upper()
    for suffix in (".УЦ", "-УЦ", "_УЦ", " УЦ"):
        if upper.endswith(suffix):
            return raw[: -len(suffix)].strip()
    return raw


def _resolve_cost(offer_id: str, cost_map: dict[str, dict]):
    cost, source, verified = resolve_purchase_cost(offer_id, cost_map)
    if cost is not None:
        return cost, source, verified
    if is_clearance_sku(offer_id):
        base = _base_offer_for_clearance(offer_id)
        if base and base != offer_id:
            cost, source, verified = resolve_purchase_cost(base, cost_map)
            if cost is not None:
                return cost, f"clearance_base:{source}", verified
    return None, "missing", False


def classify_margin(profit: float, margin_pct: float) -> str:
    if profit < 0:
        return "RED"
    if margin_pct < 20:
        return "YELLOW"
    return "GREEN"


def price_recommendation(row: dict, target_margin_percent: float) -> dict | None:
    sales = float(row.get("seller_price_rub") or 0)
    units = float(row.get("net_units") or 0)
    cogs = float(row.get("cogs_rub") or 0)
    if sales <= 0 or units <= 0:
        return None

    commission = float(row.get("sale_commission_rub") or 0)
    commission_rate = max(0.0, min(0.8, -commission / sales))
    fixed_net = (
        float(row.get("delivery_rub") or 0)
        + float(row.get("item_fees_rub") or 0)
    )
    target = target_margin_percent / 100.0
    denominator = 1.0 - commission_rate - target
    if denominator <= 0:
        return None

    required_total_sales = (cogs - fixed_net) / denominator
    current_avg = sales / units
    required_avg = required_total_sales / units
    increase_rub = max(0.0, required_avg - current_avg)
    increase_pct = increase_rub / current_avg * 100.0 if current_avg > 0 else 0.0

    return {
        "current_avg_seller_price_rub": round(current_avg, 2),
        "target_avg_seller_price_rub": round(required_avg, 2),
        "price_increase_rub": round(increase_rub, 2),
        "price_increase_percent": round(increase_pct, 1),
        "target_margin_percent": target_margin_percent,
    }


def build_profitability(
    finance_rows: list[dict],
    qty_map: dict[str, dict],
    cost_map: dict[str, dict],
    *,
    allow_unverified: bool = True,
):
    rows = []
    for fin in finance_rows:
        sku = str(fin.get("ozon_sku") or "").strip()
        offer_id = str(fin.get("offer_id") or "").strip()
        qty = qty_map.get(sku)
        base = {
            "ozon_sku": sku,
            "offer_id": offer_id,
            "name": fin.get("name") or "",
            "seller_price_rub": float(fin.get("seller_price_rub") or 0),
            "sale_commission_rub": float(fin.get("sale_commission_rub") or 0),
            "delivery_rub": float(fin.get("delivery_rub") or 0),
            "item_fees_rub": float(fin.get("item_fees_rub") or 0),
            "item_compensation_rub": float(fin.get("item_compensation_rub") or 0),
            "direct_finance_net_rub": float(fin.get("direct_finance_net_rub") or 0),
        }

        if qty is None:
            rows.append({**base, "status": "MISSING_QTY"})
            continue

        delivered = float(qty.get("delivered_units") or 0)
        returned = float(qty.get("returned_units") or 0)
        net_units = float(qty.get("net_units") or 0)

        purchase_cost, cost_source, cost_verified = _resolve_cost(offer_id, cost_map)
        qty_fields = {
            "delivered_units": delivered,
            "returned_units": returned,
            "net_units": net_units,
            "purchase_cost_source": cost_source,
            "purchase_cost_verified": bool(cost_verified),
        }

        if purchase_cost is None:
            rows.append({**base, **qty_fields, "status": "MISSING_COST"})
            continue
        if not cost_verified and not allow_unverified:
            rows.append({**base, **qty_fields, "status": "UNVERIFIED_COST"})
            continue

        full_unit_cost = float(purchase_cost) * LANDED_COST_FACTOR
        cogs = full_unit_cost * net_units
        contribution = base["direct_finance_net_rub"] - cogs
        sales = base["seller_price_rub"]
        margin_pct = contribution / sales * 100.0 if sales > 0 else 0.0
        profit_per_net_unit = contribution / net_units if net_units > 0 else None

        row = {
            **base,
            **qty_fields,
            "purchase_cost_rub": round(float(purchase_cost), 2),
            "landed_unit_cost_rub": round(full_unit_cost, 2),
            "cogs_rub": round(cogs, 2),
            "contribution_profit_rub": round(contribution, 2),
            "contribution_profit_per_net_unit_rub": (
                round(profit_per_net_unit, 2)
                if profit_per_net_unit is not None
                else None
            ),
            "contribution_margin_percent": round(margin_pct, 2),
            "status": classify_margin(contribution, margin_pct),
        }

        target = 0.0 if row["status"] == "RED" else (20.0 if row["status"] == "YELLOW" else None)
        if target is not None:
            rec = price_recommendation(row, target)
            if rec:
                row.update(rec)

        rows.append(row)

    rows.sort(
        key=lambda x: (
            {"RED": 0, "YELLOW": 1, "GREEN": 2}.get(x.get("status"), 9),
            float(x.get("contribution_profit_rub") or 0),
        )
    )
    return rows


def build_summary(rows: list[dict], non_item_total: float) -> dict:
    calculated = [r for r in rows if r.get("status") in {"RED", "YELLOW", "GREEN"}]
    sku_contribution = sum(float(r.get("contribution_profit_rub") or 0) for r in calculated)
    cogs = sum(float(r.get("cogs_rub") or 0) for r in calculated)
    return {
        "sku_total": len(rows),
        "calculated": len(calculated),
        "red": sum(r.get("status") == "RED" for r in calculated),
        "yellow": sum(r.get("status") == "YELLOW" for r in calculated),
        "green": sum(r.get("status") == "GREEN" for r in calculated),
        "missing_cost": sum(r.get("status") == "MISSING_COST" for r in rows),
        "missing_qty": sum(r.get("status") == "MISSING_QTY" for r in rows),
        "seller_price_rub": round(sum(float(r.get("seller_price_rub") or 0) for r in calculated), 2),
        "direct_finance_net_rub": round(sum(float(r.get("direct_finance_net_rub") or 0) for r in calculated), 2),
        "cogs_rub": round(cogs, 2),
        "sku_contribution_profit_rub": round(sku_contribution, 2),
        "non_item_total_rub": round(non_item_total, 2),
        "platform_contribution_after_non_item_rub": round(sku_contribution + non_item_total, 2),
    }


def _rub(v) -> str:
    return f"{float(v):,.0f}".replace(",", " ") + " ₽"


def recommendation_messages(summary: dict, date_from: str, date_to: str, rows: list[dict]) -> list[str]:
    red = [r for r in rows if r.get("status") == "RED" and r.get("target_avg_seller_price_rub")]
    yellow = [r for r in rows if r.get("status") == "YELLOW" and r.get("target_avg_seller_price_rub")]

    header = [
        f"Ozon — рекомендации по цене {date_from}–{date_to}",
        f"🔴 красных: {summary['red']} | 🟡 жёлтых: {summary['yellow']}",
    ]

    blocks = []
    if red:
        b = ["🔴 Поднять, чтобы выйти из убытка:"]
        for r in red:
            b.append(
                f"• {r['offer_id']}: ≈{_rub(r['current_avg_seller_price_rub'])} → "
                f"{_rub(r['target_avg_seller_price_rub'])} "
                f"(+{_rub(r['price_increase_rub'])}, +{r['price_increase_percent']:.1f}%)"
            )
        blocks.append(b)

    if yellow:
        b = ["🟡 Поднять, чтобы выйти на маржу ≥20%:"]
        for r in yellow:
            b.append(
                f"• {r['offer_id']}: ≈{_rub(r['current_avg_seller_price_rub'])} → "
                f"{_rub(r['target_avg_seller_price_rub'])} "
                f"(+{_rub(r['price_increase_rub'])}, +{r['price_increase_percent']:.1f}%)"
            )
        blocks.append(b)

    if summary["missing_cost"]:
        blocks.append([f"⚠️ Без закупки: {summary['missing_cost']} SKU — рекомендацию по ним не считаю."])

    messages = []
    current = "\n".join(header)
    for block in blocks:
        for line in block:
            candidate = current + "\n\n" + line
            if len(candidate) > 3900:
                messages.append(current)
                current = line
            else:
                current = candidate
    if current:
        messages.append(current)
    return messages


def write_csv(path: Path, rows: list[dict]):
    # Internal ephemeral file only; never sent/uploaded.
    fields = [
        "ozon_sku", "offer_id", "name", "delivered_units", "returned_units", "net_units",
        "seller_price_rub", "sale_commission_rub", "delivery_rub", "item_fees_rub",
        "direct_finance_net_rub", "purchase_cost_rub", "landed_unit_cost_rub", "cogs_rub",
        "contribution_profit_rub", "contribution_margin_percent",
        "current_avg_seller_price_rub", "target_avg_seller_price_rub",
        "price_increase_rub", "price_increase_percent", "status",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, delimiter=";", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date-from", required=True)
    ap.add_argument("--date-to", required=True)
    ap.add_argument("--finance", default="artifacts/finance/finance_by_sku.json")
    ap.add_argument("--finance-summary", default="artifacts/finance/finance_summary.json")
    ap.add_argument("--output", default="artifacts/private/ozon_profitability.csv")
    args = ap.parse_args()

    finance_rows = json.loads(Path(args.finance).read_text(encoding="utf-8"))
    finance_summary = json.loads(Path(args.finance_summary).read_text(encoding="utf-8"))
    cost_map = load_cost_map()
    if not cost_map:
        raise RuntimeError("PURCHASE_COST_MAP_B64 is empty")

    client = OzonClient()
    qty_map = fetch_units_by_sku(client, args.date_from, args.date_to)
    allow_unverified = os.getenv("ALLOW_LEGACY_COSTS", "").upper() in {"YES", "TRUE", "1"}

    rows = build_profitability(
        finance_rows, qty_map, cost_map, allow_unverified=allow_unverified
    )
    summary = build_summary(rows, float(finance_summary.get("non_item_total_rub") or 0))

    out = Path(args.output)
    write_csv(out, rows)

    print(
        "Profitability calculated: "
        f"sku={summary['calculated']}/{summary['sku_total']} "
        f"red={summary['red']} yellow={summary['yellow']} green={summary['green']} "
        f"missing_cost={summary['missing_cost']} missing_qty={summary['missing_qty']}"
    )

    for message in recommendation_messages(summary, args.date_from, args.date_to, rows):
        send_telegram(message)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
