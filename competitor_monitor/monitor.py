"""Read-only public-site JRL monitor. No seller-site changes, authentication or bypasses."""
from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup, NavigableString
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ozon_agent.telegram import send_telegram
from .reference_rrp import validate_rrp_reference

PRICE_RE = re.compile(r"(?<!\d)(\d{1,3}(?:[\s\u00a0\u202f.]?\d{3})+(?:[.,]\d{1,2})?|\d{3,6}(?:[.,]\d{1,2})?)\s*(?:₽|руб(?:\.|\b)|р\.)", re.I)
OLD_MARKERS = ("old-price", "price-old", "old_price", "price_old", "oldprice",
               "price--old", "price__old", "was-price", "price-was")
CARD_SELECTORS = (
    '[itemtype*="Product"], [itemtype*="product"], .product-card, .product-item, '
    '.product-thumb, .product-layout, .product-preview, .products__item, '
    '.catalog-item, .catalog__item, .js-store-prod, .t-store__card, '
    'li.product, article.product, .product__item, .t-store__prod-snippet'
)
OUT_OF_STOCK = re.compile(r"нет в наличии|отсутствует|распродан|sold out|out of stock", re.I)
BUNDLE = re.compile(r"(?i)набор|комплект|комбо|collection|машинка\s*\+\s*триммер|"
                    r"машинка\s+и\s+триммер|с\s+зарядной\s+базой|"
                    r"\+\s*(?:зарядная|подарок|wahl|stylecraft|шейвер|триммер)|"
                    r"\bX[12]\b")
TITLE_SKU = re.compile(r"(?:JRL|ДИАМАНТЕ|DIAMANTE|FRESHFADE|ONYX|FORTE|2020[CТT]|3000C)", re.I)
COLOR_SKUS = {
    "3000C": {"BLACK": "3000CB-F", "BLUE": "3000CD-F",
               "RED": "3000CE-F", "WHITE": "3000CW-T"},
    "2025C": {"BLACK": "2025CB", "ORANGE": "2025CH", "YELLOW": "2025CY"},
    "2025T": {"BLACK": "2025TB", "ORANGE": "2025TH"},
}
COLOR_WORDS = {"BLACK": ("BLACK", "ЧЕРН"), "BLUE": ("BLUE", "СИН"),
               "RED": ("RED", "КРАСН"), "WHITE": ("WHITE", "БЕЛ"),
               "ORANGE": ("ORANGE", "ОРАНЖ"), "YELLOW": ("YELLOW", "ЖЕЛ", "ЖЁЛ")}


def clean_code(s: str) -> str:
    """Normalize sku punctuation, spaces and Cyrillic lookalikes in model codes."""
    s = str(s).upper().translate(str.maketrans({
        "С": "C", "В": "B", "Т": "T", "Н": "H", "О": "O", "А": "A",
        "Х": "X", "Е": "E", "К": "K", "М": "M", "Р": "P", "У": "Y",
    }))
    return re.sub(r"[^A-Z0-9]", "", s)


