"""Probe daily realization API against Seller Economy reference SKUs."""
from datetime import date,timedelta
from collections import defaultdict
from ozon_export.client import OzonClient

TARGETS={"2020C-B","MS101","PB74","MS8828 BLACK","MS9903R+ms101","MS9500"}

def main():
    c=OzonClient()
    agg=defaultdict(lambda:{"delivered":0.0,"returned":0.0,"buyer":0.0,"points":0.0,"partners":0.0,"base":0.0,"fee":0.0})
    d=date(2026,9,7)
    while d<=date(2026,9,30):
        data=c.post("/v1/finance/realization/by-day",{"day":d.day,"month":d.month,"year":d.year})
        for row in data.get("rows") or []:
            item=row.get("item") or {}
            offer=str(item.get("offer_id") or "")
            if offer not in TARGETS: continue
            dc=row.get("delivery_commission") or {}
            rc=row.get("return_commission") or {}
            delivered=float(dc.get("quantity") or 0)
            returned=float(rc.get("quantity") or 0)
            a=agg[offer]
            a["delivered"]+=delivered
            a["returned"]+=returned
            a["buyer"]+=float(dc.get("amount") or 0)-float(rc.get("amount") or 0)
            a["points"]+=float(dc.get("bonus") or 0)-float(rc.get("bonus") or 0)
            a["partners"]+=(
                float(dc.get("bank_coinvestment") or 0)+float(dc.get("stars") or 0)+float(dc.get("pick_up_point_coinvestment") or 0)+float(dc.get("compensation") or 0)
                -float(rc.get("bank_coinvestment") or 0)-float(rc.get("stars") or 0)-float(rc.get("pick_up_point_coinvestment") or 0)-float(rc.get("compensation") or 0)
            )
            seller=float(row.get("seller_price_per_instance") or 0)
            a["base"]+=seller*(delivered-returned)
            a["fee"]+=float(dc.get("standard_fee") or 0)-float(rc.get("standard_fee") or 0)
        d+=timedelta(days=1)
    for offer in sorted(TARGETS):
        a=agg[offer]
        print("BYDAY",offer,
              f"delivered={a['delivered']:.0f}",
              f"returned={a['returned']:.0f}",
              f"net={a['delivered']-a['returned']:.0f}",
              f"buyer={a['buyer']:.2f}",
              f"points={a['points']:.2f}",
              f"partners={a['partners']:.2f}",
              f"base={a['base']:.2f}",
              f"fee={a['fee']:.2f}")
if __name__=="__main__":
    main()
