from datetime import date

from ozon_agent.promo_policy import (
    effective_rrp,
    is_promo_offer,
    promo_rrp,
)


def test_promo_scope():
    assert is_promo_offer("206")
    assert is_promo_offer("201")
    assert is_promo_offer("ms702")
    assert is_promo_offer("ms702 ripple")
    assert is_promo_offer("MS9905")
    assert is_promo_offer("ms8862 BLACK")
    assert is_promo_offer("ms8862 DARK BLUE")
    assert is_promo_offer("MS8828YELLOW")
    assert is_promo_offer("MS8828 RED")
    assert not is_promo_offer("MS8828 BLACK")
    assert not is_promo_offer("MS9903")


def test_promo_only_inside_date_window():
    rrp = {"MS9905": 1000.0}
    assert effective_rrp("MS9905", 1000.0, rrp, date(2026, 10, 18)) == 1000.0
    assert effective_rrp("MS9905", 1000.0, rrp, date(2026, 10, 19)) == 700.0
    assert effective_rrp("MS9905", 1000.0, rrp, date(2026, 11, 20)) == 700.0
    assert effective_rrp("MS9905", 1000.0, rrp, date(2026, 11, 21)) == 1000.0


def test_8828_nonblack_uses_common_family_base():
    rrp = {
        "MS8828YELLOW": 1000.0,
        "MS8828RED": 990.0,
        "MS8828 PURPLE": 1000.0,
        "MS8828 BLACK": 1100.0,
    }
    assert promo_rrp("MS8828RED", 990.0, rrp) == 701.0
    assert promo_rrp("MS8828YELLOW", 1000.0, rrp) == 701.0
    assert promo_rrp("MS8828 BLACK", 1100.0, rrp) == 1100.0
