"""Список товаров и остатки: v3/product/list + v3/product/info/list."""
from __future__ import annotations

from .client import OzonClient
from .utils import dig, first
from .writer import write_table

HEADERS = [
    ("offer_id", "Артикул (offer_id)"),
    ("product_id", "ID товара (product_id)"),
    ("name", "Название"),
    ("barcode", "Штрихкод"),
    ("status", "Статус"),
    ("status_description", "Статус (описание)"),
    ("visible", "Виден покупателям"),
    ("category_id", "ID категории"),
    ("price", "Цена, ₽"),
    ("old_price", "Цена до скидки, ₽"),
    ("premium_price", "Цена Premium, ₽"),
    ("currency_code", "Валюта"),
    ("fbo_sku", "SKU FBO"),
    ("fbs_sku", "SKU FBS"),
    ("stock_present", "Остаток доступный к продаже"),
    ("stock_reserved", "Остаток зарезервирован"),
    ("stock_coming", "Остаток в пути"),
    ("images_count", "Кол-во изображений"),
    ("created_at", "Дата создания карточки"),
]


def fetch_product_ids(client: OzonClient) -> list[dict]:
    """v3/product/list с пагинацией по last_id. Возвращает [{offer_id, product_id}]."""
    items: list[dict] = []
    last_id = ""
    while True:
        body = {"filter": {"visibility": "ALL"}, "last_id": last_id, "limit": 1000}
        data = client.post("/v3/product/list", body)
        result = data.get("result", {})
        chunk = result.get("items", [])
        items.extend(chunk)
        last_id = result.get("last_id", "")
        print(f"  product/list: получено {len(items)} товаров...")
        if not chunk or not last_id:
            break
    return items


def fetch_products_info(client: OzonClient, product_ids: list[int]) -> list[dict]:
    """v3/product/info/list батчами по 1000 product_id."""
    all_items: list[dict] = []
    batch_size = 1000
    for i in range(0, len(product_ids), batch_size):
        batch = product_ids[i : i + batch_size]
        data = client.post("/v3/product/info/list", {"product_id": batch})
        chunk = data.get("items", data.get("result", {}).get("items", []))
        all_items.extend(chunk)
        print(f"  product/info/list: обработано {min(i + batch_size, len(product_ids))}/{len(product_ids)}")
    return all_items


def _extract_row(item: dict) -> dict:
    sources = item.get("sources", []) or []
    fbo_sku = first(item, "fbo_sku") or next(
        (s.get("sku") for s in sources if s.get("source") == "fbo" and s.get("sku")), ""
    )
    fbs_sku = first(item, "fbs_sku") or next(
        (s.get("sku") for s in sources if s.get("source") == "fbs" and s.get("sku")), ""
    )
    barcodes = item.get("barcodes") or ([item["barcode"]] if item.get("barcode") else [])
    return {
        "offer_id": item.get("offer_id", ""),
        "product_id": item.get("id", item.get("product_id", "")),
        "name": item.get("name", ""),
        "barcode": "; ".join(barcodes) if barcodes else "",
        "status": first(item, "statuses.status", "status"),
        "status_description": first(item, "statuses.status_name", "statuses.status_description"),
        "visible": first(item, "visible", "statuses.is_visible", default=""),
        "category_id": item.get("description_category_id", item.get("category_id", "")),
        "price": item.get("price", ""),
        "old_price": item.get("old_price", ""),
        "premium_price": item.get("premium_price", ""),
        "currency_code": item.get("currency_code", ""),
        "fbo_sku": fbo_sku,
        "fbs_sku": fbs_sku,
        "stock_present": dig(item, "stocks.present", ""),
        "stock_reserved": dig(item, "stocks.reserved", ""),
        "stock_coming": dig(item, "stocks.coming", ""),
        "images_count": len(item.get("images", []) or item.get("primary_image", [])),
        "created_at": item.get("created_at", ""),
    }


def run(client: OzonClient) -> list[dict]:
    """Возвращает список карточек товаров (сырые item-словари из product/info/list),
    чтобы другие модули (cards, analytics, fbo_stocks) могли переиспользовать
    offer_id/product_id/sku без повторных запросов.
    """
    print("[1/6] Список товаров (product/list)...")
    id_items = fetch_product_ids(client)
    product_ids = [it["product_id"] for it in id_items if it.get("product_id")]
    print(f"  всего товаров: {len(product_ids)}")

    print("[1/6] Подробности по товарам (product/info/list)...")
    info_items = fetch_products_info(client, product_ids)

    rows = [_extract_row(it) for it in info_items]
    write_table("1_tovary", HEADERS, rows)
    return info_items
