"""Inspect Ozon realization fields for MS9500."""
from ozon_export.client import OzonClient

def main():
    client=OzonClient()

    monthly=client.post("/v2/finance/realization",{"month":9,"year":2026})
    rows=((monthly.get("result") or {}).get("rows") or [])
    print("Monthly realization rows:",len(rows))
    for row in rows:
        item=row.get("item") or {}
        if str(item.get("offer_id") or "").strip().upper()=="MS9500":
            print("MS9500 monthly:",row)

    posting=client.post("/v1/finance/realization/posting",{"month":9,"year":2026})
    rows2=posting.get("rows") or []
    print("Posting realization rows:",len(rows2))
    for row in rows2:
        item=row.get("item") or {}
        if str(item.get("offer_id") or "").strip().upper()=="MS9500":
            print("MS9500 posting:",row)

if __name__=="__main__":
    main()
