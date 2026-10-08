"""Build realized Ozon finance reports by SKU for a date range.

This layer intentionally contains NO purchase cost and NO profitability model.
Its only job is to turn Ozon finance/accrual facts into auditable files that can
be reconciled with the seller cabinet before cost/margin is added.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from .finance_accruals import (
    fetch_accrual_types,
    fetch_accruals_for_day,
    aggregate_sku_finance,
    aggregate_non_item_finance,
)


def date_range(date_from: str, date_to: str):
    start = datetime.strptime(date_from, "%Y-%m-%d").date()
    end = datetime.strptime(date_to, "%Y-%m-%d").date()
    if end < start:
        raise ValueError("date_to must be >= date_from")
    cur = start
    while cur <= end:
        yield cur.isoformat()
        cur += timedelta(days=1)


def build_catalog_sku_map(snapshot: dict) -> dict[str, dict]:
    out = {}
    for item in snapshot.get("items", []):
        offer_id = str(item.get("offer_id") or "").strip()
        for sku in (item.get("all_skus") or item.get("customer_price_skus") or []):
            out[str(sku)] = {
                "offer_id": offer_id,
                "name": item.get("name") or "",
                "eligibility": item.get("eligibility") or "",
            }
    return out


def combine_period(days: list[tuple[str, list[dict]]], type_map: dict[int, dict], sku_map: dict[str, dict]):
    sku_totals = defaultdict(lambda: {
        "seller_price_rub": 0.0,
        "sale_amount_rub": 0.0,
        "sale_commission_rub": 0.0,
        "delivery_rub": 0.0,
        "item_fees_rub": 0.0,
        "item_compensation_rub": 0.0,
        "direct_finance_net_rub": 0.0,
        "operations": 0,
        "days_with_activity": 0,
    })
    non_item = defaultdict(lambda: {
        "type_id": None,
        "type_name": "",
        "type_description": "",
        "category": "UNMAPPED",
        "amount_rub": 0.0,
        "operations": 0,
    })
    unmapped_type_ids = set()

    for day, accruals in days:
        daily_sku = aggregate_sku_finance(accruals)
        for sku, row in daily_sku.items():
            target = sku_totals[sku]
            for key in (
                "seller_price_rub","sale_amount_rub","sale_commission_rub",
                "delivery_rub","item_fees_rub","item_compensation_rub",
                "direct_finance_net_rub",
            ):
                target[key] += float(row.get(key) or 0)
            target["operations"] += int(row.get("operations") or 0)
            target["days_with_activity"] += 1

        daily_non = aggregate_non_item_finance(accruals, type_map)
        for type_id, row in daily_non.items():
            target = non_item[type_id]
            target["type_id"] = type_id
            target["type_name"] = row.get("type_name") or ""
            target["type_description"] = row.get("type_description") or ""
            target["category"] = row.get("category") or "UNMAPPED"
            target["amount_rub"] += float(row.get("amount_rub") or 0)
            target["operations"] += int(row.get("operations") or 0)
            if target["category"] == "UNMAPPED":
                unmapped_type_ids.add(type_id)

    sku_rows = []
    for sku, row in sku_totals.items():
        cat = sku_map.get(sku, {})
        seller_price = round(row["seller_price_rub"], 2)
        net = round(row["direct_finance_net_rub"], 2)
        direct_costs = round(seller_price - net, 2)
        sku_rows.append({
            "ozon_sku": sku,
            "offer_id": cat.get("offer_id", ""),
            "name": cat.get("name", ""),
            "seller_price_rub": seller_price,
            "sale_amount_rub": round(row["sale_amount_rub"], 2),
            "sale_commission_rub": round(row["sale_commission_rub"], 2),
            "delivery_rub": round(row["delivery_rub"], 2),
            "item_fees_rub": round(row["item_fees_rub"], 2),
            "item_compensation_rub": round(row["item_compensation_rub"], 2),
            "direct_costs_rub": direct_costs,
            "direct_finance_net_rub": net,
            "operations": row["operations"],
            "days_with_activity": row["days_with_activity"],
            "sku_mapping_status": "MAPPED" if cat.get("offer_id") else "UNMAPPED_SKU",
        })
    sku_rows.sort(key=lambda x: (-abs(x["seller_price_rub"]), x["ozon_sku"]))

    non_item_rows = []
    for _, row in non_item.items():
        non_item_rows.append({
            **row,
            "amount_rub": round(row["amount_rub"], 2),
        })
    non_item_rows.sort(key=lambda x: (-abs(x["amount_rub"]), x["type_id"] or -1))

    summary = {
        "sku_count": len(sku_rows),
        "mapped_sku_count": sum(1 for x in sku_rows if x["sku_mapping_status"] == "MAPPED"),
        "unmapped_sku_count": sum(1 for x in sku_rows if x["sku_mapping_status"] != "MAPPED"),
        "seller_price_rub": round(sum(x["seller_price_rub"] for x in sku_rows), 2),
        "sale_commission_rub": round(sum(x["sale_commission_rub"] for x in sku_rows), 2),
        "delivery_rub": round(sum(x["delivery_rub"] for x in sku_rows), 2),
        "item_fees_rub": round(sum(x["item_fees_rub"] for x in sku_rows), 2),
        "direct_finance_net_rub": round(sum(x["direct_finance_net_rub"] for x in sku_rows), 2),
        "non_item_total_rub": round(sum(x["amount_rub"] for x in non_item_rows), 2),
        "unmapped_type_ids": sorted(unmapped_type_ids),
    }
    return sku_rows, non_item_rows, summary


def write_csv(path: Path, rows: list[dict], fields: list[str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, delimiter=";", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    from ozon_export.client import OzonClient

    ap = argparse.ArgumentParser()
    ap.add_argument("--date-from", required=True)
    ap.add_argument("--date-to", required=True)
    ap.add_argument("--snapshot", default="artifacts/ozon_catalog.json")
    ap.add_argument("--output-dir", default="artifacts/finance")
    args = ap.parse_args()

    client = OzonClient()
    type_map = fetch_accrual_types(client)

    days = []
    for day in date_range(args.date_from, args.date_to):
        accruals = fetch_accruals_for_day(client, day)
        print(f"{day}: accrual rows={len(accruals)}")
        days.append((day, accruals))

    snapshot_path = Path(args.snapshot)
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8")) if snapshot_path.exists() else {"items": []}
    sku_map = build_catalog_sku_map(snapshot)
    sku_rows, non_item_rows, summary = combine_period(days, type_map, sku_map)
    summary["date_from"] = args.date_from
    summary["date_to"] = args.date_to

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    write_csv(out / "finance_by_sku.csv", sku_rows, [
        "ozon_sku","offer_id","name","seller_price_rub","sale_amount_rub",
        "sale_commission_rub","delivery_rub","item_fees_rub",
        "item_compensation_rub","direct_costs_rub","direct_finance_net_rub",
        "operations","days_with_activity","sku_mapping_status",
    ])
    write_csv(out / "finance_non_item.csv", non_item_rows, [
        "type_id","type_name","type_description","category","amount_rub","operations",
    ])
    (out / "finance_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "finance_by_sku.json").write_text(
        json.dumps(sku_rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "finance_non_item.json").write_text(
        json.dumps(non_item_rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(
        "Finance by SKU: "
        f"sku={summary['sku_count']} mapped={summary['mapped_sku_count']} "
        f"unmapped_sku={summary['unmapped_sku_count']} "
        f"seller_price={summary['seller_price_rub']:.2f} "
        f"direct_net={summary['direct_finance_net_rub']:.2f} "
        f"non_item={summary['non_item_total_rub']:.2f} "
        f"unmapped_types={summary['unmapped_type_ids']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