def load_rrp(path: str | Path = "competitor_monitor/rrp_jrl.csv") -> dict[str, dict]:
    """Read approved JRL retail prices from the repository CSV (never purchase costs)."""
    result = {}
    with open(path, encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            sku = str(row.get("sku") or "").strip()
            key = clean_code(sku)
            try:
                value = float(str(row.get("rrp") or "").replace(" ", "").replace(",", "."))
            except ValueError as exc:
                raise RuntimeError(f"Invalid JRL RRP for {sku}") from exc
            if not sku or not key or value <= 0 or key in result:
                raise RuntimeError(f"Invalid or duplicate JRL SKU: {sku}")
            result[key] = {"sku": sku, "rrp": value}
    if not result:
        raise RuntimeError("Файл РРЦ JRL пустой")
    return result

# Product family is not the same as a spare part referencing that model.
# Guard against e.g. "Зарядная подставка для JRL 2020C" matching 2020C.
ACCESSORY_WORDS = re.compile(
    r"зарядн\w*\s*(?:баз|подстав|станц|блок|устройств)|"
    r"док[-\s]?станц|"
    r"(?:ножев\w*\s+блок|сменн\w*\s+нож|нож\s+для|лезви\w*\s+для)|"
    r"(?:магнитн\w*\s+)?насад\w*\s+(?:для|к|на)\s+машин|"
    r"набор\s+(?:двойн\w*\s+)?(?:магнитн\w*\s+)?насад|"
    r"подставк\w*\s+для\s+(?:машин|триммер)|"
    r"(?:чехол|коврик|держател|сумка)\s+для\s+(?:машин|триммер)|"
    r"(?:батаре|аккумулятор)\s+для\s+(?:машин|триммер)|"
    r"(?:фейд|фейдовый|fade|standard|taper)\s+(?:нож|лезви|blade)|"
    r"(?:нож|лезви|blade)\s+(?:fade|standard|taper|2020C|2020T)",
    re.I,
)
MAIN_MACHINE_CODE = re.compile(
    r"^(?:2020C|2020T|2025C|2025T|3000C|FR10|SH2301|GH2024|ONYX2024|2020C2020T)"
)
BUNDLE_CODES = ("GH2024", "ONYX2024", "2020C2020T")


BLADE_SKU = re.compile(r"^(?:BF0?[1-9]|SF\d{2}|SB\d{2})(?:[A-Z0-9]*)$")
BLADE_TITLE = re.compile(
    r"(?:ножев\w*\s+блок|сменн\w*\s+нож|(?:нож|лезвие|лезви\w*)\s+для|"
    r"лезви\w*\s+(?:jrl|для)|\b(?:blade|cutting\s+blade|fade\s+blade|"
    r"taper\s+blade|standard\s+blade|replacement\s+blade)\b|"
    r"нож\s+(?:bf\d{2}|sf\d{2}))", re.I
)
NON_PRODUCT_HEADING = re.compile(r"^\s*(?:ПОДОЙД[ЕЁ]Т|ПОДХОДИТ|СОВМЕСТИМ|АРТИКУЛ|ХАРАКТЕРИСТИКИ|КОМПЛЕКТАЦИЯ|ОПИСАНИЕ|ДЛЯ МОДЕЛЕЙ|МОДЕЛИ:)", re.I)
PART_SUBJECT = re.compile(r"^\s*(?:jrl[\s:-]*)?(?:лезви\w*|ножев\w*|нож\b|блок\b|запчаст|насадк|blade\b)", re.I)
DEVICE_TITLE = re.compile(r"\b(?:машинка|clipper|триммер|trimmer|"
                          r"шейвер|shaver|электробритва)\b", re.I)
COMB_TITLE = re.compile(r"(?:расч[её]ск|насад\w*|гребень|comb|guard)", re.I)


def product_type(sku: str) -> str:
    """RRP SKU *role*; never treat a compatible accessory as the device itself."""
    code = clean_code(sku)
    if BLADE_SKU.match(code):
        return "blade"
    if code.startswith(("2020C", "2025C", "3000C", "FR1040", "FR1050", "FR1090")):
        if code == "2020C2020T":
            return "kit"
        return "clipper"
    if code.startswith(("2020T", "2025T")):
        return "trimmer"
    if code.startswith(("GH2024", "ONYX2024")):
        return "kit"
    if code.startswith("SH2301"):
        return "shaver"
    if code.startswith(("CY2025", "CYCLONE", "FP2401", "FEATHER", "PHANTOM")):
        return "dryer"
    if code.startswith(("GUARD", "JNC", "JCA")):
        return "comb"
    if code in ("JRLC1", "JRLC2"):
        return "charger"
    return "other"


def is_compatible(name: str, sku: str) -> bool:
    """Enforce commodity type as well as SKU, rejecting cross-category references.

    'Нож для машинки JRL 2020C' must not be reported as machine 2020C;
    'Машинка JRL ... с ножом BF03' must not become the replacement blade BF03.
    """
    code = clean_code(sku)
    title = str(name)
    kind = product_type(code)
    if NON_PRODUCT_HEADING.search(title):
        return False
    title_starts_as_device = bool(re.match(r"^\s*(?:jrl\s+)?(?:машинк|триммер|шейвер|фен|clipper|trimmer)\w*", title, re.I))
    blade_item = bool(BLADE_TITLE.search(title))
    accessory_item = bool(ACCESSORY_WORDS.search(title) or blade_item or PART_SUBJECT.search(title))
    if kind in ("clipper", "trimmer", "shaver", "dryer") and accessory_item and not title_starts_as_device:
        return False
    # Clipper + trimmer kit is not a blade, spare part or a mere mention
    # of the devices it fits.
    if kind == "kit":
        if accessory_item:
            return False
        if code == "2020C2020T":
            return bool(re.search(r"(?:машинк\w*\s*(?:\+|и|и\s*триммер|с)\s*триммер|"
                                  r"комплект\w*\s+(?:машинк|триммер)|"
                                  r"набор\w*\s+(?:машинк|триммер))",
                                  title, re.I))
        return not bool(re.search(r"зарядн\w*|насадк\w*|ножев\w*\s+блок", title, re.I))
    if kind == "blade":
        if not blade_item or title_starts_as_device:
            return False
        # The model code can coexist in a compatibility description, but BF03
        # is considered a blade ONLY if the advertised item is a blade.
        return True
    if kind == "comb" and not COMB_TITLE.search(title):
        return False
    if kind in ("clipper", "trimmer", "shaver", "dryer"):
        if kind == "dryer" and re.search(r"ножев\w*\s+блок|для\s+фен", title, re.I):
            return False
    return True


ACCESSORY_SLUG = re.compile(
    r"(?:nabor[-_]?dvojnyh[-_]?magnitnyh[-_]?nasadok|"
    r"zaryadn|zarjadn|zaryadk|zaryadka|podstavk|nasadok|nasadki|"
    r"nozhevoj[-_]?blok|nozhevoy[-_]?blok|blade[-_]?for|"
    r"blade[-_]?2020|blade|fade[-_]?2020|nozhi/|aksessuari/|accessories/|accessory/)",
    re.I,
)


def url_compatible(url: str, sku: str) -> bool:
    """Main devices cannot be priced from a URL explicitly listing a spare part."""
    return not (
        MAIN_MACHINE_CODE.match(clean_code(sku))
        and ACCESSORY_SLUG.search(urlparse(url).path)
    )


def match_sku(name: str, rrp: dict[str, dict]) -> dict | None:
    original = str(name).upper()
    normalized = original.translate(str.maketrans({
        "С": "C", "В": "B", "Т": "T", "Н": "H", "О": "O",
        "А": "A", "Х": "X", "Е": "E", "К": "K", "М": "M", "Р": "P", "У": "Y"
    }))
    # Match only complete codes, preserving model delimiters in the source title.
    # Pure string compression would lose word boundaries (e.g. FreshFade2020C).
    def code_pattern(raw_sku: str) -> str:
        normalized_sku = str(raw_sku).upper().translate(str.maketrans({
            "С": "C", "В": "B", "Т": "T", "Н": "H", "О": "O",
            "А": "A", "Х": "X", "Е": "E", "К": "K", "М": "M", "Р": "P", "У": "Y"
        }))
        chunks = re.findall(r"[A-Z0-9]+", normalized_sku)
        return r"[-\s_/]*".join(re.escape(part) for part in chunks)

    is_bundle = bool(BUNDLE.search(original))
    for sku in sorted(rrp, key=len, reverse=True):
        item = rrp[sku]
        if len(sku) < 4:
            continue
        if is_bundle and not (sku.startswith("GH2024") or sku in ("2020C2020T",)):
            continue
        pattern = (r"(?<![A-Z0-9])(?:FF)?" + code_pattern(item["sku"]) +
                   r"(?![-A-Z0-9])")
        if re.search(pattern, normalized) and is_compatible(original, item["sku"]):
            return item
    if is_bundle:
        return None

    squashed = clean_code(normalized)
    # Catalog titles sometimes spell the family + color but omit exact color SKU.
    for family, colors in COLOR_SKUS.items():
        if family not in squashed and not (family == "2025C" and "DIAMANTE" in normalized and
                                             re.search(r"\b2025C\b", normalized)):
            continue
        for color, code in colors.items():
            if clean_code(code) not in rrp:
                continue
            if (any(word in original or word in normalized for word in COLOR_WORDS[color])
                    and is_compatible(original, code)):
                return rrp[clean_code(code)]
    if re.search(r"(?<![A-Z0-9])1040(?![A-Z0-9])", squashed) and "FR1040" in rrp and is_compatible(original, "FR1040"):
        return rrp["FR1040"]
    return None


def current_prices(node) -> list[float]:
    """Read actually displayed prices, excluding crossed-out and 'old' price blocks."""
    fragment = BeautifulSoup(str(node), "html.parser")
    for tag in list(fragment.select("s, strike, del, [style*='line-through']")):
        tag.decompose()
    for tag in list(fragment.find_all(True)):
        attributes = " ".join(tag.get("class") or []) + " " + str(tag.get("id") or "")
        if any(marker in attributes.lower() for marker in OLD_MARKERS):
            tag.decompose()
    text = fragment.get_text(" ", strip=True).replace("\u202f", " ")
    amounts = []
    for match in PRICE_RE.finditer(text):
        token = match.group(1).replace(" ", "").replace("\xa0", "").replace("\u202f", "")
        if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", token):
            token = token.replace(".", "")
        amount = float(token.replace(",", "."))
        if 80 <= amount <= 500000:
            amounts.append(amount)
    return amounts


def clean_title(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "")).strip()[:240]


