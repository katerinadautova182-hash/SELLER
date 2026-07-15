"""HTTP-клиент для Ozon Seller API: авторизация через заголовки,
пауза между запросами, повторные попытки при 429/5xx, сохранение
сырых ответов в /raw для отладки без повторных обращений к API.

Ключи Client-Id / Api-Key передаются только в заголовках и никогда
не печатаются и не логируются в открытом виде.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import requests

from . import config

logger = logging.getLogger("ozon_export")


class OzonApiError(RuntimeError):
    def __init__(self, path: str, status_code: int, body: Any):
        self.path = path
        self.status_code = status_code
        self.body = body
        super().__init__(f"{path} -> HTTP {status_code}: {str(body)[:500]}")


class OzonClient:
    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Client-Id": config.CLIENT_ID,
                "Api-Key": config.API_KEY,
                "Content-Type": "application/json",
            }
        )
        self._last_call_ts = 0.0
        config.RAW_DIR.mkdir(parents=True, exist_ok=True)

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call_ts
        wait = config.REQUEST_DELAY - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_call_ts = time.monotonic()

    def _save_raw(self, path: str, payload: Any) -> None:
        safe_name = path.strip("/").replace("/", "__")
        folder = config.RAW_DIR / safe_name
        folder.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        fname = folder / f"{ts}_{int(time.time() * 1000) % 100000}.json"
        with open(fname, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def post(self, path: str, body: dict | None = None, allow_statuses: tuple[int, ...] = ()) -> dict:
        """POST-запрос к Ozon Seller API.

        allow_statuses — коды, которые не считаются ошибкой (например 403 для
        методов, недоступных на текущем тарифе) и возвращаются вызывающему коду
        как обычный ответ с ключом _status_code, чтобы он мог решить, что делать.
        """
        body = body or {}
        url = f"{config.BASE_URL}{path}"

        for attempt in range(1, config.MAX_RETRIES + 1):
            self._throttle()
            try:
                resp = self._session.post(url, json=body, timeout=60)
            except requests.RequestException as exc:
                logger.warning("Сетевая ошибка при запросе %s (попытка %d): %s", path, attempt, exc)
                time.sleep(2 * attempt)
                continue

            if resp.status_code == 429 or resp.status_code >= 500:
                logger.warning(
                    "%s ответил HTTP %d (попытка %d/%d), повтор через %ds",
                    path, resp.status_code, attempt, config.MAX_RETRIES, 2 * attempt,
                )
                time.sleep(2 * attempt)
                continue

            try:
                data = resp.json()
            except ValueError:
                data = {"_raw_text": resp.text}

            self._save_raw(path, {"request": body, "status_code": resp.status_code, "response": data})

            if resp.status_code in allow_statuses:
                data["_status_code"] = resp.status_code
                return data

            if not resp.ok:
                raise OzonApiError(path, resp.status_code, data)

            return data

        raise OzonApiError(path, -1, "исчерпаны попытки повтора запроса")
