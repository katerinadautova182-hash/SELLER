"""Monthly realized Ozon profitability by SKU.

Inputs:
- realized SKU finance from finance_by_sku.py;
- delivered/returned units from /v1/analytics/data;
- private purchase-cost map from PURCHASE_COST_MAP_B64.

Commercial outputs are intended for private Telegram delivery only.
Do not upload them to public GitHub artifacts.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

from ozon_export.client import OzonClient
from .business_rules import is_clearance_sku
from .catalog import normalize_sku
from .quantity_analytics import fetch_units_by_sku
from .telegram import send_telegram, send_telegram_document
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
        return "LOSS"
    if margin_pct < 10:
        return "LOW"
    if margin_pct < 20:
        return "MEDIUM"
    return "GOOD"


def build_profitability(finance_rows: list[dict], qty_map: dict[str, dict],
                        cost_map: dict[str, dict], *, allow_unverified: bool = True):
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

        rows.append({
            **base,
            **qty_fields,
            "purchase_cost_rub": round(float(purchase_cost), 2),
            "landed_unit_cost_rub": round(full_unit_cost, 2),
            "cogs_rub": round(cogs, 2),
            "contribution_profit_rub": round(contribution, 2),
            "contribution_profit_per_net_unit_rub": (
                round(profit_per_net_unit, 2) if profit_per_net_unit is not None else None
            ),
            "contribution_margin_percent": round(margin_pct, 2),
            "status": classify_margin(contribution, margin_pct),
        })

    rows.sort(key=lambda x: (
        {"LOSS": 0, "LOW": 1, "MEDIUM": 2, "GOOD": 3}.get(x.get("status"), 9),
        float(x.get("contribution_profit_rub") or 0),
    ))
    return rows


def build_summary(rows: list[dict], non_item_total: float) -> dict:
    calculated = [r for r in rows if r.get("status") in {"LOSS","LOW","MEDIUM","GOOD"}]
    sku_contribution = sum(float(r.get("contribution_profit_rub") or 0) for r in calculated)
    cogs = sum(float(r.get("cogs_rub") or 0) for r in calculated)
    return {
        "sku_total": len(rows),
        "calculated": len(calculated),
        "loss": sum(r.get("status") == "LOSS" for r in calculated),
        "low": sum(r.get("status") == "LOW" for r in calculated),
        "medium": sum(r.get("status") == "MEDIUM" for r in calculated),
        "good": sum(r.get("status") == "GOOD" for r in calculated),
        "missing_cost": sum(r.get("status") == "MISSING_COST" for r in rows),
        "missing_qty": sum(r.get("status") == "MISSING_QTY" for r in rows),
        "unverified_cost": sum(r.get("status") == "UNVERIFIED_COST" for r in rows),
        "seller_price_rub": round(sum(float(r.get("seller_price_rub") or 0) for r in calculated), 2),
        "direct_finance_net_rub": round(sum(float(r.get("direct_finance_net_rub") or 0) for r in calculated), 2),
        "cogs_rub": round(cogs, 2),
        "sku_contribution_profit_rub": round(sku_contribution, 2),
        "non_item_total_rub": round(non_item_total, 2),
        "platform_contribution_after_non_item_rub": round(sku_contribution + non_item_total, 2),
    }


def private_message(summary: dict, date_from: str, date_to: str, rows: list[dict]) -> str:
    def rub(v):
        return f"{float(v):,.0f}".replace(",", " ") + " ₽"

    lines = [
        f"Ozon — маржинальность {date_from}–{date_to}",
        "",
        f"Рассчитано SKU: {summary['calculated']} из {summary['sku_total']}",
        f"Убыток: {summary['loss']} | 0–10%: {summary['low']} | 10–20%: {summary['medium']} | ≥20%: {summary['good']}",
        f"Без закупки: {summary['missing_cost']} | без количества: {summary['missing_qty']}",
        "",
        f"Прямой результат Ozon по SKU: {rub(summary['direct_finance_net_rub'])}",
        f"Себестоимость (закуп × 1,35): {rub(summary['cogs_rub'])}",
        f"Маржинальная прибыль SKU: {rub(summary['sku_contribution_profit_rub'])}",
        f"Расходы без SKU: {rub(summary['non_item_total_rub'])}",
        f"После расходов без SKU: {rub(summary['platform_contribution_after_non_item_rub'])}",
    ]

    losses = [r for r in rows if r.get("status") == "LOSS"][:15]
    if losses:
        lines += ["", "Убыточные SKU:"]
        for r in losses:
            lines.append(
                f"• {r['offer_id']}: {rub(r['contribution_profit_rub'])} "
                f"({r['contribution_margin_percent']:.1f}%)"
            )
    return "\n".join(lines)


def write_csv(path: Path, rows: list[dict]):
    fields = [
        "ozon_sku","offer_id","name","delivered_units","returned_units","net_units",
        "seller_price_rub","sale_commission_rub","delivery_rub","item_fees_rub",
        "item_compensation_rub","direct_finance_net_rub",
        "purchase_cost_rub","landed_unit_cost_rub","cogs_rub",
        "contribution_profit_rub","contribution_profit_per_net_unit_rub",
        "contribution_margin_percent","purchase_cost_source","purchase_cost_verified","status",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w",encoding="utf-8-sig",newline="") as fh:
        w=csv.DictWriter(fh,fieldnames=fields,delimiter=";",extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--date-from",required=True)
    ap.add_argument("--date-to",required=True)
    ap.add_argument("--finance",default="artifacts/finance/finance_by_sku.json")
    ap.add_argument("--finance-summary",default="artifacts/finance/finance_summary.json")
    ap.add_argument("--output",default="artifacts/private/ozon_profitability.csv")
    args=ap.parse_args()

    finance_rows=json.loads(Path(args.finance).read_text(encoding="utf-8"))
    finance_summary=json.loads(Path(args.finance_summary).read_text(encoding="utf-8"))
    cost_map=load_cost_map()
    if not cost_map:
        raise RuntimeError("PURCHASE_COST_MAP_B64 is empty")

    client=OzonClient()
    qty_map=fetch_units_by_sku(client,args.date_from,args.date_to)
    allow_unverified=os.getenv("ALLOW_LEGACY_COSTS","").upper() in {"YES","TRUE","1"}

    rows=build_profitability(
        finance_rows,qty_map,cost_map,allow_unverified=allow_unverified
    )
    summary=build_summary(rows,float(finance_summary.get("non_item_total_rub") or 0))

    out=Path(args.output)
    write_csv(out,rows)
    summary_path=out.with_name("ozon_profitability_summary.json")
    summary_path.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")

    # Never print commercial amounts into public GitHub logs.
    print(
        "Profitability calculated: "
        f"sku={summary['calculated']}/{summary['sku_total']} "
        f"loss={summary['loss']} low={summary['low']} medium={summary['medium']} "
        f"good={summary['good']} missing_cost={summary['missing_cost']} "
        f"missing_qty={summary['missing_qty']}"
    )

    send_telegram(private_message(summary,args.date_from,args.date_to,rows))
    send_telegram_document(
        out,
        caption=f"Ozon маржинальность {args.date_from}–{args.date_to}. Себестоимость = закупка × 1,35.",
    )
    return 0


if __name__=="__main__":
    raise SystemExit(main())
