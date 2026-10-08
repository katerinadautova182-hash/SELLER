"""Temporary browser-based WB Russia pricing experiment. No seller tokens required."""
import json
import re
from pathlib import Path
from playwright.sync_api import sync_playwright

IDS = ("1308438233", "1438080038")
TARGETS = {"1308438233": 8658, "1438080038": 9716}
OUTPUT = Path("artifacts/wb-browser-controls.json")

def parse_prices(text):
    pattern = r"(?<!\\d)(\\d[\\d \\u00a0]{2,8})\\s*₽"
    prices = []
    for raw in re.findall(pattern, text):
        try:
            number = int(re.sub(r"[^0-9]", "", raw))
            if 100 <= number <= 300000:
                prices.append(number)
        except ValueError:
            pass
    return sorted(set(prices))

def main():
    OUTPUT.parent.mkdir(exist_ok=True)
    rows = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale="ru-RU", timezone_id="Europe/Moscow",
                                      viewport={"width": 1365, "height": 900})
        for nm in IDS:
            page = context.new_page()
            url = f"https://www.wildberries.ru/catalog/{nm}/detail.aspx"
            row = {"nmID": nm, "url": url, "expected_screenshot": TARGETS[nm],
                   "confirmed_buyer_price": None}
            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(6500)
                body = page.locator("body").inner_text(timeout=8000)
                row.update({"http_status": response.status if response else None,
                            "title": page.title()[:130], "currency_values_rub": parse_prices(body)[:40],
                            "price_matches_screenshot": TARGETS[nm] in parse_prices(body),
                            "body_length": len(body)})
                # Do not label free-text matches as checkout prices.
            except Exception as exc:
                row["error"] = type(exc).__name__ + ": " + str(exc)[:140]
            finally:
                page.close()
            rows.append(row)
        browser.close()
    OUTPUT.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    for r in rows:
        print(json.dumps(r, ensure_ascii=False))

if __name__ == "__main__":
    main()
