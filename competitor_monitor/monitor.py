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

PRICE_RE = re.compile(r"(?<!\d)(\d{1,3}(?:[\s\u00a0\u202f]?\d{3})+(?:[.,]\d{1,2})?|\d{3,6}(?:[.,]\d{1,2})?)\s*(?:₽|руб(?:\.|\b)|р\.)", re.I)
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


def load_rrp() -> dict[str, dict]:
    raw = os.getenv("RRP_MAP_B64", "").strip()
    if not raw:
        raise RuntimeError("Не настроен секрет RRP_MAP_B64; проверку не запускаем.")
    try:
        data = json.loads(base64.b64decode(raw, validate=True).decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise RuntimeError("RRP_MAP_B64 не содержит корректный base64 JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("РРЦ должен быть JSON-словарём SKU -> число")
    out = {}
    for raw_sku, raw_price in data.items():
        try:
            price = float(str(raw_price).replace(" ", "").replace(",", "."))
        except (ValueError, TypeError):
            continue
        if price <= 0:
            continue
        sku = clean_code(raw_sku)
        if sku and sku not in out:
            out[sku] = {"sku": str(raw_sku), "rrp": price}
    if not out:
        raise RuntimeError("RRP_MAP_B64 пустой или не содержит числовых РРЦ.")
    return out


def match_sku(name: str, rrp: dict[str, dict]) -> dict | None:
    normalized = str(name).upper().translate(str.maketrans({
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

    is_bundle = bool(BUNDLE.search(normalized))
    for sku in sorted(rrp, key=len, reverse=True):
        item = rrp[sku]
        if len(sku) < 4:
            continue
        if is_bundle and not (sku.startswith("GH2024") or sku in ("2020C2020T",)):
            continue
        pattern = (r"(?<![A-Z0-9])(?:FF)?" + code_pattern(item["sku"]) +
                   r"(?![-A-Z0-9])")
        if re.search(pattern, normalized):
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
            if code not in rrp:
                continue
            if any(word in normalized for word in COLOR_WORDS[color]):
                return rrp[code]
    if re.search(r"(?<![A-Z0-9])1040(?![A-Z0-9])", squashed) and "FR1040" in rrp:
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
        amount = float(match.group(1).replace(" ", "").replace("\xa0", "").replace(",", "."))
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
    candidates = node.select("h1,h2,h3,h4,h5,[itemprop='name'],.product-title,.product__title,a")
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
    return "", page_url


def candidate(node, page_url: str, rrp: dict, source: str):
    name, url = name_and_link(node, page_url)
    if not name:
        return None
    sku = match_sku(name, rrp)
    price_list = current_prices(node)
    if not price_list:
        return {"sku": sku["sku"] if sku else "", "name": name, "url": url, "price": None,
                "rrp": sku["rrp"] if sku else None, "status": "NO_PRICE", "source": source}
    if is_unavailable(node.get_text(" ", strip=True)):
        return {"sku": sku["sku"] if sku else "", "name": name, "url": url, "price": None,
                "rrp": sku["rrp"] if sku else None, "status": "OUT_OF_STOCK", "source": source}
    if len(set(price_list)) > 1 and source == "card":
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
                results.append({"sku": sku["sku"] if sku else "", "name": name,
                                "url": product_link, "price": None if status == "OUT_OF_STOCK" else price,
                                "rrp": sku["rrp"] if sku else None,
                                "status": status, "source": "jsonld"})
    return results


def extract(soup: BeautifulSoup, url: str, rrp: dict) -> tuple[list[dict], list[str]]:
    results = jsonld_records(soup, url, rrp)
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
    return results, list(dict.fromkeys(links))


def fetch_html(session: requests.Session, url: str) -> BeautifulSoup:
    resp = session.get(url, timeout=(8, 22))
    resp.raise_for_status()
    if "html" not in resp.headers.get("Content-Type", "").lower():
        raise ValueError(f"Не HTML: {url}")
    if len(resp.content) > 6_000_000:
        raise ValueError(f"Очень большая страница: {url}")
    if re.search(r"captcha|проверка браузера|access denied|подтвердите.*робот", resp.text[:8000], re.I):
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


def scan_site(site: dict, rrp: dict, delay: float = 0.8) -> dict:
    base = site["url"]
    categories = [internal_url(base, path) for path in site["categories"]]
    categories = [u for u in dict.fromkeys(categories) if u]
    session = session_with_retries()
    records, errors, follow_urls, pages_ok = [], [], [], 0
    visited = set()
    for url in categories:
        try:
            soup = fetch_html(session, url)
            pages_ok += 1
            found, links = extract(soup, url, rrp)
            records.extend(found)
            follow_urls.extend(links)
        except (requests.RequestException, ValueError) as ex:
            errors.append(f"{url}: {str(ex)[:150]}")
        visited.add(url)
        time.sleep(delay)
    follow_urls = list(dict.fromkeys(u for u in follow_urls if u not in visited))
    for url in follow_urls[:int(site.get("max_product_pages", 60))]:
        try:
            soup = fetch_html(session, url)
            pages_ok += 1
            found, _ = extract(soup, url, rrp)
            records.extend(found)
        except (requests.RequestException, ValueError) as ex:
            errors.append(f"{url}: {str(ex)[:150]}")
        visited.add(url)
        time.sleep(delay)

    # One entry per SKU with the lowest reliably visible current price.
    # Keep different advertised prices (catalogue/detail) in the audit.
    matched = defaultdict(list)
    unresolved = []
    for record in records:
        if record["status"] == "MATCHED" and record["price"] is not None:
            matched[record["sku"]].append(record)
        elif record["status"] != "MATCHED":
            unresolved.append(record)
    rows = []
    for sku, options in matched.items():
        options.sort(key=lambda x: (x["price"], x["url"]))
        item = dict(options[0])
        prices = sorted(set(o["price"] for o in options))
        item["all_observed_prices"] = prices
        item["price_conflict"] = len(prices) > 1
        item["delta"] = round(item["price"] - item["rrp"], 2)
        item["difference_pct"] = round((item["price"] / item["rrp"] - 1) * 100, 1)
        rows.append(item)
    rows.sort(key=lambda x: (x["delta"], x["sku"]))
    return {"site": site["name"], "url": base,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "pages_ok": pages_ok, "pages_attempted": len(visited), "errors": errors,
            "items": rows, "unresolved": unresolved,
            "violations": [r for r in rows if r["delta"] < -0.005]}


def fmt(amount: float) -> str:
    return f"{amount:,.0f}".replace(",", " ")


def format_report(report: dict) -> str:
    v = report["violations"]
    name = report["site"]
    lines = [f"JRL — {name}", f"Сайт: {report['url']}",
             f"Товаров с подтверждённым SKU и ценой: {len(report['items'])}",
             f"Ниже РРЦ: {len(v)}",
             f"Проверено страниц: {report['pages_ok']}/{report['pages_attempted']}"]
    if report["errors"]:
        lines.append(f"Ошибки/недоступные страницы: {len(report['errors'])}")
    conflicts = sum(bool(r["price_conflict"]) for r in report["items"])
    if conflicts:
        lines.append(f"Расхождения между страницами: {conflicts} (в отчёте минимальная цена)")
    if v:
        lines.append("")
        lines.append("ПОЗИЦИИ НИЖЕ РРЦ:")
        for row in v[:18]:
            lines.append(f"• {row['sku']}: {fmt(row['price'])} ₽ / РРЦ {fmt(row['rrp'])} ₽ "
                         f"(−{fmt(-row['delta'])} ₽, {row['difference_pct']:.1f}%)")
            lines.append(row["url"])
        if len(v) > 18:
            lines.append(f"Ещё {len(v)-18} позиций — в CSV отчёте GitHub Actions.")
    else:
        if report["pages_ok"] == 0 or not report["items"]:
            lines.append("")
            lines.append("ВНИМАНИЕ: проверка НЕ завершена. Нет подтверждённых сопоставлений, "
                         "нельзя делать вывод об отсутствии нарушений.")
        else:
            lines.append("")
            lines.append("По сопоставленным товарам цен ниже РРЦ не обнаружено.")
    if report["unresolved"]:
        lines.append(f"Без надёжного сопоставления/цены: {len(report['unresolved'])} "
                     f"наблюдений (CSV/JSON в артефактах).")
    if report["errors"]:
        lines.append(f"Пример ошибки: {report['errors'][0][:160]}")
    msg = "\n".join(lines)
    return msg[:3850]


def save_reports(folder: Path, reports: list[dict]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "jrl_monitor.json").write_text(json.dumps(reports, ensure_ascii=False, indent=2),
                                              encoding="utf-8")
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
    ap.add_argument("--out", default="artifacts/jrl-competitors")
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--delay", type=float, default=0.8)
    args = ap.parse_args()
    rrp = load_rrp()
    config = json.loads(Path(args.sites).read_text(encoding="utf-8"))
    reports = []
    for site in config["sites"]:
        try:
            result = scan_site(site, rrp, args.delay)
        except Exception as exc:
            result = {"site": site.get("name", site["url"]), "url": site["url"],
                      "items": [], "violations": [], "unresolved": [],
                      "pages_ok": 0, "pages_attempted": 0, "errors": [repr(exc)]}
        reports.append(result)
        message = format_report(result)
        print(message, "\n" + "-" * 50, flush=True)
        # Exactly one Telegram text message per configured site, including empty/error results.
        if not args.no_telegram:
            if not send_telegram(message):
                print("Telegram не настроен; сообщение не отправлено.", flush=True)
    save_reports(Path(args.out), reports)
    return 1 if any(r["pages_ok"] == 0 or not r["items"] for r in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
