"""Current Ozon finance accrual API (post-Sep-2026).

Uses /v1/finance/accrual/by-day. Old v3 transaction endpoints are deprecated.
"""
from __future__ import annotations

from collections import defaultdict


def fetch_accruals_for_day(client, day: str, max_pages: int = 200) -> list[dict]:
    last_id = ""
    out: list[dict] = []
    for _ in range(max_pages):
        payload = {"date": day, "last_id": last_id}
        data = client.post("/v1/finance/accrual/by-day", payload)
        rows = data.get("accruals") or []
        out.extend(rows)
        new_last_id = data.get("last_id") or ""
        if not rows or not new_last_id or new_last_id == last_id:
            break
        last_id = new_last_id
    return out


def aggregate_sku_finance(accruals: list[dict]) -> dict[str, dict]:
    """Aggregate realized finance amounts by Ozon SKU.

    total_amount is treated as the authoritative accrued amount for each accrual.
    Product-level sale/commission/delivery fields are retained separately where present.
    """
    result = defaultdict(lambda: {
        "accrued_total_rub": 0.0,
        "sale_amount_rub": 0.0,
        "seller_price_rub": 0.0,
        "sale_commission_rub": 0.0,
        "delivery_rub": 0.0,
        "item_fees_rub": 0.0,
        "operations": 0,
    })

    def money(node):
        if not node:
            return 0.0
        raw = node.get("amount") if isinstance(node, dict) else node
        try:
            return float(raw or 0)
        except (TypeError, ValueError):
            return 0.0

    for acc in accruals:
        total = money(acc.get("total_amount"))
        posting = acc.get("posting") or {}
        products = posting.get("products") or []

        # Posting/product accruals
        for p in products:
            sku = str(p.get("sku") or "")
            if not sku:
                continue
            r = result[sku]
            r["operations"] += 1
            r["accrued_total_rub"] += total
            commission = p.get("commission") or {}
            r["sale_amount_rub"] += money(commission.get("sale_amount"))
            r["seller_price_rub"] += money(commission.get("seller_price"))
            r["sale_commission_rub"] += money(commission.get("sale_commission"))
            r["delivery_rub"] += money((p.get("delivery") or {}).get("total_accrued"))

        # Item fees are directly tied to SKU.
        item_fees = (acc.get("item_fees") or {}).get("fees") or []
        for grp in item_fees:
            sku = str(grp.get("sku") or "")
            if not sku:
                continue
            for fee in grp.get("fees") or []:
                result[sku]["item_fees_rub"] += money(fee.get("accrued"))

    return {sku: {k: round(v, 2) if isinstance(v, float) else v for k, v in vals.items()}
            for sku, vals in result.items()}
