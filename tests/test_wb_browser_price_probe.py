"""Pure parser tests; no browser and no network access required."""
from decimal import Decimal

from wb_agent.browser_price_probe import _extract_price


def _product(article: int = 1308438233) -> dict:
    return {
        "products": [
            {
                "id": article,
                "supplierId": 12345,
                "sizes": [
                    {
                        "optionId": 999,
                        "stocks": [{"qty": 3}],
                        "price": {"product": 838600, "logistics": 0},
                    }
                ],
            }
        ]
    }


def test_moscow_price_and_illustrative_wallet_three_percent():
    price = _extract_price(_product(), 1308438233, Decimal("3"))
    assert price is not None
    assert price["public_price_rub"] == 8386
    assert price["wallet_price_estimate_rub"] == 8134
    assert price["wallet_price_is_estimate"] is True


def test_wrong_article_never_used():
    assert _extract_price(_product(9999), 1308438233, Decimal("3")) is None


def test_no_stock_is_not_price():
    payload = _product()
    payload["products"][0]["sizes"][0]["stocks"][0]["qty"] = 0
    assert _extract_price(payload, 1308438233, Decimal("3")) is None
