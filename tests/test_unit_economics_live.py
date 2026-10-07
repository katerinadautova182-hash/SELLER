from ozon_agent.unit_economics_live import build_rows, resolve_purchase_cost


def test_resolve_regular_verified_cost():
    cost, source, verified = resolve_purchase_cost(
        "A",
        {"A": {"cost": 100.0, "source": "invoice", "verified": True}},
    )
    assert cost == 100.0
    assert source == "invoice"
    assert verified is True


def test_build_live_unit_economics_verified():
    snapshot = {"items": [{
        "offer_id": "A",
        "name": "Test",
        "eligibility": "ELIGIBLE",
        "customer_price": 1000.0,
        "current_price": 1000.0,
        "marketing_price": 900.0,
        "commission_percent": 20.0,
        "logistics_rub": 50.0,
        "acquiring_rub": 10.0,
    }]}
    costs = {"A": {"cost": 400.0, "source": "invoice", "verified": True}}
    rows = build_rows(snapshot, costs)
    row = rows[0]
    assert row["seller_revenue_rub"] == 900.0
    assert row["landed_cost_rub"] == 540.0
    assert row["commission_rub"] == 180.0
    assert row["profit_before_tax_ads_rub"] == 120.0
    assert row["status"] == "POSITIVE"
    assert row["purchase_cost_verified"] is True


def test_legacy_numeric_cost_is_not_treated_as_verified():
    snapshot = {"items": [{
        "offer_id": "A",
        "name": "Test",
        "eligibility": "ELIGIBLE",
        "customer_price": 1000.0,
        "current_price": 1000.0,
        "marketing_price": 900.0,
        "commission_percent": 20.0,
        "logistics_rub": 50.0,
        "acquiring_rub": 10.0,
    }]}
    rows = build_rows(
        snapshot,
        {"A": {"cost": 400.0, "source": "legacy_unverified", "verified": False}},
    )
    assert rows[0]["status"] == "UNVERIFIED_COST"
