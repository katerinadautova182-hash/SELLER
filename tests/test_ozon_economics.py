from ozon_agent.economics import EconomicsInput, calculate_economics


def test_rrp_is_hard_floor_not_five_percent():
    r = calculate_economics(EconomicsInput(
        purchase_price=1000,
        rrp=2000,
        current_price=2100,
        commission_percent=10,
        logistics_rub=100,
        target_margin_percent=0,
    ))
    assert r.rrp_floor == 2000
    assert r.minimum_allowed_price >= 2000


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


def test_customer_price_policy_requires_verified_price():
    from ozon_agent.pricing import evaluate_customer_price_policy
    status, _ = evaluate_customer_price_policy(customer_price=None, rrp=1000)
    assert status == "PRICE_NOT_VERIFIED"


def test_customer_price_policy_uses_red_yellow_green_zones():
    from ozon_agent.pricing import evaluate_customer_price_policy

    status, _ = evaluate_customer_price_policy(customer_price=999, rrp=1000)
    assert status == "CRITICAL"

    status, _ = evaluate_customer_price_policy(customer_price=1199, rrp=1000, offer_id="MS701")
    assert status == "WARNING"

    status, _ = evaluate_customer_price_policy(customer_price=1200, rrp=1000, offer_id="MS701")
    assert status == "OK"


def test_accessories_are_not_forced_into_equipment_markups():
    from ozon_agent.pricing import evaluate_customer_price_policy
    status, _ = evaluate_customer_price_policy(customer_price=1030, rrp=1000, offer_id="J304")
    assert status == "OK"
