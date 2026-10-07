"""Deterministic unit-economics core for Ozon.

Important: the LLM must never calculate or override these numbers itself.
All price floors are produced here by deterministic code.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil

IMPORT_LOGISTICS_FACTOR = 1.35  # purchase price + 35% delivery/customs
RRP_FLOOR_FACTOR = 1.05         # never below RRP + 5%


@dataclass(frozen=True)
class EconomicsInput:
    purchase_price: float
    rrp: float
    current_price: float
    commission_percent: float
    logistics_rub: float = 0.0
    acquiring_rub: float = 0.0
    other_direct_costs_rub: float = 0.0
    target_margin_percent: float = 0.0


@dataclass(frozen=True)
class EconomicsResult:
    landed_cost: float
    rrp_floor: float
    break_even_price: float
    target_margin_price: float
    minimum_allowed_price: float
    profit_rub: float
    operating_margin_percent: float
    safety_buffer_percent: float
    status: str
    reason: str


def _validate(p: EconomicsInput) -> None:
    if p.purchase_price <= 0:
        raise ValueError("purchase_price must be > 0")
    if p.rrp <= 0:
        raise ValueError("rrp must be > 0")
    if p.current_price <= 0:
        raise ValueError("current_price must be > 0")
    if not 0 <= p.commission_percent < 100:
        raise ValueError("commission_percent must be in [0, 100)")
    if not 0 <= p.target_margin_percent < 100:
        raise ValueError("target_margin_percent must be in [0, 100)")
    if p.logistics_rub < 0 or p.acquiring_rub < 0 or p.other_direct_costs_rub < 0:
        raise ValueError("direct costs cannot be negative")


def _required_price(costs_rub: float, commission_rate: float, margin_rate: float) -> float:
    denominator = 1.0 - commission_rate - margin_rate
    if denominator <= 0:
        raise ValueError("commission + target margin leave no feasible selling price")
    return float(ceil(costs_rub / denominator))


def calculate_economics(p: EconomicsInput) -> EconomicsResult:
    """Calculate Ozon unit economics using margin on revenue, not markup on cost.

    profit = price - commission(price) - direct fixed costs - landed cost
    operating margin = profit / price

    The commercial floor is always at least RRP * 1.05.
    The economic floor can be higher when Ozon costs make RRP+5% insufficient.
    """
    _validate(p)

    landed_cost = p.purchase_price * IMPORT_LOGISTICS_FACTOR
    fixed_direct = landed_cost + p.logistics_rub + p.acquiring_rub + p.other_direct_costs_rub
    commission_rate = p.commission_percent / 100.0
    margin_rate = p.target_margin_percent / 100.0

    break_even = _required_price(fixed_direct, commission_rate, 0.0)
    target_price = _required_price(fixed_direct, commission_rate, margin_rate)
    rrp_floor = float(ceil(p.rrp * RRP_FLOOR_FACTOR))
    minimum_allowed = max(rrp_floor, target_price)

    commission_rub = p.current_price * commission_rate
    profit = p.current_price - commission_rub - fixed_direct
    margin_pct = (profit / p.current_price) * 100.0
    safety_buffer = ((p.current_price - break_even) / p.current_price) * 100.0

    if profit < 0:
        status = "CRITICAL"
        reason = "Продажа убыточна: текущая цена ниже точки безубыточности."
    elif p.current_price < rrp_floor:
        status = "CRITICAL"
        reason = "Цена ниже коммерческого минимума РРЦ + 5%."
    elif p.current_price < minimum_allowed:
        status = "DANGER"
        reason = "Цена прибыльна, но не обеспечивает заданную минимальную маржинальность."
    elif safety_buffer < 5:
        status = "WARNING"
        reason = "Слишком маленький запас до точки безубыточности."
    else:
        status = "OK"
        reason = "Цена выше коммерческого и экономического минимумов."

    return EconomicsResult(
        landed_cost=round(landed_cost, 2),
        rrp_floor=rrp_floor,
        break_even_price=break_even,
        target_margin_price=target_price,
        minimum_allowed_price=minimum_allowed,
        profit_rub=round(profit, 2),
        operating_margin_percent=round(margin_pct, 2),
        safety_buffer_percent=round(safety_buffer, 2),
        status=status,
        reason=reason,
    )
