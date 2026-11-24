"""Commercial buyer-price targets shared by Ozon and Yandex Market.

For *confirmed main equipment* the preferred paid price is RRP + 200..1000 RUB
(default steering point: RRP + 200 RUB). A 10 RUB deviation from either
preferred-band edge is tolerated in alerts; the steering price stays inside.. This is a TARGET, not an automatic
permission to lower a price or override unit-economics limits.

Accessories, combs, consumables, clearance and unrecognised SKUs are NOT
automatically assigned the equipment target. Their individual rules remain
separate; all products retain the baseline RRP-breach check.
"""
from __future__ import annotations

from math import ceil

from .catalog import normalize_sku
from .business_rules import canonical_sku, is_clearance_sku

TARGET_MIN_MARKUP_RUB = 200
TARGET_MAX_MARKUP_RUB = 1000
TARGET_DEFAULT_MARKUP_RUB = 200
TARGET_TOLERANCE_RUB = 10.0

# Explicitly known electric/professional devices. Do not infer an equipment
# category from RRP/price alone; a costly comb is still an accessory.
_DEVICE_MODELS = (
    "2020C", "2020T", "2020G", "2020H", "2025C",
    "3000C", "3000T", "DI2025", "GH2024", "SH2301",
    "1090", "701", "706", "802", "8816", "9910",
    "205", "204", "207", "109TITAN", "403", "404",
    "509", "510", "705", "303", "507", "511",
    "201", "206", "702", "9905", "8862", "8828", "9903R",
)
_DEVICE_PREFIXES = tuple(sorted(
    {normalize_sku(model) for model in _DEVICE_MODELS}
    | {normalize_sku("MS" + model) for model in _DEVICE_MODELS},
    key=len, reverse=True,
))
_ACCESSORY_WORDS = (
    "расчёск", "расческ", "comb", "нож", "лезвие", "насадк",
    "brush", "щётка", "щетка", "брашинг", "кейс", "чехол",
    "заряд", "подставк", "адаптер", "аксессуар", "oil",
    "blade", "guard", "attachment", "replacement",
)
_DEVICE_WORDS = (
    "машинка для стриж", "триммер", "электробрит", "шейвер",
    "фен ", "фен-", "плойка", "выпрямител", "стайлер",
    "щипцы", "гофре", "hair dryer", "clipper", "trimmer",
    "shaver", "straightener",
)


def is_main_equipment(offer_id: str | None, name: str | None = None) -> bool:
    """Conservative SKU/category detection; unknowns keep their own pricing."""
    if is_clearance_sku(offer_id):
        return False
    description = (str(name or "") + " " + str(offer_id or "")).casefold()
    if any(word in description for word in _ACCESSORY_WORDS):
        return False
    if any(word in description for word in _DEVICE_WORDS):
        return True
    key = normalize_sku(canonical_sku(offer_id))
    if not key:
        return False
    # Model 201 must not swallow unrelated SKU MS2011, and 701 must not
    # classify 7011: colour/text variants are allowed, numeric extensions not.
    return any(key == prefix or (key.startswith(prefix) and
               not key[len(prefix)].isdigit()) for prefix in _DEVICE_PREFIXES)


def target_band(rrp: float, offer_id: str | None,
                name: str | None = None) -> tuple[float, float] | None:
    """Preferred inclusive customer-price band, or None for special/unknown."""
    if not is_main_equipment(offer_id, name):
        return None
    base = ceil(float(rrp))
    return float(base + TARGET_MIN_MARKUP_RUB), float(base + TARGET_MAX_MARKUP_RUB)


def steering_price(rrp: float, offer_id: str | None,
                   name: str | None = None) -> float | None:
    """Default controlled/manual buyer price (RRP + 200 RUB)."""
    if target_band(rrp, offer_id, name) is None:
        return None
    return float(ceil(float(rrp)) + TARGET_DEFAULT_MARKUP_RUB)


def price_band_deviation(price: float, rrp: float, offer_id: str | None,
                         name: str | None = None) -> str | None:
    """Return BELOW/IN_BAND/ABOVE for equipment, tolerating <=10 RUB.

    This is a monitoring tolerance, never an instruction to intentionally
    set prices above the RRP+1000 RUB target cap. Base RRP breaches are
    checked separately and are never excused by this tolerance.
    """
    band = target_band(rrp, offer_id, name)
    if band is None:
        return None
    if float(price) < band[0] - TARGET_TOLERANCE_RUB:
        return "BELOW"
    if float(price) > band[1] + TARGET_TOLERANCE_RUB:
        return "ABOVE"
    return "IN_BAND"
