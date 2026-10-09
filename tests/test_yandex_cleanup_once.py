from yandex_agent.cleanup_once import plan_round, final_status

def test_small_gap_targets_rrp_plus_five_percent():
    rows=[{"offerId":"403","onDisplay":4502,"basicPrice":8200}]
    planned,held,_=plan_round(rows,{}, {},{})
    assert len(planned)==1
    assert planned[0]["minimum_buyer"]==4737
    assert planned[0]["seller_after"]>8200

def test_large_gap_gets_capped_step_not_skipped():
    rows=[{"offerId":"A11","onDisplay":2313,"basicPrice":5000}]
    plan,held,_=plan_round(rows,{}, {},{})
    assert len(plan)==1
    assert plan[0]["seller_after"]==6000
    assert not held

def test_never_reprice_without_confirmed_buyer_increase():
    rows=[{"offerId":"403","onDisplay":4502,"basicPrice":9000}]
    plan,held,_=plan_round(rows,{}, {"403":8200},{"403":{"buyer":4502,"expected_seller":8500}})
    assert not plan and held[0]["reason"]=="BUYER_PRICE_DID_NOT_RISE"

def test_respect_total_limit():
    rows=[{"offerId":"A11","onDisplay":2313,"basicPrice":8750}]
    plan,held,_=plan_round(rows,{}, {"A11":5000},{"A11":{"buyer":2000,"expected_seller":8600}})
    assert not plan and held[0]["reason"]=="CUMULATIVE_RISE_LIMIT"

def test_final_check():
    result=final_status([{"offerId":"403","onDisplay":4737},{"offerId":"A11","onDisplay":2313}],{})
    assert [x["sku"] for x in result]==["A11"]
