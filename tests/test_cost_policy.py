from ozon_agent.cost_policy import choose_purchase_cost


def test_current_supplier_price_has_priority():
    r = choose_purchase_cost(
        supplier_price=545,
        receipt_cost=500,
        legacy_cost=1240.83,
        legacy_source="AUTO_ALIAS",
    )
    assert r.purchase_cost == 545
    assert r.source == "CURRENT_SUPPLIER_PRICE"


def test_large_source_difference_is_flagged():
    r = choose_purchase_cost(
        supplier_price=545,
        receipt_cost=1240.83,
        legacy_cost=None,
    )
    assert r.status == "COST_CONFLICT"
