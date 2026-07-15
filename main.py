#!/usr/bin/env python3
"""Полная выгрузка данных магазина Ozon Seller в CSV/XLSX.

Использование:
    export OZON_CLIENT_ID=...
    export OZON_API_KEY=...
    python3 main.py

Либо создайте .env на основе .env.example (файл в .gitignore, в git не попадает).

Что делает:
  1. Список товаров и их основные поля/остатки       -> output/1_tovary.*
  2. Карточки товаров (габариты, кол-во фото/PDF)     -> output/2_kartochki_tovarov.*
  3. Характеристики (длинный формат)                   -> output/3_harakteristiki.*
  4. Изображения (длинный формат)                       -> output/4_izobrazheniya.*
  5. Отзывы (нужен Premium Plus)                        -> output/5_otzyvy.*
  6. Вопросы покупателей                                -> output/6_voprosy.*
  7. Ответы на вопросы                                  -> output/7_otvety.*
  8. Продажи помесячно за 12 мес по SKU                 -> output/8_prodazhi_po_mesyatsam.*
  9. Продажи итого за 12 мес по SKU                     -> output/9_prodazhi_itogo_12mes.*
 10. Остатки FBO по складам/кластерам                   -> output/10_ostatki_fbo_po_skladam.*

Все сырые ответы API сохраняются в /raw для отладки без повторных запросов.
Между запросами выдерживается пауза (см. ozon_export/config.py: REQUEST_DELAY),
при 429/5xx запросы повторяются с экспоненциальной задержкой.
"""
from __future__ import annotations

import logging
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

from ozon_export import cards, fbo_stocks, products, questions, reviews, analytics
from ozon_export.client import OzonClient, OzonApiError


def main() -> int:
    start = time.time()
    client = OzonClient()

    print("=" * 70)
    print("Выгрузка данных Ozon Seller API")
    print("=" * 70)

    try:
        info_items = products.run(client)
    except OzonApiError as exc:
        print(f"КРИТИЧЕСКАЯ ОШИБКА на этапе списка товаров: {exc}")
        return 1

    if not info_items:
        print("Товары не найдены — дальнейшая выгрузка карточек/аналитики бессмысленна.")
        return 1

    for step in (
        lambda: cards.run(client, info_items),
        lambda: reviews.run(client, info_items),
        lambda: questions.run(client, info_items),
        lambda: analytics.run(client),
        lambda: fbo_stocks.run(client),
    ):
        try:
            step()
        except OzonApiError as exc:
            print(f"!! Ошибка шага (пропускаю, остальное продолжится): {exc}")
        except Exception as exc:  # noqa: BLE001 - не даём одному модулю уронить весь экспорт
            print(f"!! Непредвиденная ошибка шага (пропускаю): {exc}")

    elapsed = time.time() - start
    print("=" * 70)
    print(f"Готово за {elapsed:.0f} сек. Результаты — в папке output/, сырые ответы — в raw/.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
