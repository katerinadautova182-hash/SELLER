"""Read current Ozon price economics and evaluate SKU guardrails.

This module is read-only. Mutation endpoints are intentionally absent in MVP-0.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Iterable, TYPE_CHECKING

if TYPE_CHECKING:
    from ozon_export.client import OzonClient
from .economics import EconomicsInput, calculate_economics


def fetch_price_rows(client: "OzonClient", product_ids: Iterable[int]) -> list[dict]:
    ids = list(product_ids)
    out: list[dict] = []
    for start in range(0, len(ids), 1000):
        batch = ids[start:start + 1000]
        data = client.post(
            "/v5/product/info/prices",
            {"filter": {"product_id": batch, "visibility": "ALL"}, "limit": 1000},
        )
        out.extend(data.get("items", []))
    return out


def normalize_price_item(item: dict) -> dict:
    price = item.get("price") or {}
    commissions = item.get("commissions") or {}

    commission_fbo = float(commissions.get("sales_percent_fbo") or 0)
    commission_fbs = float(commissions.get("sales_percent_fbs") or 0)
    commission_percent = commission_fbo or commission_fbs

    logistics_fbo = float(commissions.get("fbo_direct_flow_trans_min_amount") or 0)
    logistics_fbs = float(commissions.get("fbs_direct_flow_trans_min_amount") or 0)
    logistics_rub = logistics_fbo or logistics_fbs

    return {
        "product_id": item.get("product_id"),
        "offer_id": item.get("offer_id"),
        "current_price": float(price.get("price") or 0),
        "marketing_price": float(price.get("marketing_seller_price") or 0),
        "ozon_min_price": float(price.get("min_price") or 0),
        "commission_percent": commission_percent,
        "logistics_rub": logistics_rub,
        "acquiring_rub": float(item.get("acquiring") or 0),
    }


def evaluate_sku(*, ozon_row: dict, purchase_price: float, rrp: float,
                 target_margin_percent: float = 0.0,
                 other_direct_costs_rub: float = 0.0) -> dict:
    current_price = ozon_row["current_price"] or ozon_row["marketing_price"]
    result = calculate_economics(EconomicsInput(
        purchase_price=purchase_price,
        rrp=rrp,
        current_price=current_price,
        commission_percent=ozon_row["commission_percent"],
        logistics_rub=ozon_row["logistics_rub"],
        acquiring_rub=ozon_row["acquiring_rub"],
        other_direct_costs_rub=other_direct_costs_rub,
        target_margin_percent=target_margin_percent,
    ))
    return {**ozon_row, **asdict(result)}


def fetch_customer_prices(client: "OzonClient", skus: Iterable[int | str]) -> dict[str, dict]:
    """Fetch storefront/customer prices.

    Uses POST /v1/product/prices/details. The endpoint may require Premium Pro.
    Failure to access it must NOT be treated as proof that the storefront price
    is safe; callers should mark the buyer price as unverified.
    """
    clean = [str(x) for x in skus if x not in (None, "", 0, "0")]
    out: dict[str, dict] = {}
    for start in range(0, len(clean), 1000):
        batch = clean[start:start + 1000]
        data = client.post("/v1/product/prices/details", {"skus": batch})
        for row in data.get("prices", []):
            sku = str(row.get("sku") or "")
            cp = row.get("customer_price") or {}
            amount = cp.get("amount")
            if not sku or amount in (None, ""):
                continue
            out[sku] = {
                "customer_price": float(amount),
                "currency": cp.get("currency"),
                "offer_id": row.get("offer_id"),
                "seller_promo_price": float((row.get("price") or {}).get("amount") or 0),
            }
    return out


def evaluate_customer_price_policy(*, customer_price: float | None, rrp: float) -> tuple[str, str]:
    """Hard RRP policy based on buyer-facing price only."""
    if customer_price is None or customer_price <= 0:
        return (
            "PRICE_NOT_VERIFIED",
            "Цена для покупателя не подтверждена; соблюдение РРЦ считать нельзя.",
        )
    floor = float(ceil(rrp * RRP_FLOOR_FACTOR))
    if customer_price < floor:
        return (
            "CRITICAL",
            f"Цена для покупателя {customer_price:.2f} ниже РРЦ+5% ({floor:.2f}).",
        )
    return (
        "OK",
        f"Цена для покупателя подтверждена и не ниже РРЦ+5% ({floor:.2f}).",
    )