def is_unavailable(text: str) -> bool:
    return bool(OUT_OF_STOCK.search(text[:600]))


def internal_url(base: str, href: str | None) -> str | None:
    if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
        return None
    target = urldefrag(urljoin(base, href))[0]
    a, b = urlparse(base), urlparse(target)
    if a.netloc.lower().removeprefix("www.") != b.netloc.lower().removeprefix("www."):
        return None
    if b.scheme not in ("http", "https"):
        return None
    if re.search(r"\.(?:pdf|jpg|jpeg|png|gif|webp|svg|zip|js|css)(?:\?|$)", b.path, re.I):
        return None
    return target.rstrip("/") or target


def name_and_link(node, page_url: str) -> tuple[str, str]:
    # Headings and model-bearing links are more reliable than paragraphs of descriptions.
    candidates = node.select("h1,h2,h3,h4,h5,[itemprop='name'],.product-title,.product__title,.t-name,.t-title,[class*='title'],b,strong,a")
    for element in candidates:
        title = clean_title(element.get_text(" ", strip=True))
        if TITLE_SKU.search(title) and 6 <= len(title) <= 180:
            link = internal_url(page_url, element.get("href"))
            if link is None:
                for a in node.find_all("a", href=True):
                    link = internal_url(page_url, a.get("href"))
                    if link and link != page_url:
                        break
            return title, link or page_url
    for piece in node.stripped_strings:
        title = clean_title(str(piece))
        if TITLE_SKU.search(title) and 6 <= len(title) <= 180:
            link = page_url
            for a in node.find_all("a", href=True):
                resolved = internal_url(page_url, a.get("href"))
                if resolved and resolved != page_url:
                    link = resolved
                    break
            return title, link
    return "", page_url


