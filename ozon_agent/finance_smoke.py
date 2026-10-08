"""One-off smoke test for current Ozon finance accrual endpoint."""
from datetime import datetime, timedelta, timezone
from ozon_export.client import OzonClient
from ozon_agent.finance_accruals import fetch_accruals_for_day

def main():
    day=(datetime.now(timezone.utc)-timedelta(days=1)).date().isoformat()
    client=OzonClient()
    rows=fetch_accruals_for_day(client, day, max_pages=2)
    print(f"Finance accrual API OK for {day}. Accrual rows: {len(rows)}")
    if rows:
        print("Finance sample keys:", sorted(rows[0].keys()))
        for acc in rows:
            products=((acc.get("posting") or {}).get("products") or [])
            if products:
                print("Finance product sample keys:", sorted(products[0].keys()))
                print("Finance product quantity sample:", products[0].get("quantity"))
                break

if __name__=="__main__":
    main()
