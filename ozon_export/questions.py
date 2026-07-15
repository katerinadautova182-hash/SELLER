"""Вопросы и ответы покупателей: v1/question/list + v1/question/answer/list (beta)."""
from __future__ import annotations

from .client import OzonClient, OzonApiError
from .utils import first
from .writer import write_table

Q_HEADERS = [
    ("question_id", "ID вопроса"),
    ("sku", "SKU"),
    ("offer_id", "Артикул (offer_id)"),
    ("product_id", "ID товара (product_id)"),
    ("text", "Текст вопроса"),
    ("author_name", "Автор"),
    ("published_at", "Дата публикации"),
    ("status", "Статус"),
    ("answers_count", "Кол-во ответов"),
]

A_HEADERS = [
    ("question_id", "ID вопроса"),
    ("answer_id", "ID ответа"),
    ("text", "Текст ответа"),
    ("author_name", "Автор ответа"),
    ("published_at", "Дата публикации"),
]


def fetch_answers(client: OzonClient, question_id: str) -> list[dict]:
    try:
        data = client.post("/v1/question/answer/list", {"question_id": question_id, "limit": 100}, allow_statuses=(403, 404))
    except OzonApiError:
        return []
    if data.get("_status_code") in (403, 404):
        return []
    return data.get("answers", data.get("result", []))


def run(client: OzonClient, info_items: list[dict]) -> None:
    print("[4/6] Вопросы и ответы (question/list)...")
    from .reviews import build_sku_map

    sku_map = build_sku_map(info_items)
    q_rows, a_rows = [], []
    last_id = ""
    questions: list[dict] = []

    while True:
        try:
            data = client.post(
                "/v1/question/list",
                {"limit": 100, "last_id": last_id},
                allow_statuses=(403, 404),
            )
        except OzonApiError as exc:
            print(f"  !! question/list недоступен: {exc}")
            break

        if data.get("_status_code") in (403, 404):
            print("  !! Метод вопросов-ответов недоступен (403/404) для этого кабинета. Файлы будут пустыми.")
            break

        chunk = data.get("questions", data.get("result", []))
        if not chunk:
            break
        questions.extend(chunk)
        last_id = data.get("last_id", "")
        print(f"  question/list: получено {len(questions)} вопросов...")
        if not last_id or not data.get("has_next", bool(last_id)):
            break

    for idx, q in enumerate(questions, start=1):
        sku = first(q, "sku", default="")
        offer_id, product_id = sku_map.get(sku, ("", ""))
        answers_count = q.get("answers_count", q.get("answered_amount", 0))
        q_rows.append(
            {
                "question_id": q.get("id", ""),
                "sku": sku,
                "offer_id": offer_id,
                "product_id": product_id,
                "text": q.get("text", ""),
                "author_name": q.get("author_name", ""),
                "published_at": first(q, "published_at", "created_at"),
                "status": q.get("status", ""),
                "answers_count": answers_count,
            }
        )
        if answers_count:
            for ans in fetch_answers(client, q.get("id", "")):
                a_rows.append(
                    {
                        "question_id": q.get("id", ""),
                        "answer_id": ans.get("id", ""),
                        "text": ans.get("text", ""),
                        "author_name": ans.get("author_name", ""),
                        "published_at": first(ans, "published_at", "created_at"),
                    }
                )
        if idx % 20 == 0:
            print(f"  ответы: обработано {idx}/{len(questions)} вопросов")

    write_table("6_voprosy", Q_HEADERS, q_rows)
    write_table("7_otvety", A_HEADERS, a_rows)