def candidate(node, page_url: str, rrp: dict, source: str):
    name, url = name_and_link(node, page_url)
    if not name:
        return None
    sku = match_sku(name, rrp)
    if sku and not url_compatible(url, sku["sku"]):
        sku = None
    price_list = current_prices(node)
    if not price_list:
        return {"sku": sku["sku"] if sku else "", "name": name, "url": url, "price": None,
                "rrp": sku["rrp"] if sku else None, "status": "NO_PRICE", "source": source}
    if is_unavailable(node.get_text(" ", strip=True)):
        return {"sku": sku["sku"] if sku else "", "name": name, "url": url, "price": None,
                "rrp": sku["rrp"] if sku else None, "status": "OUT_OF_STOCK", "source": source}
    if len(set(price_list)) > 1:
        # Variant selectors may show multiple simultaneously active choices.
        return {"sku": sku["sku"] if sku else "", "name": name, "url": url, "price": None,
                "rrp": sku["rrp"] if sku else None, "status": "MULTIPLE_PRICES", "source": source}
    return {"sku": sku["sku"] if sku else "", "name": name, "url": url,
            "price": price_list[0], "rrp": sku["rrp"] if sku else None,
            "status": "MATCHED" if sku else "UNMATCHED_SKU", "source": source}


def jsonld_records(soup, url: str, rrp: dict) -> list[dict]:
    results = []
    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(tag.string or tag.get_text())
        except (TypeError, ValueError):
            continue
        stack = [data]
        while stack:
            obj = stack.pop()
            if isinstance(obj, list):
                stack.extend(obj)
                continue
            if not isinstance(obj, dict):
                continue
            stack.extend(obj.get("@graph", []) if isinstance(obj.get("@graph"), list) else [])
            types = obj.get("@type", [])
            types = types if isinstance(types, list) else [types]
            if not any(str(t).lower() == "product" for t in types):
                continue
            name = clean_title(str(obj.get("name") or ""))
            if not name:
                continue
            sku = match_sku(" ".join([name, str(obj.get("sku") or "")]), rrp)
            offers = obj.get("offers") or {}
            offers = offers if isinstance(offers, list) else [offers]
            for offer in offers:
                if not isinstance(offer, dict):
                    continue
                raw_price = offer.get("price")
                try:
                    price = float(str(raw_price).replace(" ", "").replace(",", "."))
                except (ValueError, TypeError):
                    continue
                if not 80 <= price <= 500000:
                    continue
                avail = str(offer.get("availability") or "").lower()
                status = ("OUT_OF_STOCK" if any(w in avail for w in ("outofstock", "soldout"))
                          else "MATCHED" if sku else "UNMATCHED_SKU")
                product_link = internal_url(url, str(obj.get("url") or "")) or url
                if sku and not url_compatible(product_link, sku["sku"]):
                    sku = None
                    if status == "MATCHED":
                        status = "UNMATCHED_SKU"
                results.append({"sku": sku["sku"] if sku else "", "name": name,
                                "url": product_link, "price": None if status == "OUT_OF_STOCK" else price,
                                "rrp": sku["rrp"] if sku else None,
                                "status": status, "source": "jsonld"})
    return results


