"""Auto-fix RED violations up to 250 RUB before Telegram."""
from __future__ import annotations
from math import ceil
from .price_alert import collect_violations

MAX_GAP = 250.0

def plan_small_red_updates(snapshot, rrp_map):
    red, _, _ = collect_violations(snapshot, rrp_map)
    items = {str(x.get("offer_id") or "").strip(): x for x in snapshot.get("items", [])}
    out = []
    for v in red:
        gap = float(v["gap_to_rrp"])
        if not (0 < gap <= MAX_GAP):
            continue
        item = items.get(v["offer_id"]) or {}
        seller = float(item.get("current_price") or 0)
        if seller <= 0:
            continue
        increase = float(ceil(gap * 2))
        min_price = float(item.get("ozon_min_price") or 0)
        if min_price >= float(ceil(seller + increase)):
            min_price = 0.0
        out.append({
            "offer_id": v["offer_id"],
            "seller_price_before": seller,
            "seller_price_after": float(ceil(seller + increase)),
            "increase": increase,
            "gap_to_rrp": gap,
            "rrp": float(v["rrp"]),
            "customer_price_before": float(v["customer_price"]),
            "green_floor": float(v["floor"]),
            "min_price": min_price,
        })
    return out
