"""Полные карточки товаров: характеристики, изображения, описание, комплектация.

v4/product/info/attributes  -> характеристики + изображения + PDF + габариты
v1/product/info/description -> текстовое HTML-описание (запрашивается по одному товару)
v1/description-category/attribute -> словарь названий характеристик (для человекочитаемых колонок)
"""
from __future__ import annotations

from .client import OzonClient, OzonApiError
from .writer import write_table

CARD_HEADERS = [
    ("offer_id", "Артикул (offer_id)"),
    ("product_id", "ID товара (product_id)"),
    ("name", "Название"),
    ("category_id", "ID категории описания"),
    ("type_id", "ID типа товара"),
    ("barcode", "Штрихкод"),
    ("depth", "Глубина, мм"),
    ("width", "Ширина, мм"),
    ("height", "Высота, мм"),
    ("weight", "Вес, г"),
    ("images_count", "Кол-во изображений"),
    ("images_360_count", "Кол-во изображений 360°"),
    ("pdf_count", "Кол-во PDF-файлов"),
    ("description_html", "Описание (HTML)"),
]

ATTR_HEADERS = [
    ("offer_id", "Артикул (offer_id)"),
    ("product_id", "ID товара (product_id)"),
    ("attribute_id", "ID характеристики"),
    ("attribute_name", "Название характеристики"),
    ("value", "Значение"),
]

IMAGE_HEADERS = [
    ("offer_id", "Артикул (offer_id)"),
    ("product_id", "ID товара (product_id)"),
    ("image_type", "Тип изображения"),
    ("position", "Позиция"),
    ("url", "Ссылка"),
]


def _fetch_attribute_dict(client: OzonClient, category_id: int, type_id: int) -> dict[int, str]:
    try:
        data = client.post(
            "/v1/description-category/attribute",
            {"description_category_id": category_id, "type_id": type_id, "language": "RU"},
        )
    except OzonApiError:
        return {}
    result = data.get("result", data if isinstance(data, list) else [])
    return {a.get("attribute_id"): a.get("name", "") for a in result if a.get("attribute_id")}


def fetch_attributes(client: OzonClient, product_ids: list[int]) -> list[dict]:
    all_items: list[dict] = []
    batch_size = 1000
    for i in range(0, len(product_ids), batch_size):
        batch = product_ids[i : i + batch_size]
        last_id = ""
        while True:
            body = {
                "filter": {"product_id": batch},
                "limit": 1000,
                "last_id": last_id,
            }
            data = client.post("/v4/product/info/attributes", body)
            chunk = data.get("result", [])
            all_items.extend(chunk)
            last_id = data.get("last_id", "")
            if not chunk or not last_id or len(chunk) < 1000:
                break
        print(f"  attributes: обработано {min(i + batch_size, len(product_ids))}/{len(product_ids)}")
    return all_items


def fetch_description(client: OzonClient, product_id: int, offer_id: str) -> str:
    try:
        data = client.post("/v1/product/info/description", {"product_id": product_id})
    except OzonApiError:
        return ""
    return data.get("result", {}).get("description", "")


def run(client: OzonClient, info_items: list[dict]) -> None:
    print("[2/6] Характеристики и изображения (product/info/attributes)...")
    product_ids = [it.get("id", it.get("product_id")) for it in info_items if it.get("id") or it.get("product_id")]
    attr_items = fetch_attributes(client, product_ids)

    attr_dict_cache: dict[tuple[int, int], dict[int, str]] = {}
    card_rows, attr_rows, image_rows = [], [], []

    print("[2/6] Описание товаров (product/info/description) — по одному запросу на товар...")
    for idx, item in enumerate(attr_items, start=1):
        offer_id = item.get("offer_id", "")
        product_id = item.get("id", "")
        category_id = item.get("description_category_id", 0)
        type_id = item.get("type_id", 0)

        key = (category_id, type_id)
        if key not in attr_dict_cache:
            attr_dict_cache[key] = _fetch_attribute_dict(client, category_id, type_id) if category_id and type_id else {}
        names = attr_dict_cache[key]

        for attr in item.get("attributes", []) or []:
            values = attr.get("values", []) or []
            value_text = "; ".join(
                str(v.get("value", "")) or (f"dict:{v.get('dictionary_value_id')}" if v.get("dictionary_value_id") else "")
                for v in values
            ) if values else ""
            attr_rows.append(
                {
                    "offer_id": offer_id,
                    "product_id": product_id,
                    "attribute_id": attr.get("attribute_id", ""),
                    "attribute_name": names.get(attr.get("attribute_id"), ""),
                    "value": value_text,
                }
            )

        images = item.get("images", []) or []
        for pos, url in enumerate(images, start=1):
            image_rows.append(
                {"offer_id": offer_id, "product_id": product_id, "image_type": "основное", "position": pos, "url": url}
            )
        for pos, url in enumerate(item.get("images360", []) or [], start=1):
            image_rows.append(
                {"offer_id": offer_id, "product_id": product_id, "image_type": "360°", "position": pos, "url": url}
            )
        for pdf in item.get("pdf_list", []) or []:
            image_rows.append(
                {
                    "offer_id": offer_id,
                    "product_id": product_id,
                    "image_type": "PDF",
                    "position": "",
                    "url": pdf.get("file_name", pdf.get("name", "")),
                }
            )

        description_html = fetch_description(client, product_id, offer_id)
        if idx % 20 == 0:
            print(f"  описания: {idx}/{len(attr_items)}")

        card_rows.append(
            {
                "offer_id": offer_id,
                "product_id": product_id,
                "name": item.get("name", ""),
                "category_id": category_id,
                "type_id": type_id,
                "barcode": item.get("barcode", ""),
                "depth": item.get("depth", ""),
                "width": item.get("width", ""),
                "height": item.get("height", ""),
                "weight": item.get("weight", ""),
                "images_count": len(images),
                "images_360_count": len(item.get("images360", []) or []),
                "pdf_count": len(item.get("pdf_list", []) or []),
                "description_html": description_html,
            }
        )

    write_table("2_kartochki_tovarov", CARD_HEADERS, card_rows)
    write_table("3_harakteristiki", ATTR_HEADERS, attr_rows)
    write_table("4_izobrazheniya", IMAGE_HEADERS, image_rows)
