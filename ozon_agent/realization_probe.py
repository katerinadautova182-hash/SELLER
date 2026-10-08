"""Inspect Ozon monthly realization fields for one offer."""
from ozon_export.client import OzonClient

def main():
    client=OzonClient()
    data=client.post("/v2/finance/realization",{"month":9,"year":2026})
    result=data.get("result") or {}
    rows=result.get("rows") or []
    print("Realization rows:",len(rows))
    for row in rows:
        item=row.get("item") or {}
        if str(item.get("offer_id") or "").strip().upper()=="MS9500":
            print("MS9500 realization row:",row)

if __name__=="__main__":
    main()
