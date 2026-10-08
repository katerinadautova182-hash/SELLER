import os
os.environ.setdefault("OZON_CLIENT_ID","test")
os.environ.setdefault("OZON_API_KEY","test")

from ozon_agent.profitability_period import build_profitability, build_summary


def test_profitability_uses_net_units_and_135_cost():
    finance=[{
        "ozon_sku":"1","offer_id":"A","name":"A",
        "seller_price_rub":1000,
        "sale_commission_rub":-200,
        "delivery_rub":-50,
        "item_fees_rub":-20,
        "item_compensation_rub":0,
        "direct_finance_net_rub":730,
    }]
    qty={"1":{"delivered_units":2,"returned_units":1,"net_units":1}}
    costs={"A":{"cost":400.0,"source":"owner","verified":True}}
    rows=build_profitability(finance,qty,costs)
    r=rows[0]
    assert r["landed_unit_cost_rub"] == 540
    assert r["cogs_rub"] == 540
    assert r["contribution_profit_rub"] == 190
    assert r["contribution_margin_percent"] == 19
    assert r["status"] == "MEDIUM"

    s=build_summary(rows,-50)
    assert s["sku_contribution_profit_rub"] == 190
    assert s["platform_contribution_after_non_item_rub"] == 140
