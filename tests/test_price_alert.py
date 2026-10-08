import os
os.environ.setdefault("OZON_CLIENT_ID","test")
os.environ.setdefault("OZON_API_KEY","test")

from ozon_agent.price_alert import (
    detect_high_price_anomalies,
    update_price_history,
    _high_price_rule,
    format_message,
)

def item(offer, price):
    return {
        "offer_id": offer,
        "customer_price": price,
        "customer_price_verified": True,
        "eligibility": "ELIGIBLE",
    }

def test_threshold_bands():
    assert _high_price_rule(1000) == (0.35, 300.0)
    assert _high_price_rule(3000) == (0.30, 500.0)
    assert _high_price_rule(10000) == (0.25, 1000.0)
    assert _high_price_rule(20000) == (0.20, 2000.0)

def test_intentionally_high_small_item_is_not_anomaly_if_history_is_high():
    snapshot={"items":[item("J304 YELLOW", 1390)]}
    history={"J304 YELLOW":[1290,1390,1290,1390,1290,1390]}
    assert detect_high_price_anomalies(snapshot, history) == []

def test_small_item_large_spike_becomes_white():
    snapshot={"items":[item("J304 YELLOW", 1990)]}
    history={"J304 YELLOW":[1290,1390,1290,1390,1290,1390]}
    white=detect_high_price_anomalies(snapshot, history)
    assert len(white) == 1
    assert white[0]["offer_id"] == "J304 YELLOW"
    assert white[0]["baseline_price"] == 1340.0
    assert white[0]["delta"] == 650.0

def test_expensive_item_needs_percent_and_absolute_gap():
    history={"2020C-B":[20000,20500,20000,20500]}
    # +19.5% from median and < threshold: no alert
    assert detect_high_price_anomalies(
        {"items":[item("2020C-B",24200)]}, history
    ) == []
    # Clear >20% and >2000 rise: WHITE
    white=detect_high_price_anomalies(
        {"items":[item("2020C-B",26000)]}, history
    )
    assert len(white) == 1

def test_history_requires_four_previous_samples():
    snapshot={"items":[item("A",2000)]}
    assert detect_high_price_anomalies(snapshot, {"A":[1000,1000,1000]}) == []

def test_history_keeps_last_28_samples():
    history={"A":[1000]*28}
    updated=update_price_history({"items":[item("A",1100)]}, history)
    assert len(updated["A"]) == 28
    assert updated["A"][-1] == 1100

def test_message_contains_white_section():
    white=[{
        "offer_id":"A",
        "customer_price":2000,
        "baseline_price":1000,
        "delta":1000,
        "delta_pct":100.0,
        "history_samples":10,
    }]
    msg=format_message([],[],{},white)
    assert "⚪ Аномально высокая цена: 1" in msg
    assert "A: 2 000 ₽" in msg
