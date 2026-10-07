from ozon_agent.auto_fix_yellow import plan_yellow_updates


def test_plans_only_yellow_and_raises_seller_price_3_percent():
    snapshot = {
        "items": [
            {
                "offer_id": "A",
                "eligibility": "ELIGIBLE",
                "customer_price_verified": True,
                "customer_price": 102.0,
                "current_price": 200.0,
                "ozon_min_price": 0.0,
            },
            {
                "offer_id": "B",
                "eligibility": "ELIGIBLE",
                "customer_price_verified": True,
                "customer_price": 90.0,
                "current_price": 200.0,
                "ozon_min_price": 0.0,
            },
            {
                "offer_id": "C-УЦ",
                "eligibility": "CLEARANCE",
                "customer_price_verified": False,
                "customer_price": None,
                "current_price": 200.0,
                "ozon_min_price": 0.0,
            },
        ]
    }
    rrp = {"A": 100.0, "B": 100.0, "CУЦ": 100.0}
    planned = plan_yellow_updates(snapshot, rrp)
    assert len(planned) == 1
    assert planned[0]["offer_id"] == "A"
    assert planned[0]["seller_price_before"] == 200.0
    assert planned[0]["seller_price_after"] == 206.0
