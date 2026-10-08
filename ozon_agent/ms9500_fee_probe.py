"""Probe September accrual fee types for MS9500 / SKU 496958132."""
from collections import defaultdict
from datetime import date,timedelta
from ozon_export.client import OzonClient
from ozon_agent.finance_accruals import fetch_accruals_for_day, fetch_accrual_types, money

TARGET="496958132"

def main():
    client=OzonClient()
    types=fetch_accrual_types(client)
    sums=defaultdict(float)
    d=date(2026,9,1)
    end=date(2026,9,30)
    while d<=end:
        rows=fetch_accruals_for_day(client,d.isoformat())
        for acc in rows:
            for grp in ((acc.get("item_fees") or {}).get("fees") or []):
                if str(grp.get("sku") or "") != TARGET:
                    continue
                for fee in grp.get("fees") or []:
                    try:
                        tid=int(fee.get("type_id"))
                    except Exception:
                        tid=-1
                    sums[tid]+=money(fee.get("accrued"))
        d+=timedelta(days=1)

    for tid,amount in sorted(sums.items(), key=lambda x: -abs(x[1])):
        meta=types.get(tid,{})
        print(f"MS9500 fee type={tid} name={meta.get('name','')} amount={amount:.2f}")

if __name__=="__main__":
    main()
