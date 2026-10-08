"""Probe MS9500 accruals by posting number."""
from datetime import date,timedelta
from ozon_export.client import OzonClient
from ozon_agent.finance_accruals import fetch_accruals_for_day, fetch_accrual_types, money

TARGET="496958132"

def main():
    client=OzonClient()
    types=fetch_accrual_types(client)
    d=date(2026,9,1); end=date(2026,9,30)
    while d<=end:
        for acc in fetch_accruals_for_day(client,d.isoformat()):
            posting=acc.get("posting") or {}
            products=posting.get("products") or []
            product_match=any(str(p.get("sku") or "")==TARGET for p in products)
            fee_match=any(str(g.get("sku") or "")==TARGET for g in ((acc.get("item_fees") or {}).get("fees") or []))
            if not (product_match or fee_match):
                continue
            unit=str(acc.get("unit_number") or "")
            print("ACC",d.isoformat(),"unit",unit,"cat",acc.get("accrued_category"),"type",acc.get("type_id"),"total",money(acc.get("total_amount")))
            for p in products:
                if str(p.get("sku") or "")!=TARGET:
                    continue
                comm=p.get("commission") or {}
                delivery=p.get("delivery") or {}
                print(" PRODUCT seller",money(comm.get("seller_price")),"sale_comm",money(comm.get("sale_commission")),"delivery",money(delivery.get("total_accrued")),"services",delivery.get("services"))
            for g in ((acc.get("item_fees") or {}).get("fees") or []):
                if str(g.get("sku") or "")!=TARGET:
                    continue
                for fee in g.get("fees") or []:
                    tid=int(fee.get("type_id") or -1)
                    print(" FEE",tid,types.get(tid,{}).get("name",""),money(fee.get("accrued")))
        d+=timedelta(days=1)

if __name__=="__main__":
    main()
