"""Ozon realization layer matching Seller 'Economy' period logic.

Sales, Ozon discount points, partner co-investments, nominal sales fee and
delivered quantity come from /v1/finance/realization/posting and are filtered
by order.created_date inside the selected period.

This avoids double-counting posting commission fields repeated across accruals.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime


def _date(value: str):
    return datetime.strptime(value, "%Y-%m-%d").date()


def fetch_realization_posting_month(client, year: int, month: int) -> list[dict]:
    data = client.post("/v1/finance/realization/posting", {"month": month, "year": year})
    return data.get("rows") or []


def aggregate_realization_period(client, date_from: str, date_to: str) -> dict[str, dict]:
    start=_date(date_from)
    end=_date(date_to)
    months=[]
    cur=(start.year,start.month)
    while True:
        months.append(cur)
        if cur==(end.year,end.month):
            break
        y,m=cur
        cur=(y+1,1) if m==12 else (y,m+1)

    out=defaultdict(lambda:{
        "offer_id":"",
        "name":"",
        "delivered_units":0.0,
        "buyer_revenue_rub":0.0,
        "ozon_discount_points_rub":0.0,
        "partner_programs_rub":0.0,
        "economic_sales_base_rub":0.0,
        "ozon_sales_fee_rub":0.0,
        "commission_ratio":0.0,
    })

    for year,month in months:
        for row in fetch_realization_posting_month(client,year,month):
            order=row.get("order") or {}
            created=str(order.get("created_date") or "").strip()
            if not created:
                continue
            try:
                created_date=_date(created)
            except ValueError:
                continue
            if created_date < start or created_date > end:
                continue

            item=row.get("item") or {}
            sku=str(item.get("sku") or "").strip()
            dc=row.get("delivery_commission") or {}
            if not sku or not dc:
                continue

            qty=float(dc.get("quantity") or 0)
            if qty <= 0:
                continue

            r=out[sku]
            r["offer_id"]=str(item.get("offer_id") or "")
            r["name"]=str(item.get("name") or "")
            r["delivered_units"] += qty
            r["buyer_revenue_rub"] += float(dc.get("amount") or 0)
            r["ozon_discount_points_rub"] += float(dc.get("bonus") or 0)
            r["partner_programs_rub"] += (
                float(dc.get("bank_coinvestment") or 0)
                + float(dc.get("stars") or 0)
                + float(dc.get("pick_up_point_coinvestment") or 0)
                + float(dc.get("compensation") or 0)
            )
            seller_price=float(row.get("seller_price_per_instance") or 0)
            r["economic_sales_base_rub"] += seller_price * qty
            r["ozon_sales_fee_rub"] += float(dc.get("standard_fee") or 0)
            r["commission_ratio"]=float(row.get("commission_ratio") or 0)

    return {
        sku:{
            k:round(v,2) if isinstance(v,float) else v
            for k,v in row.items()
        }
        for sku,row in out.items()
    }
