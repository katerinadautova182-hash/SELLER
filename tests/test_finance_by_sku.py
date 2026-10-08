import os
os.environ.setdefault("OZON_CLIENT_ID", "test")
os.environ.setdefault("OZON_API_KEY", "test")

from ozon_agent.finance_accruals import aggregate_sku_finance, aggregate_non_item_finance
from ozon_agent.finance_by_sku import combine_period


def sample_accruals():
    return [
        {
            "accrued_category": "POSTING",
            "posting": {
                "products": [{
                    "sku": 123,
                    "commission": {
                        "seller_price": {"amount": "1000"},
                        "sale_amount": {"amount": "1200"},
                        "sale_commission": {"amount": "-200"},
                    },
                    "delivery": {"total_accrued": {"amount": "-80"}},
                }]
            },
        },
        {
            "accrued_category": "ITEM",
            "posting": {
                "products": [{
                    "sku": 123,
                    "commission": {
                        "seller_price": {"amount": "1000"},
                        "sale_amount": {"amount": "1200"},
                        "sale_commission": {"amount": "-200"},
                    },
                }]
            },
            "item_fees": {
                "fees": [{
                    "sku": 123,
                    "fees": [
                        {"type_id": 1, "accrued": {"amount": "-20"}},
                        {"type_id": 25, "accrued": {"amount": "50"}},
                    ],
                }]
            },
        },
        {
            "accrued_category": "NON_ITEM",
            "non_item_fee": {
                "type_id": 54,
                "accrued": {"amount": "-300"},
            },
        },
    ]


def test_item_finance_does_not_duplicate_posting_fields_from_item_rows():
    rows = aggregate_sku_finance(sample_accruals())
    row = rows["123"]
    assert row["seller_price_rub"] == 1000
    assert row["sale_commission_rub"] == -200
    assert row["delivery_rub"] == -80
    assert row["item_fees_rub"] == 30
    assert row["item_compensation_rub"] == 50
    assert row["direct_finance_net_rub"] == 750


def test_non_item_is_not_allocated_to_sku():
    type_map = {54: {"name": "Promotion", "description": "Promotion", "category": "services"}}
    non_item = aggregate_non_item_finance(sample_accruals(), type_map)
    assert non_item[54]["amount_rub"] == -300

    sku_rows, non_item_rows, summary = combine_period(
        [("2026-09-01", sample_accruals())],
        type_map,
        {"123": {"offer_id": "A", "name": "Product A"}},
    )
    assert sku_rows[0]["direct_finance_net_rub"] == 750
    assert summary["non_item_total_rub"] == -300
    assert sku_rows[0]["offer_id"] == "A"
