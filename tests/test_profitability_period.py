import os
os.environ.setdefault("OZON_CLIENT_ID","test")
os.environ.setdefault("OZON_API_KEY","test")

from ozon_agent.profitability_period import build_profitability

def test_profitability_uses_ozon_economy_model():
    realization={"1":{
        "offer_id":"A","name":"A","delivered_units":2,
        "buyer_revenue_rub":1000,
        "ozon_discount_points_rub":300,
        "partner_programs_rub":20,
        "economic_sales_base_rub":1320,
        "ozon_sales_fee_rub":500,
        "posting_numbers":["P1"],
    }}
    costs={"1":{
        "acquiring_rub":-20,
        "shipment_processing_rub":-10,
        "logistics_rub":-50,
        "last_mile_rub":-20,
        "placement_rub":0,
        "returns_rub":0,
        "operational_errors_rub":0,
        "promotion_rub":-30,
    }}
    cost_map={"A":{"cost":200.0,"source":"owner","verified":True}}
    rows=build_profitability(realization,costs,cost_map)
    r=rows[0]
    assert r["landed_unit_cost_rub"] == 270
    assert r["cogs_rub"] == 540
    # Ozon costs = 500 + 20 + 10 + 50 + 20 + 30 = 630
    assert r["ozon_costs_total_rub"] == 630
    assert r["contribution_profit_rub"] == 150
