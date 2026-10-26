"""Catalog-wide JRL discovery with pagination, sitemap supplementation, and auditable coverage.

A successful HTTP response is not evidence of a complete catalog. The result has
a coverage status and separate URLs/reasons for all crawl or matching gaps.
No anti-bot evasion, login, headless scraping, or proprietary API usage.
"""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
from time import monotonic, sleep
from urllib.parse import urlparse, urljoin, parse_qsl, urlencode, urlunparse
import gzip
import re
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup


NEXT_PAGE = re.compile(
    r"^(?:next|далее|следующ\w*|впер[её]д|>)$|^[0-9]{1,4}$", re.I
)
PAGE_KEYS = {"page", "p", "pagen_1", "pagen_2", "start", "offset", "page_num"}
PAGINATION_CLASS = re.compile(r"pagin|pages|paging|navigation|page-numbers", re.I)
SKIP_URL = re.compile(
    r"(?:/cart|/basket|/checkout|/login|/register|/account|/review|/privacy|"
    r"/policy|/delivery|/shipping|/payment|/contacts|/about|/compare|/search|"
    r"/wishlist|/favorites|/blog|/article|/news|/brands/$)", re.I
)
SITEMAP_HINT = re.compile(
    r"(?:jrl|freshfade|diamante|onyx|gh2024|202[05][ct]|3000c|"
    r"fr[-_]?10(?:40|50|90)|sf\d{2}|bf\d{2}|sh2301|cy2025|"
    r"jp[a-z]\d{2,}|jpp\d{2,})", re.I
)
PRODUCT_LINK_HINT = re.compile(r"(?:jrl|джей.?ар.?эл|fresh.?fade|diamante|onyx|"
                               r"машинк|триммер|шейвер|ножев|фен|плойк)", re.I)


def canon(url: str) -> str:
    """Stable deduplication without removing route/product_id/page query parameters."""
    p = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not (k.lower().startswith("utm_") or k.lower() in
                     {"yclid", "gclid", "fbclid", "src", "iid", "from", "ref"})]
    return urlunparse((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/") or "/",
                       "", urlencode(sorted(query)), ""))


def same_site(link: str, base: str) -> bool:
    a = urlparse(link).netloc.lower().removeprefix("www.")
    b = urlparse(base).netloc.lower().removeprefix("www.")
    return a == b and urlparse(link).scheme in ("http", "https")


def pagination_links(soup: BeautifulSoup, page_url: str, site_root: str) -> set[str]:
    """Read next/all page links, not only first listing page."""
    found: set[str] = set()
    from .monitor import internal_url
    for a in soup.find_all("a", href=True):
        href = a.get("href")
        full = internal_url(site_root, urljoin(page_url, href))
        if not full:
            continue
        tagtext = a.get_text(" ", strip=True)
        parent = a.parent
        attrs = " ".join(str(v) for v in
                         ((parent.get("class", []) if parent else []) +
                          a.get("class", [])))
        nav = bool(PAGINATION_CLASS.search(attrs) or
                   a.find_parent(attrs={"class": PAGINATION_CLASS}) or
                   a.find_parent("nav", attrs={"aria-label": re.compile("pag|страниц", re.I)}))
        rel = a.get("rel") or []
        if isinstance(rel, str):
            rel = rel.split()
        explicit = "next" in rel or "prev" in rel
        # Restrict page-number links to pagination widgets or same-route page params.
        q = dict((k.lower(), v) for k, v in parse_qsl(urlparse(full).query))
        page_params = any(k in PAGE_KEYS for k in q)
        url_form = re.search(r"(?:/page/[0-9]+/?|/page-[0-9]+/?|/page[0-9]+/?)(?:\?|$)", full, re.I)
        if explicit or ((nav or page_params or url_form) and
                        (NEXT_PAGE.match(tagtext) or page_params or url_form)):
            # Pagination widgets sometimes include advertisements/other categories;
            # don't cross to a different category just because a digit appears.
            current = urlparse(page_url)
            target = urlparse(full)
            same_route = current.path.rstrip("/") == target.path.rstrip("/")
            route_paged = (same_route or bool(url_form) or
                           (current.path in target.path and bool(nav)))
            if route_paged and same_site(full, site_root) and not SKIP_URL.search(full):
                found.add(canon(full))
    for link in soup.find_all("link", rel=True, href=True):
        rels = link.get("rel") or []
        if "next" in rels:
            full = internal_url(site_root, urljoin(page_url, link["href"]))
            if full:
                found.add(canon(full))
    return found


