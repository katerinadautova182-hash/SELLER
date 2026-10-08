"""Current Ozon finance accrual API (post-Sep-2026).

Uses /v1/finance/accrual/{types,by-day,postings}. Old v3 transaction
endpoints are deprecated.
"""
from __future__ import annotations

from collections import defaultdict


CATEGORY_MAP = {
    32: "processing_and_delivery",
    29: "processing_and_delivery",
    98: "processing_and_delivery",
    41: "services",
    77: "services",
    15: "services",
    46: "services",
    84: "services",
    96: "services",
    39: "services",
    38: "services",
    78: "services",
    48: "services",
    12: "services",
    54: "services",
    59: "returns_and_cancellations",
    45: "returns_and_cancellations",
    6: "returns_and_cancellations",
    1: "acquiring",
    10: "compensation",
    25: "compensation",
}


def money(node) -> float:
    if node is None:
        return 0.0
    raw = node.get("amount") if isinstance(node, dict) else node
    try:
        return float(raw or 0)
    except (TypeError, ValueError):
        return 0.0


def fetch_accrual_types(client) -> dict[int, dict]:
    data = client.post("/v1/finance/accrual/types", {})
    out = {}
    for row in data.get("accrual_types") or []:
        try:
            type_id = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        out[type_id] = {
            "name": str(row.get("name") or ""),
            "description": str(row.get("description") or row.get("name") or ""),
            "category": CATEGORY_MAP.get(type_id, "UNMAPPED"),
        }
    return out


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
    """Aggregate realized item-level finance by Ozon SKU.

    Signs are preserved exactly as returned by Ozon:
    revenue is normally positive; commission/delivery/fees normally negative.
    non_item_fee is intentionally NOT allocated to SKU.
    """
    result = defaultdict(lambda: {
        "seller_price_rub": 0.0,
        "sale_amount_rub": 0.0,
        "sale_commission_rub": 0.0,
        "delivery_rub": 0.0,
        "item_fees_rub": 0.0,
        "item_compensation_rub": 0.0,
        "operations": 0,
    })

    for acc in accruals:
        posting = acc.get("posting") or {}
        for p in posting.get("products") or []:
            sku = str(p.get("sku") or "")
            if not sku:
                continue
            r = result[sku]
            r["operations"] += 1
            commission = p.get("commission") or {}
            r["sale_amount_rub"] += money(commission.get("sale_amount"))
            r["seller_price_rub"] += money(commission.get("seller_price"))
            r["sale_commission_rub"] += money(commission.get("sale_commission"))
            r["delivery_rub"] += money((p.get("delivery") or {}).get("total_accrued"))

        for grp in ((acc.get("item_fees") or {}).get("fees") or []):
            sku = str(grp.get("sku") or "")
            if not sku:
                continue
            for fee in grp.get("fees") or []:
                amount = money(fee.get("accrued"))
                type_id = fee.get("type_id")
                result[sku]["item_fees_rub"] += amount
                if type_id in (10, 25) and amount > 0:
                    result[sku]["item_compensation_rub"] += amount

    out = {}
    for sku, vals in result.items():
        vals["direct_finance_net_rub"] = (
            vals["seller_price_rub"]
            + vals["sale_commission_rub"]
            + vals["delivery_rub"]
            + vals["item_fees_rub"]
        )
        out[sku] = {
            k: round(v, 2) if isinstance(v, float) else v
            for k, v in vals.items()
        }
    return out


def aggregate_non_item_finance(accruals: list[dict], type_map: dict[int, dict] | None = None) -> dict[int, dict]:
    """Aggregate seller-level charges that have no SKU.

    These values must remain separate from SKU economics until an explicit
    allocation policy is approved.
    """
    type_map = type_map or {}
    result = defaultdict(lambda: {"amount_rub": 0.0, "operations": 0})
    for acc in accruals:
        fee = acc.get("non_item_fee")
        if not fee:
            continue
        try:
            type_id = int(fee.get("type_id"))
        except (TypeError, ValueError):
            type_id = -1
        r = result[type_id]
        r["amount_rub"] += money(fee.get("accrued"))
        r["operations"] += 1

    out = {}
    for type_id, vals in result.items():
        meta = type_map.get(type_id, {})
        out[type_id] = {
            "type_id": type_id,
            "type_name": meta.get("name", ""),
            "type_description": meta.get("description", ""),
            "category": meta.get("category", "UNMAPPED"),
            "amount_rub": round(vals["amount_rub"], 2),
            "operations": vals["operations"],
        }
    return out
