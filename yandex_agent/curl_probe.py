"""Diagnostic: try curl_cffi Chrome TLS impersonation for a handful of Yandex B2C cards.

Adapted transport idea from https://github.com/Geekyup/Parser-Yandex-Market
Optional securely configured cookies; no price updates and NO claim that
a price belongs to our seller until independently verified.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

from curl_cffi import requests as curl_requests

from .client import YandexMarketClient
from .price_check import _catalog, _b2c_url
from .storefront import visible_text, extract_pay_price, with_region

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9",
}


def load_cookies(raw: str) -> dict[str, str]:
    if not raw.strip():
        return {}
    data = json.loads(raw)
    if isinstance(data, dict):
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
            raise ValueError("Cookie dictionary requires string keys and values")
        return data
    if isinstance(data, list):
        result = {}
        for item in data:
            if not isinstance(item, dict):
                raise ValueError("Cookie list entries must be objects")
            domain = str(item.get("domain") or "").lstrip(".")
            if domain and not (domain == "yandex.ru" or domain.endswith(".yandex.ru")):
                continue
            name, value = item.get("name"), item.get("value")
            if isinstance(name, str) and isinstance(value, str):
                result[name] = value
        return result
    raise ValueError("Cookies must be a JSON object or array")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-products", type=int, default=5)
    ap.add_argument("--output", default="artifacts/yandex-curl-probe.json")
    args = ap.parse_args()

    if not 1 <= args.max_products <= 10:
        raise ValueError("Diagnostic probe is limited to 1..10 cards")
    mappings, seller_names, _ = _catalog(YandexMarketClient())
    rows = []
    checked_urls = set()
    cookies = load_cookies(os.getenv('YANDEX_MARKET_COOKIES_JSON', ''))
    print('Cookies configured:', bool(cookies))

    with curl_requests.Session(impersonate="chrome110", timeout=30) as session:
        if cookies:
            session.cookies.update(cookies)
        for mapping in mappings:
            if len(rows) >= args.max_products:
                break
            offer = mapping.get("offer") or {}
            if offer.get("archived"):
                continue
            url = _b2c_url(mapping)
            if not url or url in checked_urls:
                continue
            checked_urls.add(url)
            target = with_region(url, 213)
            row = {
                "offer_id": offer.get("offerId"), "url": target,
                "http_status": None, "pay_price": None,
                "verified": False, "price_status": "NOT_FETCHED",
            }
            try:
                response = session.get(target, headers=HEADERS, timeout=30)
                row["http_status"] = response.status_code
                final_path = urlsplit(str(response.url)).path
                row["final_path"] = final_path
                row["bytes_received"] = len(response.content)
                if "showcaptcha" in final_path.lower():
                    row["price_status"] = "CAPTCHA"
                elif response.status_code == 200:
                    # Never save storefront HTML or cookies in public artifacts.
                    text = visible_text(response.text)
                    result = extract_pay_price(text, seller_names, str(response.url))
                    row["pay_price"] = result.price
                    row["price_status"] = result.status
                    row["match_rule"] = result.match_rule
                    # Even if there is a unique Pay price on the page, do not
                    # assert seller ownership without a reliable seller match.
                    row["verified"] = bool(
                        result.verified and result.match_rule == "green_price_before_seller"
                    )
                else:
                    row["price_status"] = f"HTTP_{response.status_code}"
            except Exception as exc:
                row["price_status"] = f"EXCEPTION_{type(exc).__name__}"
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False))
            time.sleep(1)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "tested": len(rows),
        "http_200": sum(x["http_status"] == 200 for x in rows),
        "http_403": sum(x["http_status"] == 403 for x in rows),
        "captcha": sum(x["price_status"] == "CAPTCHA" for x in rows),
        "seller_attributed_pay_price": sum(bool(x["verified"]) for x in rows),
        "items": rows,
        "disclaimer": "Diagnostic only; no RRP conclusions, no price changes."
    }
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("SUMMARY " + json.dumps({k:v for k,v in summary.items() if k!="items"}, ensure_ascii=False))
    return 0 if summary["seller_attributed_pay_price"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
