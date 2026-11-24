from ozon_agent.pricing_policy import (
    is_main_equipment,
    steering_price,
    target_band,
    price_band_deviation,
)
from ozon_agent.price_alert import collect_violations


def test_fixed_ruble_band_does_not_scale_with_rrp():
    assert target_band(20000, "2020C-B") == (20200.0, 21000.0)
    assert target_band(4500, "MS403") == (4700.0, 5500.0)
    assert steering_price(4500, "MS403") == 4700.0


def test_accessory_and_unknown_skus_keep_separate_rules():
    assert target_band(1902, "JRL-BR1-32") is None
    assert target_band(10000, "UNKNOWN-ITEM") is None
    assert target_band(100, "MS701-УЦ") is None
    assert not is_main_equipment("MS403", name="Насадка для плойки")
    assert is_main_equipment("UNKNOWN", name="Профессиональный фен для волос")


def test_ozon_yellow_only_for_known_equipment():
    snapshot = {"items": [
        {"offer_id": "MS701", "customer_price": 10200, "customer_price_verified": True,
         "eligibility": "ELIGIBLE"},
        {"offer_id": "JRL-BR1-32", "customer_price": 10020, "customer_price_verified": True,
         "eligibility": "ELIGIBLE"},
    ]}
    red, yellow, stats = collect_violations(snapshot, {"MS701": 10100, "BR132MM": 10000})
    assert not red
    assert [r["offer_id"] for r in yellow] == ["MS701"]
    assert stats["checked"] == 2


def test_yandex_main_equipment_uses_fixed_ruble_floor():
    from yandex_agent.price_check import classify
    assert classify(19999, 20000, "2020C-B")[0] == "RED"
    assert classify(20050, 20000, "2020C-B") == ("YELLOW", 20200.0)
    assert classify(20200, 20000, "2020C-B") == ("OK", 20200.0)
    assert classify(20020, 20000, "JRL-BR1-32") == ("OK", 20000.0)


def test_both_ends_of_equipment_band_allow_ten_rubles():
    assert price_band_deviation(10189, 10000, "MS701") == "BELOW"
    assert price_band_deviation(10190, 10000, "MS701") == "IN_BAND"
    assert price_band_deviation(10200, 10000, "MS701") == "IN_BAND"
    assert price_band_deviation(11000, 10000, "MS701") == "IN_BAND"
    assert price_band_deviation(11010, 10000, "MS701") == "IN_BAND"
    assert price_band_deviation(11011, 10000, "MS701") == "ABOVE"
    assert price_band_deviation(15000, 10000, "JRL-BR1-32") is None


def test_ozon_flags_only_material_above_cap_and_includes_it_in_fingerprint():
    from ozon_agent.price_alert import fingerprint
    snapshot = {"items": [
        {"offer_id": "MS701", "customer_price": 11010,
         "customer_price_verified": True, "eligibility": "ELIGIBLE"},
    ]}
    _, _, safe = collect_violations(snapshot, {"MS701": 10000})
    assert safe["above_target"] == []
    snapshot["items"][0]["customer_price"] = 11011
    red, yellow, high = collect_violations(snapshot, {"MS701": 10000})
    assert not red and not yellow
    assert [x["offer_id"] for x in high["above_target"]] == ["MS701"]
    assert fingerprint(red, yellow, safe) != fingerprint(red, yellow, high)
