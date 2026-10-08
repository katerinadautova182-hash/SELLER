"""WB-specific verified article equivalences.

Every mapping here must be justified by a model identifier or identical RRP of
all existing color variants. Never equate Mark I with Mark II, change tool
diameters, or infer bundle prices from string similarity.
"""
from ozon_agent.catalog import normalize_sku

WB_RRP_ALIASES = {
    # Same exact base model, WB listing includes a generic colour marker.
    normalize_sku("ms9610цвет"): "MS9610",
    # Bare Mark Shmidt product number, branded key in price list.
    normalize_sku("8862"): "MS8862",
    # The two priced MS8828 variants (RED/BLACK) have the same RRP.
    normalize_sku("MS8828"): "MS8828BLACK",
    # Model 505F, attachment/form factor 23/32 preserved exactly.
    normalize_sku("505F/23/32."): "MS505F2332",
    # Ч is the black-colour suffix used in the price list.
    normalize_sku("3500black"): "3500Ч",
}
