from decimal import Decimal

from wb_agent.buyer_price import (
    WalletConfig,
    calculate_wallet_price,
    parse_card_payload,
    parse_wallet_config,
)


def test_wallet_config_from_public_wb_payloads():
    config = parse_wallet_config(
        {
            "state": 0,
            "data": [
                {
                    "wc_type": "Незалогиненный кошелёк",
                    "is_active": True,
                    "discount_value": 3,
                }
            ],
        },
        {"variables": {"wlt1DiscountDisplayMaxPrice": 12000}},
    )
    assert config.discount_percent == Decimal("3")
    assert config.max_price_rub == Decimal("12000")


def test_wallet_discount_applies_to_product_component_then_logistics():
    result = calculate_wallet_price(
        100_000,
        5_000,
        WalletConfig(discount_percent=Decimal("3"), max_price_rub=Decimal("12000")),
    )
    assert result == (1050, 1020, 1000, 50, True)


def test_wallet_discount_respects_display_max_price():
    result = calculate_wallet_price(
        1_300_000,
        0,
        WalletConfig(discount_percent=Decimal("3"), max_price_rub=Decimal("12000")),
    )
    assert result == (13000, 13000, 13000, 0, False)


def test_card_payload_uses_cheapest_in_stock_variant():
    payload = {
        "products": [
            {
                "id": 42,
                "supplierId": 777,
                "sizes": [
                    {
                        "optionId": 1,
                        "stocks": [{"qty": 0}],
                        "price": {"product": 80_000, "logistics": 0},
                    },
                    {
                        "optionId": 2,
                        "stocks": [{"qty": 3}],
                        "price": {"product": 100_000, "logistics": 5_000},
                    },
                    {
                        "optionId": 3,
                        "stocks": [{"qty": 2}],
                        "price": {"product": 95_000, "logistics": 5_000},
                    },
                ],
            }
        ]
    }
    config = WalletConfig(
        discount_percent=Decimal("3"),
        max_price_rub=Decimal("12000"),
    )
    rows = parse_card_payload(payload, config)
    price = rows[42]
    assert price.supplier_id == 777
    assert price.option_id == 3
    assert price.public_price_rub == 1000
    assert price.wallet_price_rub == 971
