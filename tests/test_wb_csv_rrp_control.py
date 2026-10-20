from datetime import date
from decimal import Decimal

from ozon_agent.catalog import normalize_sku
from wb_agent.csv_rrp_control import analyze, references, csv_rows, load_reference


def source_row(sku, buyer, base=10000, status="OK"):
    return {"nm_id":"1234567", "sku":sku, "seller_base_rub":str(base),
            "seller_discounted_rub":"8000", "buyer_min_rub":str(buyer) if buyer else "",
            "buyer_public_rub":"", "buyer_wallet_rub":"",
            "read_status":status, "checked_at":"2026-10-20T11:00:00Z"}


def test_promo_price_applied_to_206_only_during_campaign():
    ref={normalize_sku("206"):1000.0, normalize_sku("2020C"):2000.0}
    assert references("206",ref,date(2026,10,20))[:2] == (1000.0,700.0)
    assert references("206",ref,date(2026,11,21))[:2] == (1000.0,1000.0)
    assert references("2020C",ref,date(2026,10,20))[:2] == (2000.0,2000.0)


def test_red_uses_effective_rrp_and_proportional_correction():
    ref={normalize_sku("2025CH"):1000.0}
    row=analyze([source_row("2025CH",900)],ref,as_of=date(2026,10,20))[0]
    assert row["zone"]=="RED"
    assert row["rrp_plus_5"]==1050
    assert row["seller_price_proposed"]==11112
    assert row["action"]=="REVIEW_STEP_TOO_LARGE"


def test_yellow_uses_exact_three_percent_seller_increase():
    ref={normalize_sku("2020C"):1000.0}
    row=analyze([source_row("2020C",1020)],ref,as_of=date(2026,10,20))[0]
    assert row["zone"]=="YELLOW"
    assert row["seller_price_proposed"]==10300
    assert row["action"]=="PROPOSE_UPWARD_CHANGE"


def test_no_price_means_no_proposed_seller_write():
    ref={normalize_sku("2020C"):1000.0}
    row=analyze([source_row("2020C",None,status="ERROR")],ref,as_of=date(2026,10,20))[0]
    assert row["zone"]=="NO_BUYER_PRICE"
    assert row["seller_price_proposed"] is None
    assert row["apply"] is False


def test_out_of_stock_skipped_safely():
    ref={normalize_sku("2020C"):1000.0}
    row=analyze([source_row("2020C",None,status="SKIP_OUT_OF_STOCK")],ref,as_of=date(2026,10,20))[0]
    assert row["zone"]=="NO_STOCK"
    assert row["seller_price_proposed"] is None


def test_clearance_is_excluded():
    ref={normalize_sku("2020C"):1000.0}
    row=analyze([source_row("2020CУЦ",900)],ref,as_of=date(2026,10,20))[0]
    assert row["zone"]=="CLEARANCE"
    assert row["seller_price_proposed"] is None


def test_manual_rrp_applies_without_reference_map():
    ref={normalize_sku("2020C"):1000.0}
    row=analyze([source_row("PET GROOMING KIT",18000,base=25000)],ref,
                as_of=date(2026,10,20))[0]
    assert row["rrp_applied"]==18700.0
    assert row["zone"]=="RED"


def test_alias_and_missing_rrp_are_not_guessed():
    ref={normalize_sku("701с"):1600.0}
    regular,effective,rule=references("701grey",ref,date(2026,10,20))
    assert regular==1600
    assert effective==1600
    assert references("UNKNOWN MODEL",ref,date(2026,10,20))[0] is None



def test_markup_more_than_30_percent_for_rrp_above_1500():
    ref={normalize_sku("2020C"):2000.0}
    normal=analyze([source_row("2020C",2600,base=4000)],ref,
                   as_of=date(2026,10,20))[0]
    flagged=analyze([source_row("2020C",2601,base=4000)],ref,
                    as_of=date(2026,10,20))[0]
    assert normal["control_status"]=="OK"
    assert flagged["control_status"]=="HIGH_MARKUP_REVIEW"
    assert flagged["markup_over_applied_rrp_pct"]==30.05
    assert flagged["action"]=="REVIEW_HIGH_MARKUP_REVIEW"
    assert flagged["seller_price_proposed"] is None
    assert flagged["apply"] is False


def test_cheap_rrp_inclusive_1500_and_markup_alert_bands():
    ref={normalize_sku("2020C"):1500.0}
    source=[
        source_row("2020C",2250),
        source_row("2020C",2251),
        source_row("2020C",2550),
        source_row("2020C",2551),
    ]
    rows=analyze(source,ref,as_of=date(2026,10,20))
    assert [x["control_status"] for x in rows]==[
        "OK","HIGH_MARKUP_REVIEW","HIGH_MARKUP_REVIEW","HIGH_MARKUP_URGENT"
    ]
    assert all(x["apply"] is False for x in rows)


def test_promo_over_five_percent_flagged_even_below_30_markup():
    ref={normalize_sku("206"):13284.0}
    result=analyze([source_row("206",10000,base=20000)],ref,
                   as_of=date(2026,10,20))[0]
    assert result["rrp_regular"]==13284.0
    assert result["rrp_applied"]==9299.0
    assert result["rrp_plus_5"]==9764
    assert result["zone"]=="OK"
    assert result["control_status"]=="PROMO_ABOVE_TARGET"
    assert result["promo_over_target"] is True
    assert result["high_markup_level"]==""
    assert result["action"]=="REVIEW_PROMO_OVER_TARGET"
    assert result["seller_price_proposed"]==19528
    assert result["seller_change_pct"]<0
    assert result["apply"] is False


def test_promo_206_15699_is_flagged_but_never_auto_lowered():
    ref={normalize_sku("206"):13284.0}
    result=analyze([source_row("206",15699,base=22000)],ref,
                   as_of=date(2026,10,20))[0]
    assert result["control_status"]=="PROMO_ABOVE_TARGET"
    assert result["high_markup_level"]=="REVIEW"
    assert result["promo_excess_rub"]==5935.0
    assert result["action"]=="REVIEW_PROMO_OVER_TARGET"
    assert result["seller_change_pct"]<0
    assert result["apply"] is False


def test_ordinary_nonpromo_price_within_30_no_change():
    ref={normalize_sku("2020C"):2000.0}
    result=analyze([source_row("2020C",2500)],ref,
                   as_of=date(2026,10,20))[0]
    assert result["promo_over_target"] is False
    assert result["control_status"]=="OK"
    assert result["action"]==""
