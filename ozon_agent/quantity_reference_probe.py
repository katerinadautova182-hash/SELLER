"""Compare Ozon analytics quantities with September Seller Economy reference SKUs."""
from ozon_export.client import OzonClient
from ozon_agent.quantity_analytics import fetch_units_by_sku
from ozon_agent.realization_period import aggregate_realization_period

TARGETS={"2020C-B","MS101","PB74","MS8828 BLACK","MS9903R+ms101","MS9500"}

def main():
    client=OzonClient()
    qty=fetch_units_by_sku(client,"2026-09-01","2026-09-30")
    realization=aggregate_realization_period(client,"2026-09-01","2026-09-30")
    for sku,sale in realization.items():
        offer=str(sale.get("offer_id") or "")
        if offer in TARGETS:
            print("QTYCHECK",offer,sku,qty.get(str(sku)))

if __name__=="__main__":
    main()
