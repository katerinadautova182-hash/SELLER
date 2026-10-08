"""Compare Ozon analytics quantities with September Seller Economy reference SKUs."""
from ozon_export.client import OzonClient
from ozon_agent.quantity_analytics import fetch_units_by_sku
from ozon_agent.live_snapshot import build_live_snapshot

TARGETS={"2020C-B","MS101","PB74","MS8828 BLACK","MS9903R+ms101","MS9500"}

def main():
    client=OzonClient()
    qty=fetch_units_by_sku(client,"2026-09-01","2026-09-30")
    snap=build_live_snapshot(client)
    sku_to_offer={}
    for item in snap.get("items",[]):
        offer=str(item.get("offer_id") or "")
        if offer in TARGETS:
            for sku in (item.get("all_skus") or item.get("customer_price_skus") or []):
                sku_to_offer[str(sku)]=offer
    for sku,row in qty.items():
        offer=sku_to_offer.get(str(sku))
        if offer:
            print("QTYCHECK",offer,sku,row)

if __name__=="__main__":
    main()
