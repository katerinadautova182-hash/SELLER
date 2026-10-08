"""Ozon analytics quantity layer.

Fetch delivered and returned units by Ozon SKU for a date range.
This is intentionally separate from finance/accrual because accrual rows do not
provide a reliable net unit count for COGS.
"""
from __future__ import annotations

from collections import defaultdict


def fetch_units_by_sku(client, date_from: str, date_to: str, *, limit: int = 1000) -> dict[str, dict]:
    metrics = ["delivered_units", "returns"]
    offset = 0
    out: dict[str, dict] = {}
    while True:
        payload = {
            "date_from": date_from,
            "date_to": date_to,
            "metrics": metrics,
            "dimension": ["sku"],
            "filters": [],
            "sort": [{"key": "delivered_units", "order": "DESC"}],
            "limit": limit,
            "offset": offset,
        }
        data = client.post("/v1/analytics/data", payload)
        rows = ((data.get("result") or {}).get("data") or [])
        for row in rows:
            dims = row.get("dimensions") or []
            vals = row.get("metrics") or []
            if not dims:
                continue
            sku = str(dims[0].get("id") or "").strip()
            if not sku:
                continue
            delivered = float(vals[0] or 0) if len(vals) > 0 else 0.0
            returned = float(vals[1] or 0) if len(vals) > 1 else 0.0
            out[sku] = {
                "delivered_units": delivered,
                "returned_units": returned,
                "net_units": delivered - returned,
            }
        if len(rows) < limit:
            break
        offset += limit
    return out
