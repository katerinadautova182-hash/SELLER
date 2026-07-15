"""Остатки FBO по складам/кластерам: v1/analytics/stock_on_warehouses."""
from __future__ import annotations

from .client import OzonClient, OzonApiError
from .utils import first
from .writer import write_table

HEADERS = [
    ("sku", "SKU"),
    ("offer_id", "Артикул (offer_id)"),
    ("name", "Название товара"),
    ("cluster_name", "Кластер"),
    ("warehouse_name", "Склад"),
    ("free_to_sell", "Доступно к продаже, шт."),
    ("reserved", "Зарезервировано, шт."),
    ("promised", "Ожидается, шт."),
]


def run(client: OzonClient) -> None:
    print("[6/6] Остатки FBO по складам (analytics/stock_on_warehouses)...")
    rows = []
    offset = 0
    limit = 1000
    while True:
        body = {"limit": limit, "offset": offset, "warehouse_type": "ALL"}
        try:
            data = client.post("/v1/analytics/stock_on_warehouses", body, allow_statuses=(403, 404))
        except OzonApiError as exc:
            print(f"  !! stock_on_warehouses ошибка: {exc}")
            break

        if data.get("_status_code") in (403, 404):
            print("  !! Метод остатков по складам недоступен (403/404) для этого кабинета.")
            break

        chunk = data.get("result", {}).get("rows", data.get("rows", []))
        if not chunk:
            break

        for r in chunk:
            rows.append(
                {
                    "sku": r.get("sku", ""),
                    "offer_id": r.get("item_code", r.get("offer_id", "")),
                    "name": r.get("item_name", r.get("name", "")),
                    "cluster_name": first(r, "cluster_name"),
                    "warehouse_name": first(r, "warehouse_name"),
                    "free_to_sell": r.get("free_to_sell_amount", ""),
                    "reserved": r.get("reserved_amount", ""),
                    "promised": r.get("promised_amount", ""),
                }
            )

        print(f"  stock_on_warehouses: получено {len(rows)} строк...")
        if len(chunk) < limit:
            break
        offset += limit

    write_table("10_ostatki_fbo_po_skladam", HEADERS, rows)
