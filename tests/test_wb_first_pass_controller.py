from datetime import datetime, timedelta, timezone
from decimal import Decimal

from ozon_agent.catalog import normalize_sku
from wb_agent.first_pass_controller import propose

NOW = datetime(2026, 10, 20, 12, 20, tzinfo=timezone.utc)

def snapshot(sku="MS1014",buyer=1690,base=2000,disc=1800, nm=123456):
    return {"nm_id":str(nm),"sku":sku,"seller_base_rub":str(base),
            "seller_discounted_rub":str(disc),
            "buyer_min_rub":str(buyer),
            "read_status":"OK", "checked_at":(NOW-timedelta(minutes=2)).isoformat()}

def catalog(row):
    nm=int(row["nm_id"])
    return {nm:{"nmID":nm,"vendorCode":row["sku"],
                "sizes":[{"price":int(row["seller_base_rub"]),
                          "discountedPrice":float(row["seller_discounted_rub"]),
                          "clubDiscount":0}]}}


def test_cheap_goods_up_to_200_percent_allowed_no_reduction():
    ref={normalize_sku("MS1014"):1000.0}
    for price in [1050,1250,1500,2000]:
        row=snapshot(buyer=price)
        result=propose(row,catalog(row),ref,NOW)
        assert result["status"]=="OK"
        assert result["reason"]=="WITHIN_CHEAP_PRICE_BAND"
        assert result["seller_before"]==2000
        assert result["max_allowed"]==2000
        assert "seller_proposed" not in result


def test_cheap_goods_above_200_manual_review_no_autolowering():
    ref={normalize_sku("MS1014"):1000.0}
    row=snapshot(buyer=2001)
    result=propose(row,catalog(row),ref,NOW)
    assert result["status"]=="MANUAL_REVIEW"
    assert result["reason"]=="CHEAP_ABOVE_200_PCT_RRP"
    assert "seller_proposed" not in result
    assert result["apply"] is False


def test_cheap_promo_based_on_applied_rrp():
    ref={normalize_sku("MS702"):1374.0}
    row=snapshot(sku="MS702",buyer=1926,base=2200,disc=2100)
    result=propose(row,catalog(row),ref,NOW)
    assert result["rrp_active"]==963.0
    assert result["max_allowed"]==1926
    assert result["status"]=="OK"


def test_cheap_promo_above_200_manual_review():
    ref={normalize_sku("MS702"):1374.0}
    row=snapshot(sku="MS702",buyer=1927,base=2200,disc=2100)
    result=propose(row,catalog(row),ref,NOW)
    assert result["status"]=="MANUAL_REVIEW"
    assert result["reason"]=="CHEAP_ABOVE_200_PCT_RRP"


def test_expensive_normal_band_and_capped_downward_step():
    ref={normalize_sku("2020C"):3000.0}
    row=snapshot(sku="2020C",buyer=3150,base=5000,disc=4000)
    assert propose(row,catalog(row),ref,NOW)["status"]=="OK"
    row["buyer_min_rub"]="3500"
    r=propose(row,catalog(row),ref,NOW)
    assert r["status"]=="READY"
    assert r["seller_before"]==5000
    assert r["seller_proposed"]==4750
    assert r["delta_pct"]==-5
    assert r["reason"]=="STEP_CAPPED"


def test_current_price_changed_since_scan_stops_write():
    ref={normalize_sku("2020C"):3000.0}
    row=snapshot(sku="2020C",buyer=3500,base=5000,disc=4000)
    listing=catalog(row)
    listing[123456]["sizes"][0]["price"]=5100
    r=propose(row,listing,ref,NOW)
    assert r["reason"]=="BASE_CHANGED_SINCE_CAPTURE"
    assert "seller_proposed" not in r


def test_stale_browser_prices_are_not_used_for_live_changes():
    ref={normalize_sku("2020C"):3000.0}
    row=snapshot(sku="2020C",buyer=3500,base=5000,disc=4000)
    row["checked_at"]=(NOW-timedelta(minutes=20)).isoformat()
    r=propose(row,catalog(row),ref,NOW)
    assert r["reason"]=="STALE_BUYER_PRICE"
    assert r["status"]=="SKIP"


def test_unavailable_goods_and_missing_rrp_never_change():
    ref={normalize_sku("2020C"):3000.0}
    row=snapshot(sku="2020C",buyer=3500,base=5000,disc=4000)
    row["read_status"]="SKIP_OUT_OF_STOCK"
    assert propose(row,catalog(row),ref,NOW)["reason"]=="NOT_VERIFIED_OR_OUT_OF_STOCK"
    row["read_status"]="OK"
    assert propose(row,catalog(row),{},NOW)["reason"]=="MISSING_RRP"


def test_promo_above_30_percent_requires_owner_approval():
    ref={normalize_sku("206"):13284.0}
    row=snapshot(sku="206",buyer=15699,base=19500,disc=19500)
    result=propose(row,catalog(row),ref,NOW)
    assert result["rrp_active"]==9299.0
    assert result["status"]=="MANUAL_REVIEW"
    assert result["reason"]=="HIGH_MARKUP"
    assert result["markup_pct"]>30
    assert "seller_proposed" not in result
