"""One-shot, locally executed WB first correction cycle.

Needs a freshly captured WB Auto CSV, a private RRP CSV or RRP_MAP_B64,
and a WB Prices/Discounts token. NEVER commits or uploads price snapshots
to the public GitHub repository. Never alters WB prices without --apply.

Corrections use each SKU's own observed WB buyer/seller price ratio.
Updates are capped to 5% of the seller base in one cycle, and processed once
until explicitly verified in a later browser scan. Special promotional RRPs
are resolved by the shared promo_policy.py rules.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path

from ozon_agent.business_rules import is_clearance_sku
from ozon_agent.catalog import normalize_sku
from .csv_rrp_control import csv_rows, load_reference, numeric, price_day, references

API_ROOT = "https://discounts-prices-api.wildberries.ru"
LEDGER = Path(".state/wb-first-pass-ledger.json")
MAX_AGE_MINUTES = 15
MAX_BATCH = 30
MAX_CHANGE_PCT = Decimal("5")
MAX_API_RETRIES = 3


def cents_equal(a, b, *, tolerance=Decimal("0.50")):
    p, q = numeric(a), numeric(b)
    return p is not None and q is not None and abs(p-q) <= tolerance


def posted(token, path, payload=None, method="GET"):
    url = API_ROOT + path
    for attempt in range(MAX_API_RETRIES):
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=body, method=method,
            headers={"Authorization":token,"Accept":"application/json",
                **({"Content-Type":"application/json"} if body is not None else {})})
        try:
            with urllib.request.urlopen(request, timeout=35) as response:
                result=json.load(response)
        except urllib.error.HTTPError as exc:
            # Never retry a price WRITE whose server-side result could
            # already be committed. A failed confirmation is not license
            # to submit another upload.
            if method=="GET" and exc.code in (429, 500, 502, 503, 504) and attempt+1<MAX_API_RETRIES:
                time.sleep(2+attempt*3)
                continue
            raise RuntimeError("WB API HTTP "+str(exc.code)+": "+exc.read(180).decode("utf-8", errors="replace")) from None
        if not isinstance(result, dict) or result.get("error") is True:
            raise RuntimeError("WB API returned invalid/error response: "+str(result)[:150])
        return result
    raise RuntimeError("WB Seller API retries exhausted")


def catalog_by_id(token):
    goods={}
    for offset in range(0, 100000, 1000):
        query=urllib.parse.urlencode({"limit":1000,"offset":offset})
        response=posted(token,"/api/v2/list/goods/filter?"+query)
        batch=response.get("data",{}).get("listGoods")
        if not isinstance(batch,list):
            raise RuntimeError("WB Seller catalog listGoods is missing")
        for item in batch:
            try:
                nm=int(item.get("nmID"))
            except (TypeError, ValueError):
                continue
            if nm in goods:
                raise RuntimeError("Duplicate WB nmID in current Seller API catalog")
            goods[nm]=item
        if len(batch)<1000:
            return goods
        time.sleep(1)
    raise RuntimeError("WB Seller catalog safety limit exceeded")


def one_base(item):
    if item.get("editableSizePrice") is True:
        return None, "SIZE_PRICES"
    base=set()
    sizes=item.get("sizes") or []
    if not sizes:
        return None,"NO_SIZES"
    for size in sizes:
        if size.get("editableSizePrice") is True:
            return None,"SIZE_PRICES"
        price=numeric(size.get("price"))
        if price is None or price != price.to_integral_value():
            return None,"NO_BASE_PRICE"
        base.add(int(price))
    if len(base)!=1:
        return None,"DIFFERENT_SIZE_PRICES"
    if any(int(size.get("clubDiscount") or 0)>0 for size in sizes):
        return None,"CLUB_DISCOUNT"
    if int(item.get("clubDiscount") or 0)>0:
        return None,"CLUB_DISCOUNT"
    return base.pop(),"OK"


def propose(row, item, rrp_map, now, *, max_age_minutes=MAX_AGE_MINUTES,
            max_change_pct=MAX_CHANGE_PCT):
    result={"nm_id":row.get("nm_id"),"sku":row.get("sku"),
        "status":"SKIP","reason":"","apply":False,
        "observed_buyer":row.get("buyer_min_rub"),"seller_before":None}
    def refuse(reason):
        result["reason"]=reason
        return result

    if row.get("read_status")!="OK":
        return refuse("NOT_VERIFIED_OR_OUT_OF_STOCK")
    try:
        nm=int(row.get("nm_id"))
    except (ValueError, TypeError):
        return refuse("INVALID_NMID")
    buyer=numeric(row.get("buyer_min_rub"))
    if buyer is None:
        return refuse("MISSING_BUYER_PRICE")
    sku=str(row.get("sku") or "").strip()
    if is_clearance_sku(sku):
        return refuse("CLEARANCE")
    current=item.get(nm)
    if current is None or normalize_sku(sku)!=normalize_sku(current.get("vendorCode")):
        return refuse("SELLER_ARTICLE_MISMATCH")
    checked_at=str(row.get("checked_at") or "")
    try:
        stamp=datetime.fromisoformat(checked_at.replace("Z","+00:00"))
        if stamp.tzinfo is None:
            return refuse("NAIVE_CAPTURE_TIME")
        age=(now-stamp.astimezone(timezone.utc)).total_seconds()
    except ValueError:
        return refuse("INVALID_CAPTURE_TIME")
    if age < -120 or age > max_age_minutes*60:
        return refuse("STALE_BUYER_PRICE")

    regular, active, rrp_rule=references(sku,rrp_map,price_day(row))
    if active is None:
        return refuse("MISSING_RRP")
    base, base_reason=one_base(current)
    if base is None:
        return refuse(base_reason)
    if not cents_equal(base,row.get("seller_base_rub")):
        return refuse("BASE_CHANGED_SINCE_CAPTURE")
    current_sizes=current.get("sizes") or []
    displayed=row.get("seller_discounted_rub")
    if any(not cents_equal(s.get("discountedPrice"),displayed) for s in current_sizes):
        return refuse("SELLER_DISCOUNT_CHANGED")

    applied=Decimal(str(active))
    center=(applied*Decimal("1.05")).quantize(Decimal("1"),rounding=ROUND_CEILING)
    low=(applied*Decimal("1.02")).quantize(Decimal("1"),rounding=ROUND_CEILING)
    high=(applied*Decimal("1.08")).quantize(Decimal("1"),rounding=ROUND_FLOOR)
    result.update(seller_before=base,rrp_regular=regular,rrp_active=float(applied),
        is_promo=regular!=active,ideal_buyer=int(center),
        min_allowed=int(low),max_allowed=int(high),
        buyer_observed=int(buyer),
        buyer_discount_coeff=round(float(buyer/numeric(displayed)),5) if numeric(displayed) else None,
        buyer_base_coeff=round(float(buyer/Decimal(base)),5),
        rrp_match_rule=rrp_rule)

    # Owner policy: low-RRP goods may sell as high as 200% of their
    # effective RRP, including during the promotional period. Do NOT lower
    # their price just to force them into the generic +5% (+/-3%) corridor.
    if applied <= 1500:
        cheap_high=(applied*Decimal("2.00")).quantize(
            Decimal("1"),rounding=ROUND_FLOOR)
        result["max_allowed"]=int(cheap_high)
        result["pricing_policy"]="CHEAP_UP_TO_200_PCT_RRP"
        if low <= buyer <= cheap_high:
            result["status"]="OK";result["reason"]="WITHIN_CHEAP_PRICE_BAND"
            return result
        if buyer > cheap_high:
            result["status"]="MANUAL_REVIEW"
            result["reason"]="CHEAP_ABOVE_200_PCT_RRP"
            return result
    elif low <= buyer <= high:
        result["status"]="OK";return result
    if numeric(displayed) is None:
        return refuse("NO_SELLER_DISCOUNTED_PRICE")
    ratio=buyer/numeric(displayed)
    if ratio<Decimal("0.65") or ratio>Decimal("1.05"):
        return refuse("ABNORMAL_BUYER_COEFFICIENT")

    if buyer>high:
        markup=(buyer/applied-1)*100
        # Above +30% of the *effective* RRP always requires approval,
        # including an active promotional RRP; never auto-reduce it.
        if applied>1500 and markup>30:
            result["status"]="MANUAL_REVIEW"
            result["reason"]="HIGH_MARKUP"
            result["markup_pct"]=round(float(markup),2)
            return result

    change=int((Decimal(base)*center/buyer).quantize(
        Decimal("1"),rounding=ROUND_CEILING if buyer<low else ROUND_FLOOR))
    # One bounded step, not full repricing when WB price is far from target.
    max_up=int((Decimal(base)*(1+max_change_pct/100)).quantize(Decimal("1"),rounding=ROUND_FLOOR))
    min_down=int((Decimal(base)*(1-max_change_pct/100)).quantize(Decimal("1"),rounding=ROUND_CEILING))
    change=max(min_down,min(max_up,change))
    if change==base:
        return refuse("ROUNDING_ZERO_CHANGE")
    result.update(status="READY",reason="STEP_CAPPED" if change !=
        int((Decimal(base)*center/buyer).quantize(
            Decimal("1"), rounding=ROUND_CEILING if buyer<low else ROUND_FLOOR)) else "TARGETED",
        seller_proposed=change,delta_rub=change-base,
        delta_pct=round(100*(change/base-1),3))
    return result


def read_ledger(path=LEDGER):
    try:
        record=json.loads(path.read_text(encoding="utf-8"))
        if isinstance(record,dict):
            return record
    except (FileNotFoundError,ValueError):
        pass
    return {}


def main(argv=None):
    p=argparse.ArgumentParser(description="One bounded WB seller-price correction pass")
    p.add_argument("buyer_csv")
    p.add_argument("--rrp-csv", help="Private CSV export of Google Sheet Base RRP")
    p.add_argument("--apply",action="store_true",help="Use official WB Seller API to submit safe adjustments")
    p.add_argument("--max-candidates",type=int,default=30)
    p.add_argument("--max-age-minutes",type=int,default=15)
    p.add_argument("--max-step-pct",type=float,default=5)
    p.add_argument("--output",default="artifacts/wb-first-pass-report.json")
    args=p.parse_args(argv)
    if args.max_candidates<1 or args.max_candidates>100 or not (0.5<=args.max_step_pct<=5):
        p.error("First pass safety: 1-100 candidates, change 0.5-5% per SKU")
    if args.max_age_minutes<1 or args.max_age_minutes>30:
        p.error("First pass safety: max age 1-30 minutes")

    reference=load_reference(rrp_csv=args.rrp_csv)
    rows=csv_rows(args.buyer_csv)
    if not rows or not {"nm_id","sku","seller_base_rub","seller_discounted_rub",
        "buyer_min_rub","read_status","checked_at"}.issubset(rows[0]):
        raise RuntimeError("Unrecognized WB browser scan CSV")
    if not reference:
        raise RuntimeError("Private RRP reference is empty")
    token=os.getenv("WB_API_KEY","").strip()
    if not token:
        if not sys.stdin.isatty():
            raise RuntimeError("WB_API_KEY must be set for noninteractive runs")
        token=getpass.getpass("WB API token (Prices and Discounts; input hidden): ").strip()
    if not token:
        raise RuntimeError("WB API token missing")
    catalog=catalog_by_id(token)
    now=datetime.now(timezone.utc)
    ledger=read_ledger()
    max_age=args.max_age_minutes
    max_step=Decimal(str(args.max_step_pct))
    evaluated=[propose(row,catalog,reference,now,max_age_minutes=max_age,
        max_change_pct=max_step) for row in rows]
    for item in evaluated:
        nm=str(item.get("nm_id") or "")
        if item.get("status")=="READY" and nm in ledger:
            item["status"]="SKIP"
            item["reason"]="PREVIOUS_BATCH_NOT_VERIFIED"
    eligible=[r for r in evaluated if r["status"]=="READY"]
    # First live run restricts blast radius and requires separately re-scanning WB afterwards.
    # Prioritise closest-to-target prices, then smaller seller base changes.
    eligible.sort(key=lambda x:(abs(x["ideal_buyer"]/x["buyer_observed"]-1),
        abs(x["delta_pct"]),int(x["nm_id"])))
    chosen=eligible[:args.max_candidates]
    for r in eligible[args.max_candidates:]:
        r["status"]="DEFERRED_FIRST_PASS_CAP"
    output=Path(args.output)
    output.parent.mkdir(parents=True,exist_ok=True)
    summary={"mode":"APPLY" if args.apply else "DRY_RUN","total":len(rows),
        "counts":dict(Counter(r["status"] for r in evaluated)),
        "upload_requested":False,"upload_id":None,"accepted_as_applied":False,
        "selected":len(chosen),"max_step_pct":args.max_step_pct,
        "as_of":now.isoformat(),"candidates":[r["nm_id"] for r in chosen]}
    report={"summary":summary,"rows":evaluated}
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
    for r in chosen:
        print(f"  {r['sku']} (WB {r['nm_id']}): покупатель "
              f"{r['buyer_observed']} ₽; цель {r['ideal_buyer']} ₽; "
              f"продавец {r['seller_before']} → {r['seller_proposed']} ₽ "
              f"({r['delta_pct']:+.2f}%)",flush=True)
    if not args.apply or not chosen:
        return 0
    if not sys.stdin.isatty():
        raise RuntimeError("First live upload requires interactive review on local PC")
    print("Changes will be submitted ONCE. WB may quarantine some prices.")
    if input('Для отправки в WB введите ПОДТВЕРЖДАЮ: ').strip() != 'ПОДТВЕРЖДАЮ':
        print("Cancelled; NO WB price updates were sent.")
        return 0
    # Last-second preflight: refetch seller API, refuse any changed baseline.
    refreshed=catalog_by_id(token)
    for row in chosen:
        item=refreshed.get(int(row["nm_id"]))
        base,reason=one_base(item or {})
        if base != row["seller_before"]:
            raise RuntimeError("SELLER_PRICE_CHANGED_PRE_WRITE: "+str(row["nm_id"]))
        if item is None or any(
            not cents_equal(size.get("discountedPrice"),
                            next((r.get("seller_discounted_rub") for r in rows
                                  if str(r.get("nm_id"))==str(row["nm_id"])),None))
            for size in item.get("sizes") or []
        ):
            raise RuntimeError("SELLER_DISCOUNT_CHANGED_PRE_WRITE: "+str(row["nm_id"]))
    data=[{"nmID":int(r["nm_id"]),"price":int(r["seller_proposed"])} for r in chosen]
    # Persist attempts BEFORE sending POST to prevent an uncertain network timeout
    # from silently causing another price change on the next run.
    for row in chosen:
        ledger[str(row["nm_id"])]={"submitted_at":now.isoformat(),
            "seller_before":row["seller_before"],"seller_proposed":row["seller_proposed"],
            "state":"REQUEST_IN_PROGRESS"}
    LEDGER.parent.mkdir(parents=True,exist_ok=True)
    LEDGER.write_text(json.dumps(ledger,ensure_ascii=False,indent=2),encoding="utf-8")
    response=posted(token,"/api/v2/upload/task",{"data":data},method="POST")
    upload_id=(response.get("data") or {}).get("id")
    if not upload_id:
        raise RuntimeError("WB upload has no task id; state locked for manual checking")
    for row in chosen:
        ledger[str(row["nm_id"])]["state"]="SUBMITTED_PENDING_WB_VERIFICATION"
        ledger[str(row["nm_id"])]["upload_id"]=upload_id
        row["status"]="SUBMITTED_PENDING_WB_VERIFICATION"
    LEDGER.write_text(json.dumps(ledger,ensure_ascii=False,indent=2),encoding="utf-8")
    summary.update(upload_requested=True,upload_id=upload_id,
        accepted_as_applied=False)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print("WB has ACCEPTED upload task "+str(upload_id)+
        ". Price changes are NOT yet verified as applied; run fresh browser scan and check quarantine.")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