def sitemap_candidates(session: requests.Session, base: str, timeout: float,
                       *, xml_limit: int = 80, item_limit: int = 150000) -> tuple[set[str], list[str]]:
    """Supplement product URLs; sitemap enumeration must not assume every URL is JRL."""
    root = base.rstrip("/")
    sitemap_queue = deque([root + "/sitemap.xml", root + "/sitemap_index.xml"])
    errors: list[str] = []
    urls: set[str] = set()
    tried: set[str] = set()
    try:
        resp = session.get(root + "/robots.txt", timeout=(5, timeout))
        if resp.ok:
            for m in re.findall(r"^\s*Sitemap:\s*(\S+)\s*$", resp.text, re.I | re.M):
                if same_site(m, base):
                    sitemap_queue.append(m)
    except requests.RequestException:
        pass
    while sitemap_queue and len(tried) < xml_limit and len(urls) < item_limit:
        page = sitemap_queue.popleft()
        if page in tried or not same_site(page, base):
            continue
        tried.add(page)
        try:
            resp = session.get(page, timeout=(5, timeout))
            resp.raise_for_status()
            if len(resp.content) > 18_000_000:
                errors.append(f"Большой sitemap: {page}")
                continue
            body = gzip.decompress(resp.content) if page.endswith(".gz") else resp.content
            root_xml = ET.fromstring(body)
        except (requests.RequestException, OSError, ET.ParseError, ValueError):
            continue  # many shops have no sitemap; catalog may still be crawlable
        is_index = root_xml.tag.lower().endswith("sitemapindex")
        for loc in root_xml.iter():
            if not loc.tag.lower().endswith("loc") or not loc.text:
                continue
            link = loc.text.strip()
            if not same_site(link, base):
                continue
            if is_index:
                if len(sitemap_queue) + len(tried) < xml_limit * 3:
                    sitemap_queue.append(link)
            elif SITEMAP_HINT.search(urlparse(link).path):
                urls.add(canon(link))
                if len(urls) >= item_limit:
                    errors.append("Достигнут предел URL sitemap; проверка неполная.")
                    break
    if sitemap_queue and len(tried) >= xml_limit:
        errors.append(f"Обойдены не все sitemap-файлы: лимит {xml_limit}")
    return urls, errors


