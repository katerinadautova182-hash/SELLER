"""Read current Ozon price economics and evaluate SKU guardrails.

This module is read-only. Mutation endpoints are intentionally absent in MVP-0.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Iterable

from ozon_export.client import OzonClient
from .economics import EconomicsInput, calculate_economics


def fetch_price_rows(client: OzonClient, product_ids: Iterable[int]) -> list[dict]:
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
