"""Read public Yandex Market B2C pages and extract only the green Yandex Pay price.

The parser intentionally ignores ordinary prices, promo codes and personalized
discounts. A price is verified only when the public page explicitly labels it
as "Цена с картой Яндекс Пэй" (or a close spelling variant).

The storefront can show multiple sellers. We therefore prefer a green price
located immediately before our shop name. If seller attribution cannot be made
and several green prices exist, the result is UNVERIFIED rather than guessed.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

PAY_RE = re.compile(
    r"(?:Цена\s+с\s+картой\s+Яндекс\s*Пэй|"
    r"Цена\s+с\s+картой\s+Пэй|"
    r"С\s+картой\s+Яндекс\s*Пэй)"
    r"\s*([0-9][0-9\s\u00a0\u202f]*)(?:[,.][0-9]{1,2})?\s*₽",
    re.IGNORECASE,
)
SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.I | re.S)
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


@dataclass
class StorefrontPrice:
    price: float | None
    verified: bool
    status: str
    source_url: str
    match_rule: str = ""
    seller_name: str = ""


def with_region(url: str, region_id: int = 213) -> str:
    """Pin public-page checks to one Yandex region (213 = Moscow)."""
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["lr"] = str(int(region_id))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def visible_text(page_html: str) -> str:
    raw = SCRIPT_STYLE_RE.sub(" ", page_html)
    raw = TAG_RE.sub(" ", raw)
    raw = html.unescape(raw)
    raw = raw.replace("\u00a0", " ").replace("\u202f", " ")
    return SPACE_RE.sub(" ", raw).strip()


def _number(raw: str) -> float:
    clean = raw.replace(" ", "").replace("\u00a0", "").replace("\u202f", "")
    return float(clean.replace(",", "."))


def extract_pay_price(text: str, seller_names: list[str], source_url: str) -> StorefrontPrice:
    matches = [
        {"price": _number(m.group(1)), "start": m.start(), "end": m.end()}
        for m in PAY_RE.finditer(text)
    ]
    if not matches:
        return StorefrontPrice(None, False, "PAY_PRICE_NOT_FOUND", source_url)

    # Prefer the pay price belonging to our seller. On Yandex offer lists the
    # seller name normally follows the price block.
    seller_hits: list[tuple[int, str]] = []
    lower = text.casefold()
    for raw_name in seller_names:
        name = SPACE_RE.sub(" ", str(raw_name or "")).strip()
        if len(name) < 2:
            continue
        needle = name.casefold()
        start = 0
        while True:
            pos = lower.find(needle, start)
            if pos < 0:
                break
            seller_hits.append((pos, name))
            start = pos + max(1, len(needle))

    candidates: list[tuple[int, dict, str]] = []
    for seller_pos, seller_name in seller_hits:
        for match in matches:
            distance = seller_pos - match["end"]
            if 0 <= distance <= 1400:
                candidates.append((distance, match, seller_name))

    if candidates:
        candidates.sort(key=lambda x: x[0])
        _, best, seller_name = candidates[0]
        return StorefrontPrice(
            best["price"], True, "OK", source_url,
            match_rule="green_price_before_seller", seller_name=seller_name
        )

    unique_prices = sorted({m["price"] for m in matches})
    if len(unique_prices) == 1:
        return StorefrontPrice(
            unique_prices[0], True, "OK", source_url,
            match_rule="single_green_price_on_page"
        )

    return StorefrontPrice(
        None, False, "AMBIGUOUS_GREEN_PRICES", source_url,
        match_rule=f"{len(matches)} green prices; seller not identified"
    )


def fetch_pay_price(url: str, seller_names: list[str], *, region_id: int = 213,
                    session: requests.Session | None = None) -> StorefrontPrice:
    target = with_region(url, region_id)
    s = session or requests.Session()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/154.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Accept": "text/html,application/xhtml+xml",
    }
    try:
        response = s.get(target, headers=headers, timeout=35, allow_redirects=True)
    except requests.RequestException as exc:
        return StorefrontPrice(
            None, False, "HTTP_ERROR", target, match_rule=type(exc).__name__
        )
    if not response.ok:
        return StorefrontPrice(
            None, False, f"HTTP_{response.status_code}", response.url
        )
    text = visible_text(response.text)
    return extract_pay_price(text, seller_names, response.url)
