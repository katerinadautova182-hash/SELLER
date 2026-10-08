"""Historical buyer-price observations from real WB orders (read-only).

finishedPrice contains marketplace discounts but excludes a separate WB Wallet
payment benefit. These observations are NOT live Moscow storefront quotes.
"""
import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

URL = "https://statistics-api.wildberries.ru/api/v1/supplier/orders"


def fetch_recent_orders(token, days=7):
    since = (datetime.now(ZoneInfo("Europe/Moscow")) - timedelta(days=days)).strftime("%Y-%m-%d")
    url = URL + "?" + urllib.parse.urlencode({"dateFrom": since, "flag": 0})
    request = urllib.request.Request(url, headers={"Authorization": token, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=40) as response:
        data = json.load(response)
    if not isinstance(data, list):
        raise ValueError("Unexpected WB orders response")
    # Using a bounded recent time range normally returns far fewer than 80k rows.
    if len(data) >= 80000:
        raise ValueError("WB orders pagination needed; incomplete data would mislead")
    return data


def latest_observations(orders, in_stock_nmids):
    by_id = {}
    for row in orders:
        if row.get("isCancel") or row.get("isStorno"):
            continue
        nm = str(row.get("nmId") or row.get("nmID") or "")
        if nm not in in_stock_nmids:
            continue
        try:
            buyer = float(row.get("finishedPrice") or 0)
            seller = float(row.get("priceWithDisc") or 0)
        except (TypeError, ValueError):
            continue
        if buyer <= 0:
            continue
        date = str(row.get("date") or "")
        observation = {
            "nm_id": nm, "order_date": date, "buyer_order_price_excl_wallet": buyer,
            "seller_price_at_order": seller if seller > 0 else None,
            "reported_spp_pct": row.get("spp"),
            "source": "WB_STATISTICS_ORDERS", "live_price": False,
            "moscow_only": False,
        }
        old = by_id.get(nm)
        if old is None or date > old["order_date"]:
            by_id[nm] = observation
    return by_id
