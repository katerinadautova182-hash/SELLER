"""Minimal live connectivity test for Ozon Seller API.

Does not print credentials and does not mutate seller data.
"""
from __future__ import annotations

from ozon_export.client import OzonClient


def main() -> int:
    client = OzonClient()
    data = client.post(
        "/v3/product/list",
        {"filter": {"visibility": "ALL"}, "last_id": "", "limit": 1},
    )
    result = data.get("result", {})
    items = result.get("items", [])
    print(f"Ozon API OK. First page returned {len(items)} item(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
