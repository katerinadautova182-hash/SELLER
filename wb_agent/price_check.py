"""Read-only Wildberries seller price audit.

Uses only the official Prices and Discounts API. Never modifies prices.
WB seller/club discounted prices are NOT guaranteed buyer storefront prices.
"""
import base64
import difflib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from math import ceil

from ozon_agent.catalog import normalize_sku
from ozon_agent.business_rules import canonical_sku, is_clearance_sku, manual_rrp, box_rule
from .sku_aliases import WB_RRP_ALIASES
from .fbs import available_nmids
from .order_prices import fetch_recent_orders, latest_observations

API = "https://discounts-prices-api.wildberries.ru/api/v2/list/goods/filter"
TG = "https://api.telegram.org/bot{}/sendMessage"


def fetch_catalog(token):
    items = []
    offset = 0
    for _ in range(1000):
        url = API + "?" + urllib.parse.urlencode({"limit": 1000, "offset": offset})
        for attempt in range(4):
            request = urllib.request.Request(url, headers={"Authorization": token, "Accept": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=35) as response:
                    payload = json.load(response)
                break
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(2 ** attempt + 1)
                    continue
                raise RuntimeError("WB API HTTP " + str(exc.code)) from None
        if payload.get("error"):
            raise RuntimeError("WB API returned error: " + str(payload.get("errorText", "unknown"))[:250])
        page = payload.get("data", {}).get("listGoods")
        if not isinstance(page, list):
            raise ValueError("WB response missing data.listGoods")
        if not page:
            return items
        items.extend(page)
        offset += len(page)
    raise RuntimeError("Exceeded catalog page safety limit")


def load_rrp():
    raw = os.getenv("RRP_MAP_B64", "")
    if not raw:
        return {}
    result = json.loads(base64.b64decode(raw).decode("utf-8"))
    if not isinstance(result, dict):
        raise ValueError("RRP_MAP_B64 must encode a JSON dictionary")
    return {normalize_sku(k): float(v) for k, v in result.items() if v not in (None, "")}


def analyze(items, rrp):
    red, yellow = [], []
    missing, unpriced = [], []
    for item in items:
        sku = str(item.get("vendorCode", "")).strip()
        nm_id = str(item.get("nmID", "")).strip()
        if is_clearance_sku(sku):
            continue
        reference = manual_rrp(sku)
        match_rule = "manual" if reference is not None else ""
        if reference is None:
            box = box_rule(sku)
            if box:
                base_sku, multiplier = box
                base = rrp.get(base_sku)
                reference = base * multiplier if base is not None else None
                if reference is not None:
                    match_rule = "box"
            else:
                canonical = WB_RRP_ALIASES.get(normalize_sku(sku), canonical_sku(sku))
                reference = rrp.get(normalize_sku(canonical))
                if reference is not None:
                    match_rule = "alias" if normalize_sku(canonical) != normalize_sku(sku) else "direct"
        if reference is None:
            missing.append({"sku": sku, "nm_id": nm_id})
            continue
        sizes = item.get("sizes") or []
        for size in sizes:
            price = size.get("clubDiscountedPrice")
            if price is None or float(price) <= 0:
                unpriced.append(sku or nm_id)
                continue
            price = float(price)
            entry = {"sku": sku, "nm_id": nm_id, "size": size.get("techSizeName", ""),
                     "price": price, "rrp": reference, "floor": ceil(reference * 1.05),
                     "rrp_match_rule": match_rule}
            if price < reference:
                red.append(entry)
            elif price < entry["floor"]:
                yellow.append(entry)
    return red, yellow, missing, unpriced


def format_report(items, red, yellow, missing, unpriced):
    lines = ["WB — клубные цены (не финальная цена покупателя)",
             "Товаров в API: " + str(len(items)),
             "Клубная цена ниже РРЦ: " + str(len(red)),
             "Клубная цена ниже РРЦ + 5%: " + str(len(yellow)),
             "Без сопоставления РРЦ: " + str(len(missing)),
             "Без клубной цены: " + str(len(unpriced)),
             "",
             "ВАЖНО: цена с WB Кошельком НЕ ПОЛУЧЕНА; это НЕ минимальная цена покупателя. Нарушения не подтверждены."]
    for title, rows in (("Клубная цена ниже РРЦ", red), ("Клубная цена ниже РРЦ + 5%", yellow)):
        if rows:
            lines.extend(("", title))
            for row in rows[:30]:
                lines.append("{} (WB {}): {} ₽ / РРЦ {} ₽ / цель {} ₽".format(
                    row["sku"], row["nm_id"], row["price"], row["rrp"], row["floor"]))
            if len(rows) > 30:
                lines.append("... и ещё " + str(len(rows) - 30))
    if missing:
        lines.extend(("", "Нет РРЦ для первых 15 SKU: " + ", ".join(v["sku"] or v["nm_id"] for v in missing[:15])))
    return "\n".join(lines)


def send_message(token, chat_id, message):
    for i in range(0, len(message), 3500):
        payload = urllib.parse.urlencode({"chat_id": chat_id, "text": message[i:i + 3500]}).encode()
        req = urllib.request.Request(TG.format(token), data=payload, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                data = json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError("Telegram HTTP " + str(exc.code) + " — verify CHAT_ID and /start") from None
        if not data.get("ok"):
            raise RuntimeError("Telegram rejected message")


def main():
    wb = os.environ["WB_API_KEY"].strip()
    telegram = os.environ["TELEGRAM_WB_BOT_TOKEN"].strip()
    chat_id = os.environ["TELEGRAM_WB_CHAT_ID"].strip()
    items = fetch_catalog(wb)
    rrp = load_rrp()
    stock_token = os.getenv("WB_MARKETPLACE_API_KEY", "").strip() or wb
    content_token = os.getenv("WB_CONTENT_API_KEY", "").strip() or wb
    stock_status = "NOT_CONFIGURED"
    available = set()
    if stock_token and content_token:
        # Fail closed if inventory lookup breaks: NEVER report a price violation.
        available = available_nmids(stock_token, content_token)
        stock_status = "VERIFIED"
    active_items = [item for item in items if str(item.get("nmID", "")) in available]
    red, yellow, missing, unpriced = analyze(active_items, rrp)
    orders = fetch_recent_orders(wb, days=7)
    observations = latest_observations(orders, available)
    reference_by_nm = {}
    for row in active_items:
        nm = str(row.get("nmID", ""))
        sku = str(row.get("vendorCode", "")).strip()
        if is_clearance_sku(sku):
            continue
        reference = manual_rrp(sku)
        if reference is None:
            box = box_rule(sku)
            if box:
                base, multiplier = box
                reference = rrp.get(base)
                reference = reference * multiplier if reference is not None else None
            else:
                canonical = WB_RRP_ALIASES.get(normalize_sku(sku), canonical_sku(sku))
                reference = rrp.get(normalize_sku(canonical))
        if reference is not None:
            reference_by_nm[nm] = {"sku": sku, "rrp": reference}
    historical_red, historical_yellow = [], []
    for nm, obs in observations.items():
        match = reference_by_nm.get(nm)
        if match is None:
            continue
        price = obs["buyer_order_price_excl_wallet"]
        entry = dict(obs, **match, floor=ceil(match["rrp"] * 1.05))
        if price < match["rrp"]:
            historical_red.append(entry)
        elif price < entry["floor"]:
            historical_yellow.append(entry)

    os.makedirs("artifacts", exist_ok=True)
    with open("artifacts/wb-unmatched-skus.json", "w", encoding="utf-8") as file:
        json.dump(missing, file, ensure_ascii=False, indent=2)
    with open("artifacts/wb-order-observations.json", "w", encoding="utf-8") as file:
        json.dump(list(observations.values()), file, ensure_ascii=False, indent=2)
    with open("artifacts/wb-price-summary.json", "w", encoding="utf-8") as file:
        json.dump({
            "market_region": "Москва",
            "stock_status": stock_status,
            "total_catalog": len(items),
            "positive_fbs_stock": len(active_items) if stock_status == "VERIFIED" else None,
            "unmatched_on_stock": len(missing) if stock_status == "VERIFIED" else None,
            "verified_buyer_prices": 0,
            "buyer_price_status": "UNAVAILABLE_NO_AUTHORIZED_SOURCE",
            "rrp_violations_confirmed": 0,
            "historical_orders_last_7_days": len(orders),
            "in_stock_products_with_order_price": len(observations),
            "historical_below_rrp": len(historical_red),
            "historical_below_rrp_plus_5": len(historical_yellow),
            "pricing_changes": False
        }, file, ensure_ascii=False, indent=2)
    lines = ["WB — контроль конечной цены покупателя, Москва",
             "Товаров в каталоге: " + str(len(items)),
             "Проверка остатков FBS: " + (
                 str(len(active_items)) + " товаров с остатком" if stock_status == "VERIFIED"
                 else "проверка недоступна"),
             "Заказы за 7 дней: " + str(len(orders)),
             "Товаров с остатком и ценой заказа: " + str(len(observations)),
             "По заказам ниже РРЦ: " + str(len(historical_red)),
             "По заказам ниже РРЦ+5%: " + str(len(historical_yellow)),
             "ВНИМАНИЕ: это исторические цены заказов, не текущая московская витрина.",
             "finishedPrice без отдельной скидки WB Кошелька.",
             "Конечная витринная цена: НЕ ПОЛУЧЕНА",
             "Достоверных сигналов нарушения РРЦ: нет данных",
             "Цены продавца / Клуба не подставляются вместо покупательской.",
             "Изменений цен: нет."]
    for heading, subset in (("Ниже РРЦ (по заказам)", historical_red),
                            ("Ниже РРЦ+5% (по заказам)", historical_yellow)):
        if subset:
            lines.extend(("", heading))
            for entry in sorted(subset, key=lambda v: v["order_date"], reverse=True)[:20]:
                lines.append("{} WB {}: {} ₽, РРЦ {} ₽, цель {} ₽, заказ {}".format(
                    entry["sku"], entry["nm_id"], entry["buyer_order_price_excl_wallet"],
                    entry["rrp"], entry["floor"], entry["order_date"][:16]))
    send_message(telegram, chat_id, "\n".join(lines))
    print("WB monitoring:", json.dumps({
        "catalog": len(items), "stock_status": stock_status,
        "in_stock": len(active_items) if stock_status == "VERIFIED" else None,
        "buyer_price_source": "NOT_CONNECTED", "buyer_prices_verified": 0
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
