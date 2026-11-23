"""Read-only Ozon Seller API diagnostic. Sends no commercial data to logs.
The output is encrypted to an ephemeral public key before upload.
"""
import base64
import datetime as dt
import json
import os
import pathlib
import secrets
import sys
import time
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAsnNWdxPkm7hUuSEiFPgi
mTgJxC1z1NAN/lLVnUssY/cyp5TmJhushZCbPz4i6M7KNoKiQx2kVd+qolPlehGx
c5uoKHU6rSm912yrqX/Jvi1aII9ytZJ/yV5+8mELfRAXWK2082Ny0tqBPfuBGPUe
twNdnKmqW5kkjhCKp4UkS8BqSsACZtHbj9Y/e8RG5kvJ/JSEOTMLIbuaOBlJI2bk
YN0OIzDX4sFAalU8PLVREwwFwBHqI3GsBzoZz763cZgW3calt2wJPY1JU+plcsDA
Tzy5p0L8eP3FN6HzlHojkoxmgnkvrxJDXZf5cXKsZedWJacg38ktOiAE5QtqoN4V
eQIDAQAB
-----END PUBLIC KEY-----"""

session = requests.Session()
headers = {"Client-Id": os.environ["OZON_CLIENT_ID"], "Api-Key": os.environ["OZON_API_KEY"], "Content-Type": "application/json"}

def api(body):
    for attempt in range(6):
        try:
            r = session.post("https://api-seller.ozon.ru/v1/analytics/data",
                headers=headers, json=body, timeout=90)
        except requests.RequestException:
            if attempt == 5:
                raise
            time.sleep(2**attempt)
            continue
        if r.status_code in (429,500,502,503,504) and attempt < 5:
            time.sleep(min(40,2**attempt))
            continue
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}", "details": r.text[:500]}
        return r.json()
    raise RuntimeError("Ozon analytics API retry exhausted")

def query(from_, to_, dimensions, metrics):
    out = []
    offset = 0
    while True:
        payload = {"date_from":from_, "date_to":to_, "metrics":metrics,
            "dimension": dimensions, "filters":[], "sort":[{"key":"ordered_units","order":"DESC"}],
            "limit": 1000, "offset":offset}
        r = api(payload)
        if "error" in r:
            return {"error":r["error"],"details":r.get("details","")}
        part = (r.get("result") or {}).get("data") or []
        out.extend(part)
        if len(part)<1000:
            return {"rows":out}
        offset += 1000
        if offset > 50000:
            return {"rows":out, "truncated":True}

today = dt.date.today()
last = today - dt.timedelta(days=1)
start = last - dt.timedelta(days=13)
prior_end = start - dt.timedelta(days=1)
prior_start = prior_end - dt.timedelta(days=13)
older_end = prior_start - dt.timedelta(days=1)
older_start = older_end - dt.timedelta(days=13)
oct_start = dt.date(2026,10,1)
oct_equal_end = oct_start + dt.timedelta(days=last.day-1)
nov_start = dt.date(2026,11,1)
periods = {
  "recent_14d": [start.isoformat(),last.isoformat()],
  "previous_14d": [prior_start.isoformat(),prior_end.isoformat()],
  "older_14d": [older_start.isoformat(),older_end.isoformat()],
  "november_mtd": [nov_start.isoformat(),last.isoformat()],
  "october_equal_days": [oct_start.isoformat(),oct_equal_end.isoformat()],
  "october_full": ["2026-10-01","2026-10-31"]
}
result = {"generated_at_utc":dt.datetime.now(dt.timezone.utc).isoformat(),
 "metric_definition":"ordered_units=placed orders, not redeemed sales; revenue=ordered revenue",
 "periods":{}, "daily":{}, "seller_api_reachable":True,
 "performance_credentials_present":bool(os.getenv("OZON_PERFORMANCE_CLIENT_ID") and os.getenv("OZON_PERFORMANCE_CLIENT_SECRET"))}
for name,(a,b) in periods.items():
    result["periods"][name] = {"from":a,"to":b,"data":query(a,b,["sku"],["ordered_units","revenue"])}
result["daily"] = query(older_start.isoformat(),last.isoformat(),["day"],["ordered_units","revenue"])
# Additional funnel metrics if permitted: failed attempts are included only as metadata.
result["funnel_recent"] = query(start.isoformat(),last.isoformat(),["sku"],["ordered_units","revenue","hits_view","hits_tocart"])
result["funnel_previous"] = query(prior_start.isoformat(),prior_end.isoformat(),["sku"],["ordered_units","revenue","hits_view","hits_tocart"])


# Compare the funnel on exactly matched calendar days of October and November.
result["funnel_october_equal_days"] = query(oct_start.isoformat(),oct_equal_end.isoformat(),["sku"],["ordered_units","revenue","hits_view","hits_tocart"])
result["funnel_november_mtd"] = query(nov_start.isoformat(),last.isoformat(),["sku"],["ordered_units","revenue","hits_view","hits_tocart"])
# SKU-by-day drilldown: distinguish temporary spikes from sustained demand.
result["sku_day_october_equal_days"] = query(oct_start.isoformat(),oct_equal_end.isoformat(),["sku","day"],["ordered_units","revenue"])
result["sku_day_november_mtd"] = query(nov_start.isoformat(),last.isoformat(),["sku","day"],["ordered_units","revenue"])

plaintext=json.dumps(result,ensure_ascii=False,default=str,separators=(",",":")).encode()
aes_key=secrets.token_bytes(32)
nonce=secrets.token_bytes(12)
cipher=AESGCM(aes_key).encrypt(nonce,plaintext,b"ozon-sales-diagnostic-v1")
rsa_public=serialization.load_pem_public_key(PUBLIC_KEY.encode())
key_cipher=rsa_public.encrypt(aes_key,padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),algorithm=hashes.SHA256(),label=None))
dest=pathlib.Path("artifacts/ozon_secure");dest.mkdir(parents=True,exist_ok=True)
(dest/"diagnostic.aesgcm").write_bytes(nonce+cipher)
(dest/"aes_key.rsa").write_bytes(key_cipher)
(dest/"format.txt").write_text("v1: AESGCM nonce=first 12 bytes, AAD=ozon-sales-diagnostic-v1, key RSA-OAEP-SHA256",encoding="utf-8")
print("Encrypted read-only Ozon diagnostic created; commercial metrics withheld from logs.")
