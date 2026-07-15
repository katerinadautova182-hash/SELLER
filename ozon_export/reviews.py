"""Отзывы покупателей: v1/review/list (требует подписку Premium Plus).

Метод не привязан к конкретному товару в запросе — отдаёт все отзывы
магазина постранично, каждый отзыв содержит sku. Сопоставляем sku с
offer_id/product_id, используя карту, собранную из product/info/list.
"""
from __future__ import annotations

from .client import OzonClient, OzonApiError
from .utils import first
from .writer import write_table

HEADERS = [
    ("review_id", "ID отзыва"),
    ("sku", "SKU"),
    ("offer_id", "Артикул (offer_id)"),
    ("product_id", "ID товара (product_id)"),
    ("rating", "Оценка"),
    ("text", "Текст отзыва"),
    ("published_at", "Дата публикации"),
    ("photos_count", "Кол-во фото"),
    ("videos_count", "Кол-во видео"),
    ("comments_count", "Кол-во комментариев"),
    ("status", "Статус"),
]


def build_sku_map(info_items: list[dict]) -> dict:
    sku_map = {}
    for it in info_items:
        offer_id = it.get("offer_id", "")
        product_id = it.get("id", it.get("product_id", ""))
        for s in it.get("sources", []) or []:
            if s.get("sku"):
                sku_map[s["sku"]] = (offer_id, product_id)
        for key in ("fbo_sku", "fbs_sku"):
            if it.get(key):
                sku_map[it[key]] = (offer_id, product_id)
    return sku_map


def run(client: OzonClient, info_items: list[dict]) -> None:
    print("[3/6] Отзывы (review/list, требует Premium Plus)...")
    sku_map = build_sku_map(info_items)
    rows: list[dict] = []
    last_id = ""

    while True:
        try:
            data = client.post(
                "/v1/review/list",
                {"limit": 100, "last_id": last_id, "sort_dir": "ASC"},
                allow_statuses=(403, 404),
            )
        except OzonApiError as exc:
            print(f"  !! review/list недоступен: {exc}")
            break

        if data.get("_status_code") in (403, 404):
            print(
                "  !! Метод отзывов недоступен (403/404) — вероятно, на тарифе аккаунта "
                "нет подписки Premium Plus, либо метод для этого кабинета не подключён. "
                "Файл отзывов будет создан пустым."
            )
            break

        chunk = data.get("reviews", data.get("result", []))
        if not chunk:
            break

        for r in chunk:
            sku = first(r, "sku", default="")
            offer_id, product_id = sku_map.get(sku, ("", ""))
            photos = r.get("photos", r.get("photos_amount", []))
            videos = r.get("videos", r.get("videos_amount", []))
            rows.append(
                {
                    "review_id": r.get("id", ""),
                    "sku": sku,
                    "offer_id": offer_id,
                    "product_id": product_id,
                    "rating": first(r, "rating", "grade", "score"),
                    "text": r.get("text", ""),
                    "published_at": first(r, "published_at", "created_at"),
                    "photos_count": photos if isinstance(photos, int) else len(photos or []),
                    "videos_count": videos if isinstance(videos, int) else len(videos or []),
                    "comments_count": r.get("comments_amount", r.get("comments_count", "")),
                    "status": r.get("status", ""),
                }
            )

        last_id = data.get("last_id", "")
        print(f"  review/list: получено {len(rows)} отзывов...")
        if not last_id or not data.get("has_next", bool(last_id)):
            break

    write_table("5_otzyvy", HEADERS, rows)
