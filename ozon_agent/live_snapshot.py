"""Read-only live Ozon catalog snapshot for the margin agent.

RRP control is performed only for ordinary products that are actually in stock.
Clearance (УЦ) and out-of-stock products are excluded before storefront-price
requests and never enter the RRP report.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from ozon_export.client import OzonClient
from ozon_export.products import fetch_product_ids, fetch_products_info
from .business_rules import is_clearance_sku
from .pricing import fetch_price_rows, normalize_price_item, fetch_customer_prices


def _product_has_stock(list_item: dict) -> bool:
    """Use Ozon product/list stock flags when available.

    If the API supplies both has_fbo_stocks and has_fbs_stocks, a product is
    considered in stock when at least one is true. If those fields are absent,
    we do not guess that the product is out of stock.
    """
    has_fbo = list_item.get("has_fbo_stocks")
    has_fbs = list_item.get("has_fbs_stocks")
    if has_fbo is None and has_fbs is None:
        return True
    return bool(has_fbo) or bool(has_fbs)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts/ozon_catalog.json")
    args = parser.parse_args()

    client = OzonClient()
    id_items = fetch_product_ids(client)

    product_ids = [int(x["product_id"]) for x in id_items if x.get("product_id")]
    list_by_pid = {
        int(x["product_id"]): x
        for x in id_items
        if x.get("product_id")
    }

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
                if s not in product_skus:
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

    eligibility: dict[int, str] = {}
    eligible_pids: list[int] = []

    for pid in product_ids:
        offer_id = str(offers.get(pid) or list_by_pid.get(pid, {}).get("offer_id") or "").strip()
        if is_clearance_sku(offer_id):
            eligibility[pid] = "CLEARANCE"
            continue
        if not _product_has_stock(list_by_pid.get(pid, {})):
            eligibility[pid] = "OUT_OF_STOCK"
            continue
        eligibility[pid] = "ELIGIBLE"
        eligible_pids.append(pid)

    eligible_skus: list[str] = []
    for pid in eligible_pids:
        eligible_skus.extend(skus_by_product.get(pid, []))

    customer_by_sku = fetch_customer_prices(
        client,
        eligible_skus,
        attempts=3,
        retry_delay_seconds=2.0,
        require_complete=False,
    )

    unresolved_products: list[int] = []
    for pid in eligible_pids:
        skus = skus_by_product.get(pid, [])
        if not skus or not any(s in customer_by_sku for s in skus):
            unresolved_products.append(pid)

    for round_no in range(1, 4):
        if not unresolved_products:
            break
        still_missing: list[int] = []
        for pid in unresolved_products:
            skus = skus_by_product.get(pid, [])
            if not skus:
                still_missing.append(pid)
                continue
            extra = fetch_customer_prices(
                client,
                skus,
                attempts=1,
                retry_delay_seconds=0,
                require_complete=False,
            )
            customer_by_sku.update(extra)
            if not any(s in customer_by_sku for s in skus):
                still_missing.append(pid)
        unresolved_products = still_missing
        if unresolved_products and round_no < 3:
            time.sleep(2.0 * round_no)

    unresolved_set = set(unresolved_products)

    # Pricing rows are still collected for diagnostics, but only ELIGIBLE rows
    # enter RRP checking.
    price_rows = fetch_price_rows(client, product_ids)
    rows = []
    verified_products = 0

    for raw in price_rows:
        row = normalize_price_item(raw)
        pid = row.get("product_id")
        if pid is not None:
            pid = int(pid)
            row["name"] = names.get(pid, "")
            row["offer_id"] = row.get("offer_id") or offers.get(pid, "")
            row["all_skus"] = list(skus_by_product.get(pid, []))
            row["eligibility"] = eligibility.get(pid, "ELIGIBLE")
            row["has_stock"] = row["eligibility"] != "OUT_OF_STOCK"

            if row["eligibility"] == "ELIGIBLE":
                candidate_prices = [
                    customer_by_sku[s]["customer_price"]
                    for s in skus_by_product.get(pid, [])
                    if s in customer_by_sku
                ]
                seller_promo_prices = [
                    customer_by_sku[s]["seller_promo_price"]
                    for s in skus_by_product.get(pid, [])
                    if s in customer_by_sku
                    and float(customer_by_sku[s].get("seller_promo_price") or 0) > 0
                ]
                row["customer_price"] = min(candidate_prices) if candidate_prices else None
                row["seller_promo_price"] = min(seller_promo_prices) if seller_promo_prices else None
                row["customer_price_verified"] = bool(candidate_prices)
                row["customer_price_skus"] = [
                    s for s in skus_by_product.get(pid, []) if s in customer_by_sku
                ]
                if candidate_prices:
                    verified_products += 1
                    row["customer_price_status"] = "OK"
                elif pid in unresolved_set:
                    row["customer_price_status"] = "PRICE_NOT_VERIFIED"
                else:
                    row["customer_price_status"] = "PRICE_NOT_VERIFIED"
            else:
                row["customer_price"] = None
                row["customer_price_verified"] = False
                row["customer_price_skus"] = []
                row["customer_price_status"] = "NOT_REQUIRED"
        rows.append(row)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({
            "count": len(rows),
            "eligible_count": len(eligible_pids),
            "customer_price_endpoint_ok": True,
            "customer_price_error": None,
            "verified_products": verified_products,
            "unresolved_products": len(unresolved_products),
            "unresolved_offer_ids": [
                str(offers.get(pid) or pid) for pid in unresolved_products
            ],
            "items": rows,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        f"Saved {len(rows)} Ozon items to {out}; "
        f"eligible for RRP={len(eligible_pids)}; "
        f"verified buyer price={verified_products}; "
        f"unresolved after retries={len(unresolved_products)}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
