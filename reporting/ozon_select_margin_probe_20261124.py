"""Read-only Ozon realization and accrual diagnostics, encrypted output only."""
import datetime as dt
import json
import os
import pathlib
import secrets
from collections import defaultdict
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from ozon_export.client import OzonClient
from ozon_agent.finance_accruals import fetch_accruals_for_day,aggregate_sku_finance,aggregate_non_item_finance,fetch_accrual_types
from ozon_agent.unit_economics_live import load_cost_map,resolve_purchase_cost

PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAtkHL9vAKtMUAOhjp5ASZ
xCu4ZCF2ObqSUo1SR3ou8acy9rmJGKASOkltyG5bSJ0xy1nGX/NxSp2PWaOSj7nB
VvnPo/4WWzn2HN4hgFtqcqXL4DJXcLLV5whbT7TegC448Q6lq6nTb/xgW2Yj8Ci9
GNDhhQtWMLYYtoOvs4F+R/n1PR+z8oQx6c4e9SnYq0z5/frF3xAdp4THuUq6pDg2
bG6ATynObyspYnMD/wWuf20LmZ0P01WaMJ4tyzjZH5AzooYvLWoBNq3dOYe04opb
mbVS8pEc0xqnRDibiNnmbQcIkMMGECEXx8xP2ORz6m/WRgolRkSpljTujipAMdqx
nwIDAQAB
-----END PUBLIC KEY-----"""
c=OzonClient()
out={"generated_at_utc":dt.datetime.now(dt.timezone.utc).isoformat(),
    "cost_factor":1.35,"cost_map_present":False,"realization":{},"accrual_sku":{},
    "accrual_non_item":{},"errors":[]}
def num(x):
    try:return float(x or 0)
    except (TypeError,ValueError):return 0.0
cost_map=load_cost_map()
out["cost_map_present"]=bool(cost_map)
for year,month in [(2026,10),(2026,11)]:
    label=f"{year}-{month:02d}"
    try:
        x=c.post("/v1/finance/realization/posting",{"year":year,"month":month})
        raw=x.get("rows") or []
        records=[]
        for row in raw:
            order=row.get("order") or {}
            item=row.get("item") or {}
            delivery=row.get("delivery_commission") or {}
            ret=row.get("return_commission") or {}
            offer=str(item.get("offer_id") or "")
            sku=str(item.get("sku") or "")
            qty=num(delivery.get("quantity"))
            if not sku and not offer:continue
            purchase,source,verified=resolve_purchase_cost(offer,cost_map)
            rec={
                "offer_id":offer,"sku":sku,"order_created_date":str(order.get("created_date") or ""),
                "quantity":qty,"returned_qty":num(ret.get("quantity")),
                "buyer_paid":num(delivery.get("amount")),"buyer_returned":num(ret.get("amount")),
                "ozon_bonus":num(delivery.get("bonus")),"returned_bonus":num(ret.get("bonus")),
                "bank_coinvestment":num(delivery.get("bank_coinvestment")),
                "returned_bank_coinvestment":num(ret.get("bank_coinvestment")),
                "stars":num(delivery.get("stars")),"returned_stars":num(ret.get("stars")),
                "pickup_coinvestment":num(delivery.get("pick_up_point_coinvestment")),
                "returned_pickup_coinvestment":num(ret.get("pick_up_point_coinvestment")),
                "compensation":num(delivery.get("compensation")),
                "returned_compensation":num(ret.get("compensation")),
                "seller_price_per_instance":num(row.get("seller_price_per_instance")),
                "fee":num(delivery.get("standard_fee")),"fee_return":num(ret.get("standard_fee")),
                "commission_ratio":num(row.get("commission_ratio")),
                "purchase_cost":purchase if verified else None,
                "cost_source":source,"cost_verified":bool(verified)
            }
            records.append(rec)
        out["realization"][label]={"raw_rows":len(raw),"records":records}
    except Exception as e:
        out["errors"].append({"op":"realization/posting","month":label,"error":str(e)[:220]})
try:
    type_map=fetch_accrual_types(c)
except Exception as e:
    out["errors"].append({"op":"accrual/types","error":str(e)[:220]})
    type_map={}
for month in [10,11]:
  for day in range(1,24):
    date=f"2026-{month:02d}-{day:02d}"
    try:
        raw=fetch_accruals_for_day(c,date)
        out["accrual_sku"][date]=aggregate_sku_finance(raw)
        # Financial components excluded by legacy aggregator: buyer sale, Ozon compensation and adjusted commission.
        extra={}
        for acc in raw:
            if str(acc.get("accrued_category") or "").upper()!="POSTING":
                continue
            for p in ((acc.get("posting") or {}).get("products") or []):
                sku=str(p.get("sku") or "")
                if not sku:continue
                comm=p.get("commission") or {}
                d=extra.setdefault(sku,{"sale_price_rub":0.0,"bonus_rub":0.0,
                    "coinvestment_rub":0.0,"adjusted_commission_rub":0.0,
                    "posting_net_rub":0.0,"posting_rows":0,
                    "commission_ratio_values":[]})
                d["posting_rows"]+=1
                def money(z):
                    if isinstance(z,dict):z=z.get("amount")
                    return num(z)
                sp=money(comm.get("sale_price"))
                cm=money(comm.get("sale_commission"))
                bonus=money(comm.get("bonus"))
                co=money(comm.get("coinvestment"))
                deliv=money((p.get("delivery") or {}).get("total_accrued"))
                d["sale_price_rub"]+=sp
                d["bonus_rub"]+=bonus
                d["coinvestment_rub"]+=co
                d["adjusted_commission_rub"]+=money(comm.get("commission"))
                d["posting_net_rub"]+=sp+cm+bonus+co+deliv
                ratio=comm.get("commission_ratio")
                if ratio not in (None,""):
                    d["commission_ratio_values"].append(str(ratio))
        out.setdefault("accrual_extra",{})[date]=extra
        out["accrual_non_item"][date]=aggregate_non_item_finance(raw,type_map)
    except Exception as e:
        out["errors"].append({"op":"accrual/by-day","day":date,"error":str(e)[:160]})

plaintext=json.dumps(out,ensure_ascii=False,separators=(",",":")).encode()
key=secrets.token_bytes(32)
nonce=secrets.token_bytes(12)
cipher=AESGCM(key).encrypt(nonce,plaintext,b"ozon-sales-diagnostic-v1")
rsa=serialization.load_pem_public_key(PUBLIC_KEY.encode())
wrapped=rsa.encrypt(key,padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),algorithm=hashes.SHA256(),label=None))
folder=pathlib.Path("artifacts/ozon_select_20261124_secure")
folder.mkdir(parents=True,exist_ok=True)
(folder/"diagnostic.aesgcm").write_bytes(nonce+cipher)
(folder/"aes_key.rsa").write_bytes(wrapped)
(folder/"format.txt").write_text("AESGCM AAD=ozon-sales-diagnostic-v1; RSA-OAEP SHA256",encoding="utf-8")
print("Read-only financial diagnosis encrypted; metrics and costs not disclosed in logs.")
