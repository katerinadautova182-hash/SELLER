"""Probe Ozon placement-by-products report for September 2026."""
import csv,io,time,requests
from ozon_export.client import OzonClient

def main():
    client=OzonClient()
    created=client.post("/v1/report/placement/by-products/create",{
        "date_from":"2026-09-01",
        "date_to":"2026-09-30"
    })
    code=created.get("code") or (created.get("result") or {}).get("code")
    print("PLACEMENT create:",created)
    if not code:
        raise SystemExit("No report code")
    for _ in range(30):
        info=client.post("/v1/report/info",{"code":code})
        result=info.get("result") or {}
        status=str(result.get("status") or "")
        print("PLACEMENT status:",status)
        file_url=result.get("file")
        if file_url:
            r=requests.get(file_url,timeout=60)
            r.raise_for_status()
            raw=r.content.decode("utf-8-sig",errors="replace")
            print("PLACEMENT lines:",len(raw.splitlines()))
            print("\n".join(raw.splitlines()[:12]))
            return
        time.sleep(2)
    raise SystemExit("Placement report timeout")

if __name__=="__main__":
    main()
