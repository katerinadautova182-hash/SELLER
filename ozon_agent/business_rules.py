"""Business-specific SKU exceptions and manual overrides."""
from __future__ import annotations
from .catalog import normalize_sku

MANUAL_PURCHASE_COSTS = {
    normalize_sku("Ghost2"): 6848.14,
}

BOX_MULTIPLIERS = {
    normalize_sku("JDJ02-BOX"): (normalize_sku("JDJ02"), 10),
}

def is_clearance_sku(offer_id: str | None) -> bool:
    """Clearance / discounted stock (УЦ) is excluded from RRP correction alerts."""
    if not offer_id:
        return False
    raw = str(offer_id).upper().replace(" ", "")
    return "УЦ" in raw

def manual_purchase_cost(offer_id: str | None) -> float | None:
    return MANUAL_PURCHASE_COSTS.get(normalize_sku(offer_id))

def box_rule(offer_id: str | None):
    return BOX_MULTIPLIERS.get(normalize_sku(offer_id))