def extract(soup: BeautifulSoup, url: str, rrp: dict) -> tuple[list[dict], list[str]]:
    results = jsonld_records(soup, url, rrp)
    page_h1 = soup.find("h1")
    heading = clean_title(page_h1.get_text(" ", strip=True)) if page_h1 else ""
    # Detail pages may have links to related machines and accessories. The
    # current page H1 is authoritative for rejecting accessory-vs-main false hits.
    page_is_accessory = bool(heading and ACCESSORY_WORDS.search(heading))

    # Site cards are parsed independently: NEVER take the first price on an entire page.
    for card in soup.select(CARD_SELECTORS):
        text = card.get_text(" ", strip=True)
        if 15 < len(text) < 1800 and len(PRICE_RE.findall(text)) <= 4:
            row = candidate(card, url, rrp, "card")
            if row:
                results.append(row)

    # Fallback for bespoke landing pages (e.g. barberkit / jrl-pro):
    # find a model-bearing link/heading and its smallest priced parent.
    anchors = soup.select("h2,h3,h4,h5,a")
    for anchor in anchors:
        raw = clean_title(anchor.get_text(" ", strip=True))
        if not TITLE_SKU.search(raw) or not 5 < len(raw) < 180:
            continue
        for parent in list(anchor.parents)[:5]:
            if parent.name in ("html", "body", "header", "nav", "footer"):
                break
            text = parent.get_text(" ", strip=True)
            if len(text) > 1400:
                break
            if len(text) >= len(raw) and PRICE_RE.search(text):
                row = candidate(parent, url, rrp, "near-title")
                if row:
                    results.append(row)
                break

    # Plain div-based cards also occur on custom storefronts. Locate a price text
    # and walk upward only until a single product title can be resolved.
    for text_node in soup.find_all(string=PRICE_RE):
        parent = text_node.parent
        if parent is None or any(a.name in ("s", "strike", "del") for a in [parent, *list(parent.parents)[:3]]):
            continue
        for block in [parent, *list(parent.parents)[:7]]:
            if block.name in ("body", "html", "header", "footer", "nav"):
                break
            block_text = block.get_text(" ", strip=True)
            if len(block_text) > 1400:
                break
            if TITLE_SKU.search(block_text):
                row = candidate(block, url, rrp, "near-price")
                if row:
                    results.append(row)
                break

    # Product detail when a single h1 is an actual JRL product.
    h1 = soup.find("h1")
    if h1 and TITLE_SKU.search(h1.get_text(" ", strip=True)):
        for css in (".product-info", ".product-detail", ".product__details",
                    ".t-store__prod-popup", "[itemtype*='Product']", "main"):
            nodes = soup.select(css)
            for node in nodes[:2]:
                if len(node.get_text(" ", strip=True)) > 4000:
                    continue
                row = candidate(node, url, rrp, "detail")
                if row:
                    results.append(row)
                break
            if nodes:
                break

    # Crawl only plausible product pages from the catalog, not arbitrary site links.
    links = []
    for anchor in soup.find_all("a", href=True):
        title = clean_title(anchor.get_text(" ", strip=True))
        link = internal_url(url, anchor.get("href"))
        if not link or link == url:
            continue
        if TITLE_SKU.search(title):
            links.append(link)
        elif re.search(r"подробнее|смотреть|купить|detail", title, re.I):
            for parent in list(anchor.parents)[:3]:
                if TITLE_SKU.search(parent.get_text(" ", strip=True)[:500]):
                    links.append(link)
                    break
    if page_is_accessory or ACCESSORY_SLUG.search(urlparse(url).path):
        results = [r for r in results if not
                   (r.get("sku") and MAIN_MACHINE_CODE.match(clean_code(r["sku"])))]
    # Product link can be more specific than its parent catalogue card title.
    for row in results:
        if row.get("sku") and not url_compatible(row.get("url", url), row["sku"]):
            row.update(sku="", rrp=None, status="UNMATCHED_SKU")
    return results, list(dict.fromkeys(links))


