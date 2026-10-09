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

def test_exact_red_shortfall():
    data=[{"offerId":"403","onDisplay":"3885","basicPrice":1000}]
    plan, skipped=candidates(data,{})
    assert len(plan)==1
    assert plan[0]["seller_after"]==1626
    assert not skipped

def test_no_clearance():
    plan, skip=candidates([{"offerId":"403УЦ","onDisplay":"100","basicPrice":500}],{})
    assert not plan
