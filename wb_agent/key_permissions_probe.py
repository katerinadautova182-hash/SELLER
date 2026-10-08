"""Read-only WB API permissions probe; never log credentials or customer data."""
import json,os,urllib.request,urllib.error
key=os.environ["WB_API_KEY"]
checks=[
 ("prices","GET","https://discounts-prices-api.wildberries.ru/api/v2/list/goods/filter?limit=1&offset=0",None),
 ("statistics","GET","https://statistics-api.wildberries.ru/api/v1/supplier/orders?dateFrom=2026-10-07&flag=1",None),
 ("marketplace","GET","https://marketplace-api.wildberries.ru/api/v3/warehouses",None),
 ("content","POST","https://content-api.wildberries.ru/content/v2/get/cards/list",{"settings":{"cursor":{"limit":1},"filter":{"withPhoto":-1}}}),
]
for name,method,url,body in checks:
 try:
  req=urllib.request.Request(url,method=method,data=json.dumps(body).encode() if body else None,headers={"Authorization":key,"Content-Type":"application/json","Accept":"application/json"})
  with urllib.request.urlopen(req,timeout=25) as r:
   payload=json.load(r)
   shape=type(payload).__name__
   count=len(payload) if isinstance(payload,list) else len(payload.get("cards",[])) if isinstance(payload,dict) and isinstance(payload.get("cards"),list) else "n/a"
   print(json.dumps({"category":name,"status":r.status,"shape":shape,"count":count}))
 except urllib.error.HTTPError as exc:
  print(json.dumps({"category":name,"status":exc.code,"result":"denied_or_error"}))
 except Exception as exc:
  print(json.dumps({"category":name,"result":type(exc).__name__}))