def crawl(site: dict, rrp: dict, delay: float = 0.25) -> dict:
    """Walk every discovered category/pagination/product URL, recording incomplete coverage."""
    from .monitor import (
        extract, fetch_html, internal_url, session_with_retries, TITLE_SKU,
        PRICE_RE,
    )

    base = site["url"]
    start = monotonic()
    deadline = float(site.get("max_duration_seconds", 600))
    max_catalog = int(site.get("max_catalog_pages", 900))
    max_product = int(site.get("max_product_pages", 2500))
    session = session_with_retries()
    records: list[dict] = []
    errors: list[str] = []
    catalog_q = deque()
    products: set[str] = set()
    visited_catalog: set[str] = set()
    visited_products: set[str] = set()
    catalog_seen: set[str] = set()
    truncated = False
    pages_ok = 0

    def queue_catalog(link: str):
        target = internal_url(base, link)
        if target is not None:
            normalized = canon(target)
            if normalized not in catalog_seen:
                catalog_seen.add(normalized)
                catalog_q.append(normalized)

    for category in site.get("categories") or ["/"]:
        queue_catalog(urljoin(base, category))

    # Avito exposes user listings via JavaScript and may return access challenges;
    # sitemap is for the whole Avito platform, not the single chosen seller.
    is_avito = site.get("platform") == "avito"
    if not is_avito:
        sitemap, issues = sitemap_candidates(session, base, 15.0)
        errors.extend(issues)
        products.update(sitemap)

    while catalog_q:
        if monotonic() - start >= deadline or len(visited_catalog) >= max_catalog:
            truncated = True
            break
        url = catalog_q.popleft()
        if url in visited_catalog:
            continue
        visited_catalog.add(url)
        try:
            soup = fetch_html(session, url)
            pages_ok += 1
            found, links = extract(soup, url, rrp)
            records.extend(found)
            for link in links:
                if same_site(link, base):
                    products.add(canon(link))
            # A card might contain model info even if the <a> has only "Подробнее".
            for item in found:
                if item.get("sku") and item.get("url") != url and same_site(item["url"], base):
                    products.add(canon(item["url"]))
            for next_page in pagination_links(soup, url, base):
                queue_catalog(next_page)
            # If no product-link extractor sees an accessory without an exact
            # model number, include title-based JRL links where they look like product cards.
            for link in soup.find_all("a", href=True):
                title = link.get_text(" ", strip=True)
                if not PRODUCT_LINK_HINT.search(title) or len(title) > 190:
                    continue
                parent = link.find_parent(class_=re.compile(
                    r"product|catalog-item|item-card|t-store__card", re.I))
                if parent is None and not TITLE_SKU.search(title):
                    continue
                url2 = internal_url(base, urljoin(url, link["href"]))
                if url2 and url2 != url and not SKIP_URL.search(url2):
                    products.add(canon(url2))
        except (requests.RequestException, ValueError) as ex:
            errors.append(f"{url}: {str(ex)[:180]}")
        if delay:
            sleep(delay)

    # Never count a category/listing page as product detail.
    products.difference_update(visited_catalog)
    product_q = deque(sorted(products))
    while product_q:
        if monotonic() - start >= deadline or len(visited_products) >= max_product:
            truncated = True
            break
        url = product_q.popleft()
        if url in visited_products or url in visited_catalog:
            continue
        visited_products.add(url)
        try:
            soup = fetch_html(session, url)
            pages_ok += 1
            # Restrict Avito seller: no proof the listing belongs to the saved
            # seller => no managerial price alert, even if the title says JRL.
            if is_avito:
                seller_id = site.get("seller_id", "")
                html = str(soup)
                if seller_id and seller_id not in html:
                    errors.append(f"{url}: принадлежность продавцу не подтверждена")
                    continue
            found, _ = extract(soup, url, rrp)
            records.extend(found)
        except (requests.RequestException, ValueError) as ex:
            errors.append(f"{url}: {str(ex)[:180]}")
        if delay:
            sleep(delay)

    matched: defaultdict[str, list[dict]] = defaultdict(list)
    unresolved = []
    for record in records:
        if record["status"] == "MATCHED" and record["price"] is not None:
            matched[record["sku"]].append(record)
        elif record["status"] != "MATCHED":
            unresolved.append(record)
    rows = []
    for sku, observations in matched.items():
        observations.sort(key=lambda x: (x["price"], x["url"]))
        item = dict(observations[0])
        prices = sorted(set(o["price"] for o in observations))
        item["all_observed_prices"] = prices
        item["price_conflict"] = len(prices) > 1
        item["delta"] = round(item["price"] - item["rrp"], 2)
        item["difference_pct"] = round((item["price"] / item["rrp"] - 1) * 100, 1)
        rows.append(item)
    rows.sort(key=lambda x: (x["delta"], x["sku"]))

    if truncated:
        errors.append(
            "Не все страницы обойдены (лимит времени/количества): "
            f"оставшиеся разделы={len(catalog_q)}, товарные URL={len(product_q)}"
        )
    if is_avito:
        # Seller-only completeness is not demonstrable from public Avito HTML.
        # Even a nonempty response is NOT a complete scrape of seller inventory.
        errors.append("Авито: полнота объявлений продавца не подтверждена официальным источником.")
    if not rows:
        errors.append("Не найдено надёжно сопоставленных товарных цен JRL.")
    unresolved_price_urls = {
        r.get("url") for r in unresolved if r.get("status") in
        ("NO_PRICE", "MULTIPLE_PRICES") and r.get("sku")
    }
    if unresolved_price_urls:
        errors.append(
            f"Неопределённая цена у {len(unresolved_price_urls)} карточек JRL"
        )

    # A successful crawl means all *discovered* URLs succeeded. This is not a
    # certification that there are no unindexed or JS-hidden products.
    completed = not errors and bool(rows)
    return {
        "site": site["name"], "url": base,
        "legal_entity": site.get("legal_entity"), "inn": site.get("inn"),
        "verified": site.get("verified", False),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "pages_ok": pages_ok,
        "pages_attempted": len(visited_catalog) + len(visited_products),
        "catalog_pages": len(visited_catalog),
        "product_pages": len(visited_products),
        "discovered_products": len(products),
        "products_pending": len(product_q),
        "catalog_pages_pending": len(catalog_q),
        "coverage": "DISCOVERED_CATALOG_COMPLETE" if completed else "INCOMPLETE",
        "is_complete": completed,
        "errors": errors,
        "items": rows,
        "unresolved": unresolved,
        "violations": [r for r in rows if r["delta"] < -0.005],
    }
