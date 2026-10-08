"""Probe all accrual fee trees for MS9500-related posting numbers."""
from datetime import date,timedelta
from ozon_export.client import OzonClient
from ozon_agent.finance_accruals import fetch_accruals_for_day, fetch_accrual_types, money

UNITS={"31596582-0419-1","0221557140-0150-1","16847541-0208-1","05779053-0993-1"}
def walk(node,path=""):
    if isinstance(node,dict):
        if "type_id" in node and isinstance(node.get("accrued"),dict):
            yield path,int(node.get("type_id") or -1),money(node.get("accrued"))
        for k,v in node.items():
            yield from walk(v,f"{path}.{k}" if path else k)
    elif isinstance(node,list):
        for i,v in enumerate(node):
            yield from walk(v,f"{path}[{i}]")

def main():
    client=OzonClient(); types=fetch_accrual_types(client)
    d=date(2026,9,1); end=date(2026,9,30)
    while d<=end:
        for acc in fetch_accruals_for_day(client,d.isoformat()):
            unit=str(acc.get("unit_number") or "")
            if unit not in UNITS:
                continue
            print("UNIT",unit,"date",d.isoformat(),"cat",acc.get("accrued_category"),"total",money(acc.get("total_amount")))
            for path,tid,amount in walk(acc):
                print(" ANYFEE",tid,types.get(tid,{}).get("name",""),amount,path)
        d+=timedelta(days=1)

if __name__=="__main__":
    main()
