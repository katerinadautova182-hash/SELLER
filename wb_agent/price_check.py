"""Read-only Wildberries seller price audit.

Uses only the official Prices and Discounts API. Never modifies prices.
WB seller/club discounted prices are NOT guaranteed buyer storefront prices.
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from math import ceil

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


def normalize(value):
    return "".join(str(value).upper().split())


def load_rrp():
    raw = os.getenv("RRP_MAP_B64", "")
    if not raw:
        return {}
    result = json.loads(base64.b64decode(raw).decode("utf-8"))
    if not isinstance(result, dict):
        raise ValueError("RRP_MAP_B64 must encode a JSON dictionary")
    return {normalize(k): float(v) for k, v in result.items() if v not in (None, "")}


def analyze(items, rrp):
    red, yellow = [], []
    missing, unpriced = [], []
    for item in items:
        sku = str(item.get("vendorCode", "")).strip()
        nm_id = str(item.get("nmID", "")).strip()
        if "УЦ" in normalize(sku):
            continue
        reference = rrp.get(normalize(sku))
        if reference is None:
            missing.append(sku or nm_id)
            continue
        sizes = item.get("sizes") or []
        for size in sizes:
            price = size.get("discountedPrice")
            if price is None or float(price) <= 0:
                unpriced.append(sku or nm_id)
                continue
            price = float(price)
            entry = {"sku": sku, "nm_id": nm_id, "size": size.get("techSizeName", ""),
                     "price": price, "rrp": reference, "floor": ceil(reference * 1.05)}
            if price < reference:
                red.append(entry)
            elif price < entry["floor"]:
                yellow.append(entry)
    return red, yellow, missing, unpriced


def format_report(items, red, yellow, missing, unpriced):
    lines = ["WB — проверка цен продавца (без изменений)",
             "Товаров в API: " + str(len(items)),
             "Ниже РРЦ: " + str(len(red)),
             "Между РРЦ и РРЦ + 5%: " + str(len(yellow)),
             "Без сопоставления РРЦ: " + str(len(missing)),
             "Без цены: " + str(len(unpriced)),
             "",
             "Важно: цена после скидки продавца, не персональная цена покупателя."]
    for title, rows in (("Ниже РРЦ", red), ("Ниже РРЦ + 5%", yellow)):
        if rows:
            lines.extend(("", title))
            for row in rows[:30]:
                lines.append("{} (WB {}): {} ₽ / РРЦ {} ₽ / цель {} ₽".format(
                    row["sku"], row["nm_id"], row["price"], row["rrp"], row["floor"]))
            if len(rows) > 30:
                lines.append("... и ещё " + str(len(rows) - 30))
    if missing:
        lines.extend(("", "Нет РРЦ для первых 15 SKU: " + ", ".join(missing[:15])))
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
    red, yellow, missing, unpriced = analyze(items, rrp)
    report = format_report(items, red, yellow, missing, unpriced)
    print("WB catalog rows:", len(items), "RRP mapped:", len(rrp),
          "red:", len(red), "yellow:", len(yellow), "unmapped:", len(missing))
    if not rrp:
        report += "\n\nRRP_MAP_B64 is missing — threshold audit not performed."
    send_message(telegram, chat_id, report)
    os.makedirs("artifacts", exist_ok=True)
    with open("artifacts/wb-price-summary.json", "w", encoding="utf-8") as file:
        json.dump({"total": len(items), "red": len(red), "yellow": len(yellow),
                   "no_rrp": len(missing), "no_price": len(unpriced)}, file, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
