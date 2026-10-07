from ozon_agent.margin import estimate_live_margin


def test_live_margin_uses_purchase_plus_35_percent():
    r = estimate_live_margin(
        purchase_cost=1000,
        current_price=3000,
        marketing_price=2500,
        commission_percent=20,
        logistics_rub=100,
        acquiring_rub=25,
    )
    assert r.landed_cost_rub == 1350
    assert r.seller_revenue_rub == 2500
    assert r.commission_rub == 500
    assert r.profit_before_tax_ads_rub == 525
    assert r.margin_before_tax_ads_percent == 21.0
    assert r.revenue_source == "marketing_price"
