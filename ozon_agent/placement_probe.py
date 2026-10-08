"""Probe Ozon placement-by-products report headers for September."""
import time, requests
from ozon_export.client import OzonClient

def main():
    client=OzonClient()
    created=client.post("/v1/report/placement/by-products/create",{"date_from":"2026-09-01","date_to":"2026-09-30"})
    code=created.get("code") or (created.get("result") or {}).get("code")
    print("PLACEMENT code",code)
    if not code:
        print("PLACEMENT create response",created); return
    for _ in range(20):
        info=client.post("/v1/report/info",{"code":code})
        result=info.get("result") or {}
        status=str(result.get("status") or "")
        print("PLACEMENT status",status)
        if status.lower() in {"success","succeeded"} and result.get("file"):
            url=result["file"]
            r=requests.get(url,timeout=60)
            r.raise_for_status()
            text=r.content.decode("utf-8-sig",errors="replace")
            print("PLACEMENT HEAD")
            print("\n".join(text.splitlines()[:8]))
            return
        time.sleep(3)
    print("PLACEMENT timeout")

if __name__=="__main__":
    main()
