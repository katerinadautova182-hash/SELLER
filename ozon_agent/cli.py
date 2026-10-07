"""CLI for read-only Ozon margin-agent diagnostics."""
from __future__ import annotations

import argparse
import json

from ozon_export.client import OzonClient
from ozon_export.products import fetch_product_ids
from .catalog import Catalog
from .loaders import load_alias_csv, load_rrp_csv
from .report import build_readiness_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Ozon margin-agent readiness check")
    parser.add_argument("--aliases", required=True, help="CSV: sku_variant,sku_canonical,purchase_cost,source")
    parser.add_argument("--rrp", required=True, help="CSV: Бренд,Артикул,Наименование,РРЦ, руб.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    catalog = Catalog.from_rows(load_alias_csv(args.aliases), load_rrp_csv(args.rrp))
    client = OzonClient()
    id_items = fetch_product_ids(client)
    product_ids = [it.get("product_id") for it in id_items if it.get("product_id")]
    # Product list contains product_id only; use product info to recover offer_id.
    offer_ids: list[str] = []
    for start in range(0, len(product_ids), 100):
        chunk = product_ids[start:start + 100]
        data = client.post("/v3/product/info/list", {"product_id": chunk})
        for item in data.get("items", []):
            offer_id = item.get("offer_id")
            if offer_id:
                offer_ids.append(str(offer_id))

    report = build_readiness_report(offer_ids, catalog)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        s = report["summary"]
        print(f"Всего SKU Ozon: {s['total']}")
        print(f"Готовы: {s['ready']}")
        print(f"Нет закупки: {s['missing_cost']}")
        print(f"Нет РРЦ: {s['missing_rrp']}")
        print(f"Нет обоих данных: {s['missing_both']}")
        print(f"Не сопоставлены: {s['unmatched']}")
        if report["issues"]:
            print("\nПроблемные SKU:")
            for row in report["issues"][:100]:
                print(f"- {row['offer_id']}: {row['status']} — {row['issue'] or ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
