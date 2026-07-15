"""Общие мелкие утилиты для разбора ответов Ozon Seller API.

Схема ответов у Ozon менялась несколько раз (v2 -> v3 -> v4), и часть полей
встречается под разными именами / на разной вложенности в зависимости от
версии метода. Чтобы скрипт не падал из-за расхождений с документацией,
используем защитное извлечение полей с перебором альтернативных путей.
"""
from __future__ import annotations

from typing import Any


def dig(d: Any, path: str, default: Any = "") -> Any:
    """dig(item, 'statuses.status') -> item['statuses']['status'] либо default."""
    cur = d
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur if cur is not None else default


def first(d: Any, *paths: str, default: Any = "") -> Any:
    for p in paths:
        v = dig(d, p, default=None)
        if v not in (None, ""):
            return v
    return default
