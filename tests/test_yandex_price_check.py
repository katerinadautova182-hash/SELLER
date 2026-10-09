from yandex_agent.storefront import extract_pay_price, visible_text
from yandex_agent.price_check import classify


def test_extracts_green_price_for_target_seller_not_first_offer():
    text = (
        "Товар A Цена с картой Яндекс Пэй 9 100 ₽ вместо 10 000 ₽ "
        "Чужой магазин В корзину "
        "Товар A Цена с картой Яндекс Пэй 10 490 ₽ вместо 11 000 ₽ "
        "Mark Shmidt & jRL Professional В корзину"
    )
    result = extract_pay_price(
        text, ["Mark Shmidt & jRL Professional"], "https://example.test"
    )
    assert result.verified
    assert result.price == 10490
    assert result.match_rule == "green_price_before_seller"


def test_single_green_price_is_accepted_when_page_is_unambiguous():
    text = "Название Цена с картой Яндекс Пэй 7 777 ₽ вместо 8 000 ₽ Пэй"
    result = extract_pay_price(text, ["Our Shop"], "https://example.test")
    assert result.verified
    assert result.price == 7777
    assert result.match_rule == "single_green_price_on_page"


def test_multiple_prices_without_seller_match_fail_closed():
    text = (
        "Цена с картой Яндекс Пэй 7 000 ₽ Магазин 1 "
        "Цена с картой Яндекс Пэй 8 000 ₽ Магазин 2"
    )
    result = extract_pay_price(text, ["Our Shop"], "https://example.test")
    assert not result.verified
    assert result.price is None
    assert result.status == "AMBIGUOUS_GREEN_PRICES"


def test_ordinary_price_or_promo_is_not_accepted():
    text = "Цена 5 999 ₽ 10% ПРОМОКОД Пэй"
    result = extract_pay_price(text, ["Our Shop"], "https://example.test")
    assert not result.verified
    assert result.status == "PAY_PRICE_NOT_FOUND"


def test_html_cleanup_keeps_visible_pay_label():
    html = "<div>Цена с картой Яндекс Пэй <b>12&#8239;345 ₽</b></div><script>999</script>"
    text = visible_text(html)
    result = extract_pay_price(text, [], "https://example.test")
    assert result.verified
    assert result.price == 12345


def test_rrp_thresholds():
    assert classify(999, 1000)[0] == "RED"
    assert classify(1000, 1000)[0] == "YELLOW"
    assert classify(1049, 1000)[0] == "YELLOW"
    status, floor = classify(1050, 1000)
    assert status == "OK"
    assert floor == 1050
