"""Smoke test Ozon analytics delivered/returns metrics."""
from ozon_export.client import OzonClient
from ozon_agent.quantity_analytics import fetch_units_by_sku

def main():
    client=OzonClient()
    rows=fetch_units_by_sku(client,"2026-09-01","2026-09-30")
    total_delivered=sum(x["delivered_units"] for x in rows.values())
    total_returns=sum(x["returned_units"] for x in rows.values())
    total_net=sum(x["net_units"] for x in rows.values())
    print(f"Quantity analytics OK. SKU={len(rows)} delivered={total_delivered} returns={total_returns} net={total_net}")
    for sku,row in list(rows.items())[:5]:
        print("Quantity sample:",sku,row)

if __name__=="__main__":
    main()
