"""Batch inspect FBO/FBS delivered postings for control SKUs."""
from ozon_export.client import OzonClient

TARGETS={"2020C-B","MS101","PB74","MS8828 BLACK","MS9903R+ms101","MS9500"}
SINCE="2026-08-01T00:00:00Z"
TO="2026-10-01T00:00:00Z"

def fetch_pages(c,endpoint):
    out=[];offset=0
    while True:
        data=c.post(endpoint,{
            "dir":"ASC",
            "filter":{"since":SINCE,"to":TO,"status":"delivered"},
            "limit":1000,"offset":offset,
            "with":{"analytics_data":True,"financial_data":False}
        })
        rows=data.get("result") or []
        out.extend(rows)
        if len(rows)<1000: break
        offset+=1000
    return out

def main():
    c=OzonClient()
    for endpoint,scheme in [("/v2/posting/fbo/list","FBO"),("/v3/posting/fbs/list","FBS")]:
        rows=fetch_pages(c,endpoint)
        print("BATCH",scheme,"rows",len(rows))
        for r in rows:
            products=r.get("products") or []
            matches=[p for p in products if str(p.get("offer_id") or "") in TARGETS]
            if not matches: continue
            for p in matches:
                print(
                    "POSTROW",scheme,p.get("offer_id"),r.get("posting_number"),
                    "qty",p.get("quantity"),
                    "created",r.get("created_at"),
                    "fact",r.get("fact_delivery_date"),
                    "shipment",r.get("shipment_date"),
                    "status",r.get("status")
                )

if __name__=="__main__":
    main()
