from ozon_agent.cost_policy import choose_purchase_cost, is_trusted_cost_source


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


def test_auto_alias_cannot_become_purchase_cost():
    r = choose_purchase_cost(
        supplier_price=None,
        receipt_cost=None,
        legacy_cost=1240.83,
        legacy_source="AUTO_ALIAS_PLUS",
    )
    assert r.purchase_cost is None
    assert r.status == "UNTRUSTED_LEGACY_COST"


def test_auto_alias_sources_are_untrusted():
    assert is_trusted_cost_source("AUTO_ALIAS") is False
    assert is_trusted_cost_source("AUTO_ALIAS_PLUS") is False
    assert is_trusted_cost_source("legacy_unverified") is False
    assert is_trusted_cost_source("Поступление 27.04.2026") is True
    assert is_trusted_cost_source("ЗАКУП.xlsx 16.07.2026") is True
    assert is_trusted_cost_source("manual_confirmed") is True
