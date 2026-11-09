import os,json,time,pathlib,requests,csv,collections,sys
out=pathlib.Path("artifacts/october_2026_reconcile");out.mkdir(parents=True,exist_ok=True)
s=requests.Session()
def request(url,headers,body):
 for i in range(4):
  r=s.post(url,headers=headers,json=body,timeout=100)
  if r.status_code in (429,500,502,503,504):time.sleep(2**i*3);continue
  if r.status_code>=400:raise RuntimeError(str(r.status_code)+": "+r.text[:800])
  return r.json()
 raise RuntimeError("retries exceeded")
status={}
try:
 key=os.environ["YANDEX_MARKET_API_KEY"];headers={"Api-Key":key,"Content-Type":"application/json"}
 campid=71762450; orders=[]
 for a,b in [("2026-10-01","2026-10-16"),("2026-10-17","2026-10-31")]:
  token=None
  for page in range(100):
   params="?limit=200"+("&pageToken="+requests.utils.quote(token,safe="") if token else "")
   obj=request(f"https://api.partner.market.yandex.ru/v2/campaigns/{campid}/stats/orders"+params,headers,{"dateFrom":a,"dateTo":b})
   if page==0:(out/f"yandex_stats_sample_{a}.json").write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")
   chunk=obj.get("result",{}).get("orders",[]) if isinstance(obj.get("result"),dict) else obj.get("orders",[])
   orders+=chunk
   token=(obj.get("result",{}).get("paging",{}) if isinstance(obj.get("result"),dict) else obj.get("paging",{})).get("nextPageToken")
   if not token:break
 (out/"yandex_stats_orders.json").write_text(json.dumps(orders,ensure_ascii=False),encoding="utf-8")
 status["Yandex"]={"ok":True,"orders":len(orders),"sample_fields":list(orders[0].keys()) if orders else []}
except Exception as e:status["Yandex"]={"ok":False,"error":str(e)}
try:
 h={"Client-Id":os.environ["OZON_CLIENT_ID"],"Api-Key":os.environ["OZON_API_KEY"],"Content-Type":"application/json"}
 allitems=[];last=""
 for i in range(200):
  body={"filter":{"visibility":"ALL"},"last_id":last,"limit":1000}
  obj=request("https://api-seller.ozon.ru/v3/product/list",h,body)
  result=obj.get("result",{}) or {}
  items=result.get("items",[])
  allitems.extend(items)
  nxt=result.get("last_id","")
  if not nxt or nxt==last or len(items)<1000:break
  last=nxt
 (out/"ozon_products.json").write_text(json.dumps(allitems,ensure_ascii=False),encoding="utf-8")
 status["Ozon"]={"ok":True,"product_rows":len(allitems),"sample_fields":list(allitems[0].keys()) if allitems else []}
except Exception as e:status["Ozon"]={"ok":False,"error":str(e)}
(out/"status.json").write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(status,ensure_ascii=False))
if not all(x["ok"] for x in status.values()):sys.exit(1)
