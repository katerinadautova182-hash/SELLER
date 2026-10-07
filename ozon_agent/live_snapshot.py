"""Read-only live Ozon catalog snapshot for the margin agent."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ozon_export.client import OzonClient
from ozon_export.products import fetch_product_ids, fetch_products_info
from .pricing import fetch_price_rows, normalize_price_item


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts/ozon_catalog.json")
    args = parser.parse_args()

    client = OzonClient()
    id_items = fetch_product_ids(client)
    product_ids = [int(x["product_id"]) for x in id_items if x.get("product_id")]
    info = fetch_products_info(client, product_ids)

    names = {}
    offers = {}
    for item in info:
        pid = item.get("id", item.get("product_id"))
        if pid is None:
            continue
        names[int(pid)] = item.get("name", "")
        offers[int(pid)] = item.get("offer_id", "")

    price_rows = fetch_price_rows(client, product_ids)
    rows = []
    for raw in price_rows:
        row = normalize_price_item(raw)
        pid = row.get("product_id")
        if pid is not None:
            row["name"] = names.get(int(pid), "")
            row["offer_id"] = row.get("offer_id") or offers.get(int(pid), "")
        rows.append(row)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"count": len(rows), "items": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Saved {len(rows)} Ozon items to {out}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
