"""Статистика продаж за последние 12 месяцев по SKU: v1/analytics/data.

Запрашивается помесячно (а не одним диапазоном на год) — это надёжнее:
у метода есть ограничения на объём выборки за один вызов, а помесячная
разбивка полезна и как самостоятельный отчёт.
"""
from __future__ import annotations

import datetime as dt

from .client import OzonClient, OzonApiError
from .writer import write_table

METRICS = ["revenue", "ordered_units"]

MONTH_HEADERS = [
    ("sku", "SKU"),
    ("name", "Название товара"),
    ("period", "Месяц"),
    ("revenue", "Выручка, ₽"),
    ("ordered_units", "Продано, шт."),
    ("avg_check", "Средний чек, ₽"),
]

TOTAL_HEADERS = [
    ("sku", "SKU"),
    ("name", "Название товара"),
    ("revenue_12m", "Выручка за 12 мес., ₽"),
    ("ordered_units_12m", "Продано за 12 мес., шт."),
    ("avg_check_12m", "Средний чек за 12 мес., ₽"),
]


def _month_ranges(months: int = 12) -> list[tuple[str, str, str]]:
    today = dt.date.today()
    first_of_this_month = today.replace(day=1)
    ranges = []
    y, m = first_of_this_month.year, first_of_this_month.month
    for _ in range(months):
        start = dt.date(y, m, 1)
        end = min(
            (dt.date(y + (m == 12), (m % 12) + 1, 1) - dt.timedelta(days=1)),
            today,
        )
        ranges.append((start.isoformat(), end.isoformat(), start.strftime("%Y-%m")))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return list(reversed(ranges))


def _fetch_month(client: OzonClient, date_from: str, date_to: str) -> list[dict]:
    rows = []
    offset = 0
    limit = 1000
    while True:
        body = {
            "date_from": date_from,
            "date_to": date_to,
            "metrics": METRICS,
            "dimension": ["sku"],
            "filters": [],
            "sort": [{"key": "revenue", "order": "DESC"}],
            "limit": limit,
            "offset": offset,
        }
        try:
            data = client.post("/v1/analytics/data", body, allow_statuses=(403, 404))
        except OzonApiError as exc:
            print(f"  !! analytics/data ошибка за {date_from}..{date_to}: {exc}")
            return rows

        if data.get("_status_code") in (403, 404):
            print(f"  !! analytics/data недоступен (403/404) за {date_from}..{date_to} — тариф/доступ ограничивают метод.")
            return rows

        chunk = data.get("result", {}).get("data", [])
        if not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < limit:
            break
        offset += limit
    return rows


def run(client: OzonClient) -> None:
    print("[5/6] Статистика продаж за 12 месяцев (analytics/data, помесячно)...")
    month_rows, totals = [], {}

    for date_from, date_to, label in _month_ranges(12):
        chunk = _fetch_month(client, date_from, date_to)
        print(f"  {label}: {len(chunk)} SKU")
        for row in chunk:
            dims = row.get("dimensions", [])
            sku = dims[0].get("id", "") if dims else ""
            name = dims[0].get("name", "") if dims else ""
            metrics = row.get("metrics", [])
            revenue = metrics[0] if len(metrics) > 0 else 0
            units = metrics[1] if len(metrics) > 1 else 0
            avg_check = round(revenue / units, 2) if units else ""
            month_rows.append(
                {"sku": sku, "name": name, "period": label, "revenue": revenue, "ordered_units": units, "avg_check": avg_check}
            )
            t = totals.setdefault(sku, {"sku": sku, "name": name, "revenue_12m": 0, "ordered_units_12m": 0})
            t["revenue_12m"] += revenue
            t["ordered_units_12m"] += units
            if name:
                t["name"] = name

    for t in totals.values():
        t["avg_check_12m"] = round(t["revenue_12m"] / t["ordered_units_12m"], 2) if t["ordered_units_12m"] else ""

    write_table("8_prodazhi_po_mesyatsam", MONTH_HEADERS, month_rows)
    write_table("9_prodazhi_itogo_12mes", TOTAL_HEADERS, list(totals.values()))
