from datetime import datetime, timedelta, timezone
from decimal import Decimal

from ozon_agent.catalog import normalize_sku
from wb_agent.github_daily_correction import (
    buyer_only, load_public_snapshot, enrich, make_plan, scrub_report,
    approval_items, approval_messages
)

NOW = datetime(2026, 10, 20, 15, tzinfo=timezone.utc)


def scan(sku="2020C", price=3500, hours_old=24, state="OK", nm=777):
    return {
        "nm_id": str(nm), "sku": sku,
        "buyer_min_rub": str(price) if state == "OK" else "",
        "read_status": state, "source": "wb_live_dom_min_price" if state == "OK" else "",
        "checked_at": (NOW - timedelta(hours=hours_old)).isoformat()
    }


def seller(sku="2020C", nm=777, base=10000, discount_price=4400):
    return {nm: {"nmID": nm, "vendorCode": sku, "sizes": [
        {"price":base,"discountedPrice":discount_price,"clubDiscount":0}
    ]}}


def test_buyer_only_input_does_not_require_private_seller_prices():
    row=buyer_only(scan())
    assert row["nm_id"] == "777"
    assert "seller_base_rub" not in row
    assert "seller_discounted_rub" not in row


def test_daily_snapshot_accepted_after_twenty_four_hours():
    rows=[scan(hours_old=24,price=3500)]
    ref={normalize_sku("2020C"):3900}
    records, chosen=make_plan(rows,seller(),ref,NOW,{},hours=36)
    assert len(chosen)==1
    assert records[0]["status"]=="READY"
    assert records[0]["delta_pct"]>0
    assert records[0]["seller_before"]==10000


def test_age_over_thirty_six_hours_rejected():
    rows=[scan(hours_old=37,price=3500)]
    records, chosen=make_plan(rows,seller(),{normalize_sku("2020C"):3900},NOW,{},hours=36)
    assert not chosen
    assert records[0]["reason"]=="STALE_BUYER_PRICE"


def test_previous_run_prevents_duplicate_write():
    rows=[scan(hours_old=12,price=3500)]
    rec,chosen=make_plan(rows,seller(),{normalize_sku("2020C"):3900},NOW,{"777":{}},hours=36)
    assert not chosen
    assert rec[0]["reason"]=="PREVIOUS_BATCH_NOT_VERIFIED"


def test_cheap_under_200_percent_never_reduced():
    rows=[scan(sku="MS1014",price=1699)]
    rec, chosen=make_plan(rows,seller(sku="MS1014"),
        {normalize_sku("MS1014"):1000},NOW,{},hours=36)
    assert not chosen
    assert rec[0]["status"]=="OK"
    assert rec[0]["reason"]=="WITHIN_CHEAP_PRICE_BAND"


def test_no_price_does_not_get_filled_from_seller():
    rows=[scan(state="ERROR")]
    rec, chosen=make_plan(rows,seller(),{normalize_sku("2020C"):3900},NOW,{},hours=36)
    assert not chosen
    assert rec[0]["reason"]=="NOT_VERIFIED_OR_OUT_OF_STOCK"


def test_public_summary_does_not_expose_seller_price():
    rec, chosen=make_plan([scan()],seller(),{normalize_sku("2020C"):3900},NOW,{},hours=36)
    summary=scrub_report(rec,chosen,mode="DRY_RUN")
    assert "seller_before" not in str(summary)
    assert "seller_proposed" not in str(summary)
    assert "10000" not in str(summary)


def test_source_validation_prevents_fake_price():
    import pytest
    row=scan()
    row["source"]="not_wb_source"
    with pytest.raises(ValueError):
        buyer_only(row)


def test_unredacted_seller_prices_are_rejected_from_public_git_snapshot():
    import pytest
    row=scan()
    row["seller_base_rub"]="10000"
    with pytest.raises(ValueError, match="unredacted"):
        buyer_only(row)


def test_cheap_two_times_is_allowed_but_above_requires_approval():
    ref={normalize_sku("MS1014"):1000.0}
    for price, status in [(2000,"OK"),(2001,"MANUAL_REVIEW")]:
        rec, chosen=make_plan([scan(sku="MS1014",price=price)],
            seller(sku="MS1014"),ref,NOW,{},hours=36)
        assert rec[0]["status"] == status
        assert not chosen


def test_over_thirty_percent_expensive_requires_private_approval():
    ref={normalize_sku("2020C"):3000.0}
    r=scan(sku="2020C",price=4200)
    rows, chosen=make_plan([r],seller(sku="2020C"),ref,NOW,{},hours=36)
    assert not chosen
    assert rows[0]["status"]=="MANUAL_REVIEW"
    assert rows[0]["reason"]=="HIGH_MARKUP"
    assert len(approval_items(rows))==1
    msg=approval_messages(rows)[0]
    assert "2020C" in msg and "30%" in msg
    assert "10000" not in msg


def test_already_changed_high_markup_skipped_from_approval():
    ref={normalize_sku("2020C"):3000.0}
    r=scan(sku="2020C",price=4200)
    rows, chosen=make_plan([r],seller(sku="2020C"),ref,NOW,{"777":{"state":"SUBMITTED_PENDING_VERIFICATION"}},hours=36)
    assert not chosen
    assert rows[0]["reason"]=="PREVIOUS_BATCH_NOT_VERIFIED"
    assert not approval_items(rows)


def test_promo_expensive_markup_also_requires_approval():
    ref={normalize_sku("206"):13284.0}
    r=scan(sku="206",price=15699)
    rows, chosen=make_plan([r],seller(sku="206",base=19500,discount_price=19500),ref,NOW,{},hours=36)
    assert not chosen
    assert rows[0]["status"]=="MANUAL_REVIEW"
    assert rows[0]["rrp_active"]==9299
    assert len(approval_items(rows))==1
