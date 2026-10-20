"""Read a Wildberries buyer price using a local real browser, read-only.

No WB seller token, paid services, or price writes are used.
Runs on the user's own computer, rather than GitHub-hosted IPs that WB blocks.

Usage:
    python -m wb_agent.browser_price_probe 1308438233
    python -m wb_agent.browser_price_probe 1308438233 --wallet-pct 3
"""
from __future__ import annotations

import argparse
import json
import os
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs

from .buyer_price import (
    BuyerPriceUnavailable,
    CARD_API,
    DEFAULT_DEST,
    WalletConfig,
    parse_card_payload,
)


def _extract_price(data: dict, article: int, wallet_pct: Decimal) -> dict | None:
    """Extract one exact product from a verified successful JSON response."""
    public_config = WalletConfig(Decimal("0"), Decimal("0"))
    public = parse_card_payload(data, public_config).get(article)
    if public is None:
        return None

    estimate_config = WalletConfig(wallet_pct, Decimal("15000"))
    estimated = parse_card_payload(data, estimate_config).get(article)
    if estimated is None:
        return None

    return {
        "article": article,
        "public_price_rub": public.public_price_rub,
        "wallet_price_estimate_rub": estimated.wallet_price_rub,
        "wallet_estimate_pct": str(wallet_pct),
        "wallet_price_is_estimate": True,
        "supplier_id": public.supplier_id,
        "option_id": public.option_id,
        "price_components_rub": {
            "product": public.product_price_rub,
            "logistics": public.logistics_rub,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Local WB public price reader for Moscow")
    parser.add_argument("article", type=int, help="WB nmID, e.g. 1308438233")
    parser.add_argument("--dest", default=os.getenv("WB_BUYER_DEST", DEFAULT_DEST),
                        help="WB buyer dest (Moscow is -1257786)")
    parser.add_argument("--wallet-pct", default="3",
                        help="Estimated WB Wallet discount %% (not verified live); default 3")
    parser.add_argument("--headless", action="store_true",
                        help="Hidden browser; try visible mode first")
    args = parser.parse_args()

    pct = Decimal(args.wallet_pct)
    if not (Decimal("0") <= pct < Decimal("100")):
        parser.error("--wallet-pct must be from 0 to less than 100")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        parser.error("Install browser driver: py -3 -m pip install playwright")

    article = args.article
    browser_url = f"https://www.wildberries.ru/catalog/{article}/detail.aspx"
    api_url = CARD_API + "?" + urlencode({
        "appType": 1, "curr": "rub", "lang": "ru", "dest": args.dest,
        "nm": article,
    })
    # Kept outside the repository: no browser profile/cookies ever committed.
    user_data = Path.home() / ".seller_wb_browser"
    user_data.mkdir(parents=True, exist_ok=True)

    diagnostics = []
    result = None
    source = None

    with sync_playwright() as pw:
        browser_args = dict(
            user_data_dir=str(user_data),
            headless=args.headless,
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            viewport={"width": 1440, "height": 900},
        )
        try:
            context = pw.chromium.launch_persistent_context(channel="chrome", **browser_args)
        except Exception:
            context = pw.chromium.launch_persistent_context(**browser_args)

        page = context.pages[0] if context.pages else context.new_page()

        # The real storefront may fetch product data through its own network requests.
        # Capture only public JSON product responses; never store cookies or headers.
        responses = []
        def collect(response):
            url = response.url
            if response.status != 200 or not (
                "/cards/v4/detail" in url or "/exactmatch/" in url
            ):
                return
            try:
                data = response.json()
                if isinstance(data, dict):
                    responses.append((url, data))
            except Exception:
                pass

        page.on("response", collect)
        try:
            page.goto(browser_url, wait_until="domcontentloaded", timeout=45000)
        except Exception as exc:
            diagnostics.append("Storefront: " + type(exc).__name__)

        try:
            page.wait_for_timeout(10000)
        except Exception:
            pass

        for url, data in responses:
            # Do not confuse the browser's delivery region with Moscow.
            card_dest = parse_qs(urlparse(url).query).get("dest", [None])[0]
            if card_dest != args.dest:
                continue
            try:
                result = _extract_price(data, article, pct)
                if result is not None:
                    source = "browser_network_response"
                    diagnostics.append("Source: " + url.split("?")[0])
                    break
            except BuyerPriceUnavailable as exc:
                diagnostics.append("Browser response schema: " + str(exc)[:120])

        # Use the same real browser session for a fresh, Moscow-dest card request.
        # A 403, challenge, or empty product response is NOT a price.
        if result is None:
            try:
                response_data = page.evaluate(
                    """async (url) => {
                        const controller = new AbortController();
                        const timer = setTimeout(() => controller.abort(), 20000);
                        try {
                            const response = await fetch(url, {
                                method: 'GET', credentials: 'omit',
                                signal: controller.signal
                            });
                            if (!response.ok) return {status: response.status, data: null};
                            return {status: response.status, data: await response.json()};
                        } finally {clearTimeout(timer);}
                    }""",
                    api_url,
                )
                diagnostics.append("Browser fetch HTTP " + str(response_data.get("status")))
                data = response_data.get("data")
                if isinstance(data, dict):
                    result = _extract_price(data, article, pct)
                    if result is not None:
                        source = "browser_fetch_moscow_dest"
            except Exception as exc:
                diagnostics.append("Browser fetch: " + type(exc).__name__ + ": " + str(exc)[:120])

        # Fallback: navigate to the JSON URL *in Chrome*, not from cloud curl.
        if result is None:
            try:
                api_page = context.new_page()
                api_response = api_page.goto(api_url, wait_until="domcontentloaded", timeout=25000)
                diagnostics.append("Browser direct API HTTP " + str(api_response.status if api_response else "unknown"))
                if api_response and api_response.status == 200:
                    data = json.loads(api_page.locator("body").inner_text(timeout=5000))
                    result = _extract_price(data, article, pct)
                    if result is not None:
                        source = "browser_direct_moscow_dest"
            except Exception as exc:
                diagnostics.append("Browser direct API: " + type(exc).__name__ + ": " + str(exc)[:120])

        context.close()

    payload = {
        "region": "Moscow" if args.dest == DEFAULT_DEST else "custom",
        "dest": args.dest,
        "price_confirmed": result is not None,
        "source": source,
        "result": result,
        "diagnostics": diagnostics,
    }
    output_path = Path("artifacts") / "wb-browser-price-probe.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print("Saved:", output_path.resolve())

    if result is None:
        raise SystemExit(
            "No verified product price received. No price changes made. "
            "Check browser challenge / region and diagnostics."
        )


if __name__ == "__main__":
    main()
