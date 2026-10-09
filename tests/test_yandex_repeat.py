from yandex_agent.repeat_reprice import eligible

def test_limit_cumulative_growth():
    items=[{"sku":"A","seller_before":100,"seller_after":132,"buyer_before":75},
           {"sku":"B","seller_before":100,"seller_after":110,"buyer_before":90}]
    approved,held=eligible(items,{"A":100,"B":100},{})
    assert [x["sku"] for x in approved]==["B"]
    assert held[0]["reason"]=="CUMULATIVE_30_PERCENT_LIMIT"

def test_refuse_repeat_without_storefront_improvement():
    items=[{"sku":"A","seller_before":110,"seller_after":115,"buyer_before":70}]
    approved,held=eligible(items,{"A":100},{"A":70})
    assert not approved
    assert held[0]["reason"]=="NO_CONFIRMED_STORE_PRICE_RESPONSE"