def fetch_html(session: requests.Session, url: str) -> BeautifulSoup:
    resp = session.get(url, timeout=(8, 22))
    resp.raise_for_status()
    if "html" not in resp.headers.get("Content-Type", "").lower():
        raise ValueError(f"Не HTML: {url}")
    if len(resp.content) > 6_000_000:
        raise ValueError(f"Очень большая страница: {url}")
    # Normal shop scripts may mention reCAPTCHA without blocking shopping.
    # A genuine anti-bot challenge has a focused short HTML page or challenge title.
    title_node = BeautifulSoup(resp.content[:25000], "html.parser").title
    title_text = title_node.get_text(" ", strip=True) if title_node else ""
    if (
        re.search(r"access denied|проверка браузера|подтвердите.*робот", title_text, re.I)
        or (
            len(resp.content) < 12000
            and re.search(r"капча|captcha|checking your browser|verify you are human", resp.text[:8000], re.I)
        )
    ):
        raise ValueError(f"Антибот или CAPTCHA: {url}")
    return BeautifulSoup(resp.content, "html.parser")


def session_with_retries() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; JRLPriceAudit/1.0; +public-price-monitor)",
                            "Accept-Language": "ru-RU,ru;q=0.9"})
    retries = Retry(total=2, backoff_factor=1.0,
                    status_forcelist=[429, 500, 502, 503, 504],
                    allowed_methods=["GET"], respect_retry_after_header=True)
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


def scan_site(site: dict, rrp: dict, delay: float = 0.25) -> dict:
    from .crawler import crawl
    return crawl(site, rrp, delay)


