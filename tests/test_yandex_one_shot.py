from yandex_agent.one_shot_reprice import candidates

def test_one_shot_red_and_yellow():
    data = [
        {"offerId":"403", "onDisplay":"3885", "basicPrice":5000},
        {"offerId":"GSH9", "onDisplay":"1776", "basicPrice":3000},
    ]
    mapped={"GSH9":1718}
    plan, skipped=candidates(data,mapped)
    by_sku={x["sku"]:x for x in plan}
    assert by_sku["403"]["seller_after"] == 5626
    assert by_sku["GSH9"]["seller_after"] == 3090
    assert not skipped

def test_cap_and_incomplete_price():
    data=[{"offerId":"403","onDisplay":"3885","basicPrice":1000},
          {"offerId":"201","onDisplay":"","basicPrice":5000}]
    plan, skipped=candidates(data,{})
    assert not plan
    assert skipped[0]["reason"] == "RISE_EXCEEDS_15_PERCENT"

def test_no_clearance():
    plan, skip=candidates([{"offerId":"403УЦ","onDisplay":"100","basicPrice":500}],{})
    assert not plan
