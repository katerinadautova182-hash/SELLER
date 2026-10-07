"""Business-specific SKU exceptions and manual overrides."""
from __future__ import annotations
from .catalog import normalize_sku

# Confirmed marketplace aliases -> canonical reference SKU.
# Once confirmed, an alias belongs here and must not be rediscovered every run.
SKU_ALIASES = {
    normalize_sku("MS509"): "MS 509",
    normalize_sku("701GREY"): "701C",
    normalize_sku("801GREEN"): "801з",
}

MANUAL_PURCHASE_COSTS = {
    normalize_sku("Ghost2"): 6848.14,
}

BOX_MULTIPLIERS = {
    normalize_sku("JDJ02-BOX"): (normalize_sku("JDJ02"), 10),
}


def canonical_sku(offer_id: str | None) -> str:
    """Return canonical reference SKU for RRP/purchase lookup."""
    raw = str(offer_id or "").strip()
    if not raw:
        return ""
    return SKU_ALIASES.get(normalize_sku(raw), raw)


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
