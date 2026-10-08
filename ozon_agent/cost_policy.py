"""Purchase-cost source policy.

Trusted sources for profitability:
1) current supplier / purchase price source;
2) explicit receipt / posting / invoice / manual confirmed cost.

AUTO_ALIAS, AUTO_ALIAS_PLUS and legacy values are never allowed to become
profitability cost. They may be retained only for diagnostics/matching.
"""
from __future__ import annotations
from dataclasses import dataclass


UNTRUSTED_SOURCE_MARKERS = ("AUTO_ALIAS", "LEGACY")


@dataclass(frozen=True)
class CostChoice:
    purchase_cost: float | None
    source: str | None
    status: str
    conflict_percent: float | None = None


def is_trusted_cost_source(source: str | None) -> bool:
    s = str(source or "").upper()
    return not any(marker in s for marker in UNTRUSTED_SOURCE_MARKERS)


def choose_purchase_cost(*, supplier_price: float | None,
                         receipt_cost: float | None,
                         legacy_cost: float | None,
                         legacy_source: str | None = None,
                         conflict_threshold_percent: float = 25.0) -> CostChoice:
    trusted = [
        ("CURRENT_SUPPLIER_PRICE", supplier_price),
        ("RECEIPT_OR_MANUAL", receipt_cost),
    ]
    valid = [(name, float(v)) for name, v in trusted if v is not None and float(v) > 0]

    if not valid:
        # Legacy/auto-alias may exist, but must never silently become COGS.
        if legacy_cost is not None and float(legacy_cost) > 0:
            return CostChoice(None, legacy_source or "LEGACY_ALIAS", "UNTRUSTED_LEGACY_COST", None)
        return CostChoice(None, None, "MISSING_COST", None)

    chosen_source, chosen = valid[0]
    conflict = None
    if len(valid) > 1:
        other = valid[1][1]
        conflict = abs(chosen - other) / chosen * 100.0
        if conflict >= conflict_threshold_percent:
            return CostChoice(round(chosen, 2), chosen_source, "COST_CONFLICT", round(conflict, 1))

    return CostChoice(round(chosen, 2), chosen_source, "OK", round(conflict, 1) if conflict is not None else None)
