from ozon_agent.economics import EconomicsInput, calculate_economics


def test_rrp_plus_5_is_hard_floor():
    r = calculate_economics(EconomicsInput(
        purchase_price=1000,
        rrp=2000,
        current_price=2100,
        commission_percent=10,
        logistics_rub=100,
        target_margin_percent=0,
    ))
    assert r.rrp_floor == 2100
    assert r.minimum_allowed_price >= 2100


def test_economic_floor_can_be_above_rrp_floor():
    r = calculate_economics(EconomicsInput(
        purchase_price=1000,
        rrp=1500,
        current_price=1800,
        commission_percent=25,
        logistics_rub=500,
        target_margin_percent=10,
    ))
    assert r.minimum_allowed_price > r.rrp_floor


def test_landed_cost_uses_35_percent():
    r = calculate_economics(EconomicsInput(
        purchase_price=1000,
        rrp=2000,
        current_price=2500,
        commission_percent=10,
    ))
    assert r.landed_cost == 1350


def test_negative_profit_is_critical():
    r = calculate_economics(EconomicsInput(
        purchase_price=1000,
        rrp=2000,
        current_price=1200,
        commission_percent=20,
        logistics_rub=300,
    ))
    assert r.profit_rub < 0
    assert r.status == "CRITICAL"
