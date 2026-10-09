"""Official Yandex Market 'Prices' report: current 'На витрине' (onDisplay).

No storefront scraping/cookies, no price changes. Fail closed on unknown layouts.
Docs: https://yandex.ru/dev/market/partner-api/doc/ru/reference/reports/generateGoodsPricesReport
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import time
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import requests

from .client import YandexMarketClient
from .price_check import _catalog, classify, load_rrp, resolve_rrp
from ozon_agent.business_rules import is_clearance_sku
from ozon_agent.telegram import send_telegram
from .reporting import format_alerts, suggest_rrp


def number(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if value > 0 else None
    if not isinstance(value, str):
        return None
    normalized = re.sub(r"[\s\u00a0\u202f₽]", "", value)
    if re.fullmatch(r"\d+(?:[.,]\d{1,2})?", normalized):
        x = float(normalized.replace(",", "."))
        return x if x > 0 else None
    return None


def price_rows(document):
    """Yield only recognized SKU-bearing shop/business report records."""
    if isinstance(document, list):
        for value in document:
            yield from price_rows(value)
    elif isinstance(document, dict):
        if ("offerId" in document or "OFFER_ID" in document) and (
            "onDisplay" in document or "ON_DISPLAY" in document
        ):
            yield document
        else:
            for v in document.values():
                if isinstance(v, (dict, list)):
                    yield from price_rows(v)


def extract_report(content: bytes) -> list[dict]:
    """Parse the official ZIP containing report JSON files."""
    if not zipfile.is_zipfile(io.BytesIO(content)):
        raise ValueError("Yandex price report was not a ZIP file")
    data = []
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for name in archive.namelist():
            if not name.lower().endswith(".json") or name.startswith("__MACOSX"):
                continue
            if len(archive.read(name)) > 30_000_000:
                raise ValueError("Oversized JSON report")
            document = json.loads(archive.read(name).decode("utf-8-sig"))
            data.extend(price_rows(document))
    if not data:
        raise ValueError("No onDisplay/offerId rows found in official Yandex report")
    return data


def obtain_report(client: YandexMarketClient, business_id: int) -> list[dict]:
    response = client.request(
        "POST", "/v2/reports/goods-prices/generate",
        params={"format": "JSON"}, payload={"businessId": business_id},
    )
    result = response.get("result") or {}
    report_id = result.get("reportId")
    if not report_id:
        raise RuntimeError("Report generation returned no reportId: " + repr(result)[:300])
    for _ in range(30):
        info = client.request("GET", f"/v2/reports/info/{report_id}").get("result") or {}
        status = info.get("status")
        if status == "DONE":
            url = info.get("file")
            if not url or urlparse(url).scheme != "https":
                raise RuntimeError("Report download URL missing or not HTTPS")
            response = requests.get(url, timeout=90)
            response.raise_for_status()
            if len(response.content) > 80_000_000:
                raise RuntimeError("Yandex price report too large")
            return extract_report(response.content)
        if status == "FAILED":
            raise RuntimeError("Yandex report FAILED: " + str(info.get("subStatus")))
        time.sleep(5)
    raise TimeoutError(f"Report {report_id} not ready after 150 seconds")


def run() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="artifacts/yandex-official-prices.json")
    args = ap.parse_args()
    rrp_map = load_rrp()
    if not rrp_map:
        raise RuntimeError("RRP_MAP_B64 missing")
    client = YandexMarketClient()
    mappings, _, _ = _catalog(client)
    catalog_names = {(r["business_id"], str((r.get("offer") or {}).get("offerId") or "").strip()): str((r.get("offer") or {}).get("name") or "") for r in mappings}
    business_ids = sorted({row["business_id"] for row in mappings})
    results = []
    for business_id in business_ids:
        report = obtain_report(client, business_id)
        for item in report:
            offer_id = str(item.get("offerId") or item.get("OFFER_ID") or "").strip()
            if not offer_id or is_clearance_sku(offer_id):
                continue
            buyer_price = number(item.get("onDisplay") if "onDisplay" in item else item.get("ON_DISPLAY"))
            rrp, rule = resolve_rrp(offer_id, rrp_map)
            row = {"business_id": business_id, "offer_id": offer_id,
                   "display_price": buyer_price, "rrp": rrp, "rrp_rule": rule,
                   "name": catalog_names.get((business_id, offer_id), "")}
            if buyer_price is None:
                row["status"] = "PRICE_NOT_NUMERIC"
            elif rrp is None:
                row["status"] = "MISSING_RRP"
                row["rrp_suggestions"] = suggest_rrp(offer_id, rrp_map)
            else:
                row["status"], row["target_price"] = classify(buyer_price, rrp)
            results.append(row)
    stats = {key: sum(r["status"] == key for r in results) for key in (
        "RED", "YELLOW", "OK", "MISSING_RRP", "PRICE_NOT_NUMERIC")}
    stats["total"] = len(results)
    verified = stats["RED"] + stats["YELLOW"] + stats["OK"]
    stats["verified"] = verified
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"source": "official_yandex_goods_prices_report",
                                "stats": stats, "items": results},
                               ensure_ascii=False, indent=2), encoding="utf-8")
    missing = [r for r in results if r["status"] == "MISSING_RRP"]
    if missing:
        print("SKU MATCHING NEEDED (not sent to Telegram):")
        for row in sorted(missing, key=lambda r: r["offer_id"]):
            print(json.dumps({
                "offer_id": row["offer_id"],
                "name": row.get("name"),
                "candidates": row.get("rrp_suggestions", []),
            }, ensure_ascii=False))
    for message in format_alerts(results, stats):
        print(message)
        send_telegram(message)
    return 0 if verified else 2


if __name__ == "__main__":
    raise SystemExit(run())
