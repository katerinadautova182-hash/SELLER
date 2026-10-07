from ozon_agent.unit_economics_live import build_rows, resolve_purchase_cost


def test_resolve_regular_cost():
    cost, source = resolve_purchase_cost("A", {"A": 100.0})
    assert cost == 100.0
    assert source in ("cost_map", "cost_map_raw")


def test_build_live_unit_economics():
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
    rows = build_rows(snapshot, {"A": 400.0})
    row = rows[0]
    assert row["seller_revenue_rub"] == 900.0
    assert row["landed_cost_rub"] == 540.0
    assert row["commission_rub"] == 180.0
    assert row["profit_before_tax_ads_rub"] == 120.0
    assert row["status"] == "POSITIVE"