def fmt(amount: float) -> str:
    return f"{amount:,.0f}".replace(",", " ")


def product_lines(row: dict, *, include_link: bool = True) -> list[str]:
    """Product name, price on competitor site, official RRP and difference."""
    name = clean_title(str(row.get("name") or row.get("sku") or "Без названия"))
    if len(name) > 120:
        name = name[:117].rstrip() + "..."
    sku = str(row.get("sku") or "").strip()
    price, rrp = float(row["price"]), float(row["rrp"])
    delta = round(price - rrp, 2)
    label = f"• {name}" + (f" — арт. {sku}" if sku else "")
    lines = [label, f"Цена на сайте: {fmt(price)} ₽",
             f"РРЦ: {fmt(rrp)} ₽"]
    if delta < 0:
        lines.append(f"Ниже РРЦ на {fmt(-delta)} ₽")
    elif delta > 0:
        lines.append(f"Выше РРЦ на {fmt(delta)} ₽")
    else:
        lines.append("Цена равна РРЦ")
    if include_link and row.get("url"):
        lines.append(row["url"])
    return lines


def format_report(report: dict, *, max_chars: int | None = 3850,
                  include_all_links: bool = True,
                  only_violations: bool = True) -> str:
    """Manager-facing: only reliable prices, no debug diagnostics.

    The full audit stays in CSV/JSON and per-site text attachments.
    Never silently truncate an item's displayed price, name or URL.
    """
    items = [r for r in (report.get("items") or [])
             if r.get("sku") and r.get("price") is not None and r.get("rrp") is not None]
    below = sorted(
        [r for r in items if float(r["price"]) < float(r["rrp"])],
        key=lambda r: (float(r["price"]) - float(r["rrp"]), str(r["sku"])),
    )
    above = sorted(
        [r for r in items if float(r["price"]) >= float(r["rrp"])],
        key=lambda r: str(r["sku"]),
    )
    domain = urlparse(report["url"]).netloc.removeprefix("www.")
    owner = str(report.get("legal_entity") or "Юридическое лицо не установлено")
    if not report.get("verified", True):
        owner += " (реквизиты требуют подтверждения)"
    coverage = report.get("coverage", "INCOMPLETE")
    complete = coverage == "DISCOVERED_CATALOG_COMPLETE"
    lines = [f"{domain} — {owner}",
             f"Контроль РРЦ JRL | статус: {'ОБОЙДЕН ВЕСЬ ДОСТУПНЫЙ КАТАЛОГ' if complete else 'ПРОВЕРКА НЕПОЛНА'}",
             f"Сопоставлено товаров: {len(items)} | Ниже РРЦ: {len(below)}",
             f"Проверено страниц: {report.get('pages_ok', 0)}/{report.get('pages_attempted', 0)}"]
    if not items:
        lines += ["", "ВНИМАНИЕ: проверка НЕ завершена. Нет подтверждённых цен. Вывод о соблюдении РРЦ невозможен."]
        return "\n".join(lines)
    if not below:
        lines += ["", "По найденным товарам цен ниже РРЦ не выявлено." if complete
                  else "Проверка не завершена — отсутствие нарушений не подтверждено."]
    if not complete:
        lines += ["", "Данные предварительные: не все объявления/страницы/цены подтверждены."]

    sections = [("ТОВАРЫ НИЖЕ РРЦ", below)]
    if not only_violations:
        sections.append(("ОСТАЛЬНЫЕ ПРОВЕРЕННЫЕ ТОВАРЫ", above))
    omitted = 0
    reserved = 90
    for title, section in sections:
        heading_written = False
        for row in section:
            details = product_lines(row, include_link=include_all_links)
            addition = (["", title] if not heading_written else [""]) + details
            proposed = "\n".join(lines + addition)
            if max_chars is not None and len(proposed) + reserved > max_chars:
                omitted += 1
                continue
            lines.extend(addition)
            heading_written = True

    if omitted:
        lines += ["", f"Остальные позиции: {omitted} — в полном отчёте."]
    result = "\n".join(lines)
    if max_chars is not None and len(result) > max_chars:
        raise ValueError("Нельзя отправить сообщение целиком: превышен лимит Telegram")
    return result


