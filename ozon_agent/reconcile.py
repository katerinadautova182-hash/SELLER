"""Reconcile Ozon SKUs against purchase-cost and RRP reference data."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .catalog import Catalog


@dataclass(frozen=True)
class ReconcileRow:
    offer_id: str
    sku_canonical: str | None
    has_purchase_cost: bool
    has_rrp: bool
    status: str
    issue: str | None


def reconcile_offer_ids(offer_ids: Iterable[str], catalog: Catalog) -> list[ReconcileRow]:
    rows: list[ReconcileRow] = []
    for offer_id in offer_ids:
        item = catalog.resolve(offer_id)
        if item is None:
            rows.append(ReconcileRow(
                offer_id=offer_id,
                sku_canonical=None,
                has_purchase_cost=False,
                has_rrp=False,
                status="UNMATCHED",
                issue="SKU не найден в справочнике алиасов.",
            ))
            continue

        has_cost = item.purchase_cost is not None and item.purchase_cost > 0
        has_rrp = item.rrp is not None and item.rrp > 0
        if has_cost and has_rrp:
            status, issue = "READY", None
        elif not has_cost and not has_rrp:
            status, issue = "MISSING_BOTH", "Нет закупочной цены и РРЦ."
        elif not has_cost:
            status, issue = "MISSING_COST", "Нет закупочной цены."
        else:
            status, issue = "MISSING_RRP", "Нет РРЦ."

        rows.append(ReconcileRow(
            offer_id=offer_id,
            sku_canonical=item.sku_canonical,
            has_purchase_cost=has_cost,
            has_rrp=has_rrp,
            status=status,
            issue=issue,
        ))
    return rows
