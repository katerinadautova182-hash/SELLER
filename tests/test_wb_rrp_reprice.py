from wb_agent.rrp_reprice import proposed_base_price


def test_no_change_when_buyer_price_meets_target():
    assert proposed_base_price(10000, 10500, 10500) == 10000


def test_raise_base_price_proportionally_to_buyer_gap():
    assert proposed_base_price(10000, 10000, 10500) == 10500


def test_round_up_base_price():
    assert proposed_base_price(10000, 9999, 10500) == 10502
