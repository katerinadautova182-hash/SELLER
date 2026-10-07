"""Business-specific SKU exceptions and manual overrides."""
from __future__ import annotations
from .catalog import normalize_sku

# Confirmed marketplace aliases -> canonical reference SKU.
# Once confirmed, an alias belongs here and must not be rediscovered every run.
SKU_ALIASES = {
    normalize_sku("MS509"): "MS 509",
    normalize_sku("701GREY"): "701C",
    normalize_sku("801GREEN"): "801з",
    normalize_sku("JDJ02-BOX"): "JDJ02",
}

MANUAL_PURCHASE_COSTS = {
    normalize_sku("Ghost2"): 6848.14,
}

BOX_MULTIPLIERS = {
    normalize_sku("JDJ02-BOX"): (normalize_sku("JDJ02"), 10),
}

# Marketplace bundle SKU -> component SKUs.
# Bundle purchase cost is the sum of the component purchase costs.
BUNDLE_COMPONENTS = {
    normalize_sku("MS9903R+ms101"): (
        normalize_sku("MS9903R-2400"),
        normalize_sku("MS101"),
    ),
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


def bundle_components(offer_id: str | None):
    return BUNDLE_COMPONENTS.get(normalize_sku(offer_id))


# Confirmed manual RRP values that are not present in the current price list.
MANUAL_RRP_OVERRIDES = {
    normalize_sku("PET GROOMING KIT"): 18700.0,
    normalize_sku("403"): 4511.0,
    normalize_sku("403 brown"): 4511.0,
    normalize_sku("403black"): 4511.0,
    normalize_sku("404"): 6546.0,
    normalize_sku("J201 LIGHT GREEN"): 845.0,
    normalize_sku("MS202"): 2088.0,
    normalize_sku("MS509"): 3900.0,
    normalize_sku("PB52"): 174.0,
    normalize_sku("PB71"): 143.0,
    normalize_sku("SF 03"): 1347.0,
}


def manual_rrp(offer_id: str | None) -> float | None:
    return MANUAL_RRP_OVERRIDES.get(normalize_sku(offer_id))
