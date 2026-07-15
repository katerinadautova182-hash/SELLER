"""Конфигурация: ключи доступа читаются только из переменных окружения / .env.

Ключи никогда не должны попадать в код, логи или git-репозиторий.
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

BASE_URL = "https://api-seller.ozon.ru"

CLIENT_ID = os.environ.get("OZON_CLIENT_ID", "").strip()
API_KEY = os.environ.get("OZON_API_KEY", "").strip()

if not CLIENT_ID or not API_KEY:
    raise RuntimeError(
        "Не заданы OZON_CLIENT_ID / OZON_API_KEY. "
        "Задайте их в переменных окружения или файле .env (см. .env.example)."
    )

RAW_DIR = Path(__file__).resolve().parent.parent / "raw"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"

# Пауза между запросами (сек). Лимит Ozon Seller API — 50 запросов/сек на кабинет,
# но у части ресурсоёмких методов (аналитика, отчёты) более жёсткие индивидуальные
# ограничения, поэтому по умолчанию работаем существенно медленнее общего лимита.
REQUEST_DELAY = 0.5

# Сколько раз повторять запрос при 429 / 5xx перед тем как сдаться.
MAX_RETRIES = 5
