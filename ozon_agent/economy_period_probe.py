"""Test Ozon Economy period semantics across FBO/FBS control SKUs."""
from datetime import datetime,timezone
from ozon_export.client import OzonClient

TARGETS={"2020C-B","MS101","PB74","MS8828 BLACK","MS9903R+ms101","MS9500"}

def in_sep(ts):
    if not ts: return False
    s=str(ts).replace("Z","+00:00")
    try: d=datetime.fromisoformat(s)
    except Exception: return False
    return d.year==2026 and d.month==9

def scheme_info(c,p):
    try:
        d=c.post("/v2/posting/fbo/get",{"posting_number":p,"with":{"analytics_data":True,"financial_data":False,"legal_info":False}})
        r=d.get("result") or {}
        return "FBO",r.get("fact_delivery_date"),r.get("created_at")
    except Exception:
        pass
    try:
        d=c.post("/v3/posting/fbs/get",{"posting_number":p,"with":{"analytics_data":True,"financial_data":False,"legal_info":False}})
        r=d.get("result") or {}
        return "FBS",r.get("fact_delivery_date"),r.get("shipment_date")
    except Exception:
        return "UNKNOWN",None,None

def main():
    c=OzonClient()
    data=c.post("/v1/finance/realization/posting",{"month":9,"year":2026})
    rows=data.get("rows") or []
    agg={o:{"qty":0.0,"buyer":0.0,"parts":[]} for o in TARGETS}
    for row in rows:
        item=row.get("item") or {}
        offer=str(item.get("offer_id") or "")
        if offer not in TARGETS: continue
        dc=row.get("delivery_commission") or {}
        if not dc: continue
        p=str((row.get("order") or {}).get("posting_number") or "")
        created=str((row.get("order") or {}).get("created_date") or "")
        scheme,fact,other=scheme_info(c,p)
        include=(scheme=="FBO" and in_sep(fact)) or (scheme=="FBS" and created.startswith("2026-9"))
        qty=float(dc.get("quantity") or 0); buyer=float(dc.get("amount") or 0)
        agg[offer]["parts"].append((p,scheme,created,fact,qty,buyer,include))
        if include:
            agg[offer]["qty"]+=qty;agg[offer]["buyer"]+=buyer
    for offer,v in agg.items():
        print("SEMANTICS",offer,f"qty={v['qty']}",f"buyer={v['buyer']:.2f}")
        for part in v["parts"]:
            print(" PART",offer,part)

if __name__=="__main__":
    main()
