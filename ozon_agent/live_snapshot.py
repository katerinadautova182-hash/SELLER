"""Read-only live Ozon catalog snapshot for the margin agent."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ozon_export.client import OzonClient
from ozon_export.products import fetch_product_ids, fetch_products_info
from .pricing import fetch_price_rows, normalize_price_item, fetch_customer_prices


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
    skus_by_product: dict[int, list[str]] = {}
    sku_to_product: dict[str, int] = {}
    for item in info:
        pid = item.get("id", item.get("product_id"))
        if pid is None:
            continue
        pid = int(pid)
        names[pid] = item.get("name", "")
        offers[pid] = item.get("offer_id", "")
        product_skus: list[str] = []
        for source in item.get("sources", []) or []:
            sku = source.get("sku")
            if sku not in (None, "", 0, "0"):
                s = str(sku)
                product_skus.append(s)
                sku_to_product[s] = pid
        for key in ("fbo_sku", "fbs_sku"):
            sku = item.get(key)
            if sku not in (None, "", 0, "0"):
                s = str(sku)
                if s not in product_skus:
                    product_skus.append(s)
                    sku_to_product[s] = pid
        skus_by_product[pid] = product_skus

    customer_price_error = None
    customer_by_sku: dict[str, dict] = {}
    try:
        customer_by_sku = fetch_customer_prices(client, sku_to_product.keys())
    except Exception as exc:
        # Access to /v1/product/prices/details may require Premium Pro.
        # Never fall back to seller price and call it a verified buyer price.
        customer_price_error = f"{type(exc).__name__}: {exc}"

    price_rows = fetch_price_rows(client, product_ids)
    rows = []
    for raw in price_rows:
        row = normalize_price_item(raw)
        pid = row.get("product_id")
        if pid is not None:
            pid = int(pid)
            row["name"] = names.get(pid, "")
            row["offer_id"] = row.get("offer_id") or offers.get(pid, "")
            candidate_prices = [
                customer_by_sku[s]["customer_price"]
                for s in skus_by_product.get(pid, [])
                if s in customer_by_sku
            ]
            # Lowest verified buyer-facing price is the only safe value for RRP control.
            row["customer_price"] = min(candidate_prices) if candidate_prices else None
            row["customer_price_verified"] = bool(candidate_prices)
            row["customer_price_skus"] = [
                s for s in skus_by_product.get(pid, []) if s in customer_by_sku
            ]
        rows.append(row)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({
            "count": len(rows),
            "customer_price_endpoint_ok": customer_price_error is None,
            "customer_price_error": customer_price_error,
            "items": rows,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Saved {len(rows)} Ozon items to {out}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
