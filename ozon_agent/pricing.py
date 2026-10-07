"""Read current Ozon price economics and evaluate SKU guardrails.

Read-only: this module never mutates marketplace prices.
"""
from __future__ import annotations

import time
from dataclasses import asdict
from math import ceil
from typing import Iterable, TYPE_CHECKING

if TYPE_CHECKING:
    from ozon_export.client import OzonClient
from .economics import EconomicsInput, calculate_economics, RRP_FLOOR_FACTOR


def fetch_price_rows(client: "OzonClient", product_ids: Iterable[int]) -> list[dict]:
    ids = list(product_ids)
    out: list[dict] = []
    for start in range(0, len(ids), 1000):
        batch = ids[start:start + 1000]
        data = client.post(
            "/v5/product/info/prices",
            {"filter": {"product_id": batch, "visibility": "ALL"}, "limit": 1000},
        )
        out.extend(data.get("items", []))
    return out


def normalize_price_item(item: dict) -> dict:
    price = item.get("price") or {}
    commissions = item.get("commissions") or {}

    commission_fbo = float(commissions.get("sales_percent_fbo") or 0)
    commission_fbs = float(commissions.get("sales_percent_fbs") or 0)
    commission_percent = commission_fbo or commission_fbs

    logistics_fbo = float(commissions.get("fbo_direct_flow_trans_min_amount") or 0)
    logistics_fbs = float(commissions.get("fbs_direct_flow_trans_min_amount") or 0)
    logistics_rub = logistics_fbo or logistics_fbs

    return {
        "product_id": item.get("product_id"),
        "offer_id": item.get("offer_id"),
        "current_price": float(price.get("price") or 0),
        "marketing_price": float(price.get("marketing_seller_price") or 0),
        "ozon_min_price": float(price.get("min_price") or 0),
        "commission_percent": commission_percent,
        "logistics_rub": logistics_rub,
        "acquiring_rub": float(item.get("acquiring") or 0),
    }


def evaluate_sku(*, ozon_row: dict, purchase_price: float, rrp: float,
                 target_margin_percent: float = 0.0,
                 other_direct_costs_rub: float = 0.0) -> dict:
    current_price = ozon_row["current_price"] or ozon_row["marketing_price"]
    result = calculate_economics(EconomicsInput(
        purchase_price=purchase_price,
        rrp=rrp,
        current_price=current_price,
        commission_percent=ozon_row["commission_percent"],
        logistics_rub=ozon_row["logistics_rub"],
        acquiring_rub=ozon_row["acquiring_rub"],
        other_direct_costs_rub=other_direct_costs_rub,
        target_margin_percent=target_margin_percent,
    ))
    return {**ozon_row, **asdict(result)}


def _parse_customer_price_rows(data: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in data.get("prices", []) or []:
        sku = str(row.get("sku") or "")
        cp = row.get("customer_price") or {}
        amount = cp.get("amount")
        if not sku or amount in (None, ""):
            continue
        out[sku] = {
            "customer_price": float(amount),
            "currency": cp.get("currency"),
            "offer_id": row.get("offer_id"),
            "seller_promo_price": float((row.get("price") or {}).get("amount") or 0),
        }
    return out


def fetch_customer_prices(client: "OzonClient", skus: Iterable[int | str],
                          *, attempts: int = 3, retry_delay_seconds: float = 2.0,
                          require_complete: bool = True) -> dict[str, dict]:
    """Fetch verified storefront/customer prices with completeness retries.

    HTTP-level retries are already handled by OzonClient. This function adds
    data-level retries: if Ozon returns a successful response but omits some
    requested SKUs or their customer_price, only the missing SKUs are requested
    again.

    When require_complete=True, unresolved SKUs after all attempts raise an
    exception. Silent customer_price=None is forbidden for the RRP workflow.
    """
    clean = list(dict.fromkeys(
        str(x) for x in skus if x not in (None, "", 0, "0")
    ))
    if not clean:
        return {}

    out: dict[str, dict] = {}
    pending = clean[:]

    for attempt in range(1, attempts + 1):
        next_pending: list[str] = []
        for start in range(0, len(pending), 1000):
            batch = pending[start:start + 1000]
            data = client.post("/v1/product/prices/details", {"skus": batch})
            parsed = _parse_customer_price_rows(data)
            out.update(parsed)
            next_pending.extend(sku for sku in batch if sku not in parsed)

        pending = list(dict.fromkeys(next_pending))
        if not pending:
            break
        if attempt < attempts:
            time.sleep(retry_delay_seconds * attempt)

    if pending and require_complete:
        sample = ", ".join(pending[:20])
        more = f" (+{len(pending)-20} ещё)" if len(pending) > 20 else ""
        raise RuntimeError(
            "Ozon did not return verified customer_price after "
            f"{attempts} attempts for {len(pending)} SKU: {sample}{more}"
        )

    return out


def evaluate_customer_price_policy(*, customer_price: float | None, rrp: float) -> tuple[str, str]:
    if customer_price is None or customer_price <= 0:
        return (
            "PRICE_NOT_VERIFIED",
            "Цена для покупателя не подтверждена; РРЦ-проверка должна завершиться ошибкой.",
        )
    floor = float(ceil(rrp * RRP_FLOOR_FACTOR))
    if customer_price < rrp:
        return (
            "CRITICAL",
            f"Цена для покупателя {customer_price:.2f} ниже РРЦ ({rrp:.2f}).",
        )
    if customer_price < floor:
        return (
            "WARNING",
            f"Цена для покупателя {customer_price:.2f} ниже РРЦ+5% ({floor:.2f}).",
        )
    return (
        "OK",
        f"Цена для покупателя подтверждена и не ниже РРЦ+5% ({floor:.2f}).",
    )
