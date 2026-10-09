"""Small read-only client for Yandex Market Partner API."""
from __future__ import annotations

import os
import time
from typing import Any

import requests

API = "https://api.partner.market.yandex.ru"


class YandexMarketClient:
    def __init__(self, token: str | None = None):
        self.token = (token or os.getenv("YANDEX_MARKET_API_KEY", "")).strip()
        if not self.token:
            raise RuntimeError("YANDEX_MARKET_API_KEY is not configured")
        self.session = requests.Session()
        self.session.headers.update({
            "Api-Key": self.token,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "SELLER-yandex-price-control/1.0",
        })

    def request(self, method: str, path: str, *, params: dict | None = None,
                payload: dict | None = None) -> dict[str, Any]:
        url = API + path
        last_error: Exception | None = None
        for attempt in range(5):
            try:
                response = self.session.request(
                    method, url, params=params, json=payload, timeout=45
                )
                if response.status_code in (429, 500, 502, 503, 504):
                    if attempt < 4:
                        time.sleep(2 ** attempt + 1)
                        continue
                if not response.ok:
                    raise RuntimeError(
                        f"Yandex Market API HTTP {response.status_code}: "
                        f"{response.text[:700]}"
                    )
                data = response.json()
                if isinstance(data, dict) and data.get("status") == "ERROR":
                    raise RuntimeError(
                        "Yandex Market API error: " + str(data.get("errors"))[:700]
                    )
                return data
            except (requests.RequestException, ValueError, RuntimeError) as exc:
                last_error = exc
                if attempt < 4 and not isinstance(exc, RuntimeError):
                    time.sleep(2 ** attempt + 1)
                    continue
                raise
        raise RuntimeError(str(last_error or "unknown Yandex Market API error"))

    def campaigns(self) -> list[dict]:
        out: list[dict] = []
        page_token: str | None = None
        for _ in range(100):
            params: dict[str, Any] = {"limit": 100}
            if page_token:
                params["pageToken"] = page_token
            data = self.request("GET", "/v2/campaigns", params=params)
            page = data.get("campaigns") or []
            if not isinstance(page, list):
                raise ValueError("Yandex campaigns response has no campaigns array")
            out.extend(page)
            page_token = ((data.get("paging") or {}).get("nextPageToken") or "").strip()
            if not page_token:
                return out
        raise RuntimeError("Too many Yandex campaign pages")

    def offer_mappings(self, business_id: int) -> list[dict]:
        out: list[dict] = []
        page_token: str | None = None
        for _ in range(1000):
            params: dict[str, Any] = {"limit": 100}
            if page_token:
                params["pageToken"] = page_token
            data = self.request(
                "POST",
                f"/v2/businesses/{int(business_id)}/offer-mappings",
                params=params,
                payload={},
            )
            result = data.get("result") or {}
            page = result.get("offerMappings") or []
            if not isinstance(page, list):
                raise ValueError("Yandex offer-mappings response has no offerMappings array")
            out.extend(page)
            page_token = ((result.get("paging") or {}).get("nextPageToken") or "").strip()
            if not page_token:
                return out
        raise RuntimeError("Too many Yandex offer-mapping pages")
