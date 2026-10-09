from yandex_agent.scheduled_control import detect_high_prices, add_history, report_lines
from yandex_agent.one_shot_reprice import candidates

def test_anomaly_requires_four_past_prices():
    row = [{"offerId":"403","onDisplay":5500}]
    assert not detect_high_prices(row, {"403":[3000,3000,3000]})
    alerts = detect_high_prices(row, {"403":[3000]*4})
    assert len(alerts)==1 and alerts[0]["sku"]=="403"

def test_historical_baseline_not_rrp():
    row = [{"offerId":"403","onDisplay":4000}]
    assert not detect_high_prices(row, {"403":[3900]*4})

def test_both_zones_single_increment():
    rows=[{"offerId":"403","onDisplay":3885,"basicPrice":7400},
          {"offerId":"GSH9","onDisplay":1776,"basicPrice":3100}]
    plan,skip=candidates(rows,{"GSH9":1718})
    assert [r["status"] for r in plan]==["RED","YELLOW"]
    assert plan[0]["seller_after"]==8026
    assert plan[1]["seller_after"]==3193

def test_history_rolling():
    h=add_history([{"offerId":"403","onDisplay":4100}],{"403":[3000]*28})
    assert len(h["403"])==28 and h["403"][-1]==4100

def test_remaining_red_and_yellow():
    rows=[{"offerId":"403","onDisplay":4000},{"offerId":"GSH9","onDisplay":1750}]
    lines,remaining=report_lines([],[],rows,[],{"GSH9":1718})
    assert len(remaining)==2
    assert any("Ниже РРЦ" in line for line in lines)
