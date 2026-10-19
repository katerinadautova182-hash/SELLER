"""Temporary Mark Shmidt promo policy (19 Oct through 20 Nov 2026, Moscow time).

No RRP values are stored here. Promo RRPs are derived at runtime from the
existing private RRP map, so the public repository never contains the price list.
"""
from __future__ import annotations

from datetime import date, datetime
from math import ceil
from zoneinfo import ZoneInfo

from .business_rules import box_rule, canonical_sku, manual_rrp
from .catalog import normalize_sku

PROMO_START = date(2026, 10, 19)
PROMO_END = date(2026, 11, 20)
PROMO_FACTOR = 0.70
MOSCOW = ZoneInfo("Europe/Moscow")


def moscow_date(now: datetime | None = None) -> date:
    if now is None:
        now = datetime.now(MOSCOW)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=MOSCOW)
    else:
        now = now.astimezone(MOSCOW)
    return now.date()


def promo_active(day: date | None = None) -> bool:
    day = day or moscow_date()
    return PROMO_START <= day <= PROMO_END


def is_promo_offer(offer_id: str | None) -> bool:
    key = normalize_sku(offer_id)
    if not key:
        return False
    if key in {
        normalize_sku("206"),
        normalize_sku("201"),
        normalize_sku("ms702"),
        normalize_sku("ms702 ripple"),
        normalize_sku("ms9905"),
    }:
        return True
    if "8862" in key:
        return True
    if "8828" in key and "BLACK" not in key:
        return True
    return False


def regular_rrp(offer_id: str | None, rrp_map: dict[str, float]) -> float | None:
    value = manual_rrp(offer_id)
    if value is not None:
        return float(value)

    rule = box_rule(offer_id)
    if rule:
        base, multiplier = rule
        value = rrp_map.get(normalize_sku(base))
        return float(value) * float(multiplier) if value is not None else None

    canonical = canonical_sku(offer_id)
    value = manual_rrp(canonical)
    if value is not None:
        return float(value)
    value = rrp_map.get(normalize_sku(canonical))
    return float(value) if value is not None else None


def promo_rrp(offer_id: str | None, base_rrp: float | None,
              rrp_map: dict[str, float]) -> float | None:
    """Derive the temporary promo RRP without storing promo prices in Git."""
    if base_rrp is None or not is_promo_offer(offer_id):
        return base_rrp

    key = normalize_sku(offer_id)

    # All non-black 8828 colours have one common promo RRP in the supplied list.
    # Derive it from the highest regular RRP among the selected 8828 colours.
    if "8828" in key and "BLACK" not in key:
        family = [
            float(v) for k, v in rrp_map.items()
            if "8828" in normalize_sku(k) and "BLACK" not in normalize_sku(k)
            and v not in (None, "")
        ]
        family_base = max(family) if family else float(base_rrp)
        return float(ceil(family_base * PROMO_FACTOR) + 1)

    value = float(ceil(float(base_rrp) * PROMO_FACTOR))
    # Price-list rounding for the mini 702 straightener is one ruble above
    # ordinary ceiling of the 30% reduction.
    if key == normalize_sku("ms702"):
        value += 1.0
    return value


def effective_rrp(offer_id: str | None, base_rrp: float | None,
                  rrp_map: dict[str, float], day: date | None = None) -> float | None:
    if not promo_active(day):
        return base_rrp
    return promo_rrp(offer_id, base_rrp, rrp_map)


def target_buyer_price(offer_id: str | None, base_rrp: float | None,
                       rrp_map: dict[str, float], *, mode: str) -> float | None:
    """Return the RRP+5% buyer-price floor for promo or restoration."""
    if base_rrp is None or not is_promo_offer(offer_id):
        return None
    rrp = promo_rrp(offer_id, base_rrp, rrp_map) if mode == "promo" else float(base_rrp)
    return float(ceil(float(rrp) * 1.05))
