"""Contribution-margin estimate for live Ozon pricing.

RRP control uses customer_price.
Margin uses seller-side price (marketing_price if present, otherwise current_price),
because Ozon may fund part of the buyer discount.

This is contribution margin BEFORE tax and advertising. Realized net profit should
be reconciled against finance transactions / monthly unit-economics data.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MarginEstimate:
    seller_revenue_rub: float
    landed_cost_rub: float
    commission_rub: float
    logistics_rub: float
    acquiring_rub: float
    other_direct_costs_rub: float
    profit_before_tax_ads_rub: float
    margin_before_tax_ads_percent: float
    revenue_source: str


def estimate_live_margin(*, purchase_cost: float, current_price: float,
                         marketing_price: float, commission_percent: float,
                         logistics_rub: float = 0.0, acquiring_rub: float = 0.0,
                         other_direct_costs_rub: float = 0.0) -> MarginEstimate:
    if purchase_cost <= 0:
        raise ValueError("purchase_cost must be > 0")
    seller_revenue = float(marketing_price or current_price)
    if seller_revenue <= 0:
        raise ValueError("seller-side price must be > 0")

    landed = float(purchase_cost) * 1.35
    commission = seller_revenue * float(commission_percent or 0) / 100.0
    profit = (
        seller_revenue
        - commission
        - float(logistics_rub or 0)
        - float(acquiring_rub or 0)
        - float(other_direct_costs_rub or 0)
        - landed
    )
    margin = profit / seller_revenue * 100.0
    return MarginEstimate(
        seller_revenue_rub=round(seller_revenue, 2),
        landed_cost_rub=round(landed, 2),
        commission_rub=round(commission, 2),
        logistics_rub=round(float(logistics_rub or 0), 2),
        acquiring_rub=round(float(acquiring_rub or 0), 2),
        other_direct_costs_rub=round(float(other_direct_costs_rub or 0), 2),
        profit_before_tax_ads_rub=round(profit, 2),
        margin_before_tax_ads_percent=round(margin, 2),
        revenue_source="marketing_price" if marketing_price else "current_price",
    )