def save_reports(folder: Path, reports: list[dict]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "jrl_monitor.json").write_text(json.dumps(reports, ensure_ascii=False, indent=2),
                                              encoding="utf-8")
    for report in reports:
        slug = re.sub(r"[^a-z0-9]+", "-", str(report["site"]).lower()).strip("-") or "site"
        (folder / f"{slug}.txt").write_text(
            format_report(report, max_chars=None, include_all_links=True,
                          only_violations=False) + "\n", encoding="utf-8",
        )
    fields = ["site", "sku", "name", "price", "rrp", "delta", "difference_pct",
              "url", "status", "source", "price_conflict", "all_observed_prices"]
    with (folder / "jrl_monitor.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for report in reports:
            for r in report["items"] + report["unresolved"]:
                writer.writerow({**{k: "" for k in fields}, **{
                    "site": report["site"], **{k: v for k, v in r.items() if k in fields},
                    "all_observed_prices": ",".join(map(str, r.get("all_observed_prices", [])))
                }})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sites", default="competitor_monitor/sites.json")
    ap.add_argument("--site", action="append", default=[], help="Select a store domain; can repeat for multiple stores")
    ap.add_argument("--rrp", default="competitor_monitor/rrp_jrl.csv")
    ap.add_argument("--out", default="artifacts/jrl-competitors")
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--delay", type=float, default=0.8)
    ap.add_argument("--max-products", type=int, default=None, help="Override per-site crawl limit for smoke tests")
    args = ap.parse_args()
    rrp = load_rrp(args.rrp)
    validate_rrp_reference(rrp)
    config = json.loads(Path(args.sites).read_text(encoding="utf-8"))
    selected_sites = [site for site in config["sites"] if site.get("enabled", True)]
    if args.site:
        domains = {x.casefold().removeprefix("www.") for x in args.site}
        selected_sites = [site for site in selected_sites
                          if (site.get("name", "").casefold() in domains
                              or urlparse(site["url"]).netloc.casefold().removeprefix("www.") in domains)]
        if len(selected_sites) != len(domains):
            raise ValueError(f"Не все сайты найдены: {args.site}; найдено {len(selected_sites)}")
    reports = []
    for site in selected_sites:
        try:
            entry = {**site, "max_product_pages": args.max_products} if args.max_products is not None else site
            result = scan_site(entry, rrp, args.delay)
        except Exception as exc:
            print(f"ОШИБКА СКАНЕРА {site['name']}: {type(exc).__name__}: {exc}", flush=True)
            result = {"coverage": "INCOMPLETE", "is_complete": False,
                      "site": site.get("name", site["url"]), "url": site["url"],
                      "legal_entity": site.get("legal_entity"), "inn": site.get("inn"),
                      "verified": site.get("verified", False),
                      "items": [], "violations": [], "unresolved": [],
                      "pages_ok": 0, "pages_attempted": 0, "errors": [repr(exc)]}
        reports.append(result)
        if result.get("errors"):
            print(f"ДИАГНОСТИКА {site['name']}: "
                  + " | ".join(result["errors"][:18]), flush=True)
        message = format_report(result)
        print(message, "\n" + "-" * 50, flush=True)
        # Exactly one Telegram text message per configured site, including empty/error results.
        if not args.no_telegram:
            if result.get("coverage") != "DISCOVERED_CATALOG_COMPLETE":
                print("Отчёт НЕ отправлен менеджерам: обход неполный.", flush=True)
            elif not send_telegram(message):
                raise RuntimeError("Telegram не настроен или отправка не удалась")
    save_reports(Path(args.out), reports)
    incomplete = [r["site"] for r in reports if not r["items"]]
    if incomplete:
        print("Не удалось подтвердить цены в: " + ", ".join(incomplete), flush=True)
    return 1 if any(r.get("coverage") != "DISCOVERED_CATALOG_COMPLETE" for r in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
