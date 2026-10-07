"""Purchase-cost source policy for live pricing.

Priority:
1) current supplier price list
2) explicit receipt/manual purchase cost
3) legacy alias cost as fallback

Legacy AUTO_ALIAS values must not silently override newer supplier prices.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostChoice:
    purchase_cost: float | None
    source: str | None
    status: str
    conflict_percent: float | None = None


def choose_purchase_cost(*, supplier_price: float | None,
                         receipt_cost: float | None,
                         legacy_cost: float | None,
                         legacy_source: str | None = None,
                         conflict_threshold_percent: float = 25.0) -> CostChoice:
    candidates = [
        ("CURRENT_SUPPLIER_PRICE", supplier_price),
        ("RECEIPT_OR_MANUAL", receipt_cost),
        (legacy_source or "LEGACY_ALIAS", legacy_cost),
    ]
    valid = [(name, float(v)) for name, v in candidates if v is not None and float(v) > 0]
    if not valid:
        return CostChoice(None, None, "MISSING_COST", None)

    chosen_source, chosen = valid[0]
    conflict = None
    if len(valid) > 1:
        other = valid[1][1]
        conflict = abs(chosen - other) / chosen * 100.0
        if conflict >= conflict_threshold_percent:
            return CostChoice(round(chosen, 2), chosen_source, "COST_CONFLICT", round(conflict, 1))

    return CostChoice(round(chosen, 2), chosen_source, "OK", round(conflict, 1) if conflict is not None else None)
