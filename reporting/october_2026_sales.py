"""October 2026 marketplace order quantities. Read-only APIs; raw evidence retained."""
import os, csv, json, pathlib, collections, datetime, sys, time
import requests

OUT=pathlib.Path("artifacts/october_2026"); OUT.mkdir(parents=True,exist_ok=True)
SESSION=requests.Session()
START="2026-10-01"; END="2026-10-31"
status={}
rows=[]
def save(name,obj):
    (OUT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
def call(method,url,**kwargs):
    for i in range(5):
        r=SESSION.request(method,url,timeout=90,**kwargs)
        if r.status_code in (429,500,502,503,504):
            time.sleep(min(30,2**i*2));continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("API retried five times: "+url)
def append(platform,sku,title,count,kind,order_id=""):
    if sku and count:
        rows.append(dict(platform=platform,sku=str(sku),title=str(title or ""),quantity=int(count),kind=kind,order_id=str(order_id)))
def ozon():
    cid=os.environ["OZON_CLIENT_ID"]; key=os.environ["OZON_API_KEY"]
    headers={"Client-Id":cid,"Api-Key":key,"Content-Type":"application/json"}
    offset=0; allrows=[]
    while True:
        payload={"date_from":START,"date_to":END,"metrics":["ordered_units"],"dimension":["sku"],"filters":[],"sort":[{"key":"ordered_units","order":"DESC"}],"limit":1000,"offset":offset}
        obj=call("POST","https://api-seller.ozon.ru/v1/analytics/data",headers=headers,json=payload)
        chunk=obj.get("result",{}).get("data",[])
        allrows.extend(chunk)
        if len(chunk)<1000:break
        offset+=1000
    save("ozon_analytics_raw.json",allrows)
    for x in allrows:
        dim=(x.get("dimensions") or [{}])[0]; metrics=x.get("metrics") or [0]
        append("Ozon",dim.get("id"),dim.get("name"),round(float(metrics[0] or 0)),"ordered_units_NOT_net")
    status["Ozon"]={"ok":True,"rows":len(allrows),"metric":"ordered_units: orders, NOT completed sales"}
def wildberries():
    token=os.environ.get("WB_STATISTICS_API_KEY") or os.environ["WB_API_KEY"]
    headers={"Authorization":token}
    # Query October from the WB statistics sales feed. sales may include later-return updates;
    # use event lastChangeDate and retain all raw rows for audit.
    data=call("GET","https://statistics-api.wildberries.ru/api/v1/supplier/sales",headers=headers,params={"dateFrom":"2026-10-01T00:00:00","flag":0})
    data=[x for x in data if START <= str(x.get("date",""))[:10] <= END]
    save("wb_sales_raw.json",data)
    for x in data:
        article=x.get("supplierArticle") or x.get("nmId")
        # WB statistics sale records represent pieces, and returns have isStorno or negative saleID prefix R
        is_return=bool(x.get("isStorno")) or str(x.get("saleID","")).startswith("R")
        append("Wildberries",article,x.get("subject"),-1 if is_return else 1,"return_or_storno" if is_return else "sale",x.get("saleID",""))
    status["Wildberries"]={"ok":True,"rows":len(data),"metric":"dated sales events, returns signed negative; audit against finance report"}
def yandex():
    key=os.environ["YANDEX_MARKET_API_KEY"]
    headers={"Api-Key":key,"Content-Type":"application/json"}
    camps=call("GET","https://api.partner.market.yandex.ru/v2/campaigns",headers=headers)
    save("yandex_campaigns_response.json",camps)
    ids=set()
    for c in camps.get("campaigns",[]):
        b=c.get("business",{}) or {}
        if b.get("id"):ids.add(str(b["id"]))
        if c.get("businessId"):ids.add(str(c["businessId"]))
    if not ids:
        raise RuntimeError("Yandex businessId not in campaigns response; unable to safely collect orders")
    allorders={}
    for bid in ids:
        for first,last in [(START,"2026-10-30"),("2026-10-31",END)]:
            page=None
            while True:
                params={"limit":50}
                if page:params["pageToken"]=page
                body={"dates":{"creationDateFrom":first,"creationDateTo":last}}
                result=call("POST",f"https://api.partner.market.yandex.ru/v1/businesses/{bid}/orders",headers=headers,params=params,json=body)
                for order in result.get("orders",[]):
                    allorders[str(order.get("id"))]=order
                page=(result.get("paging") or {}).get("nextPageToken")
                if not page:break
    save("yandex_orders_raw.json",list(allorders.values()))
    for order in allorders.values():
        state=order.get("status","UNKNOWN")
        if state in ("CANCELLED","UNPAID","PLACING"):continue
        for item in order.get("items",[]):
            append("Yandex Market",item.get("offerId"),item.get("offerName"),item.get("count",0),"order_status_"+state,order.get("id"))
    status["Yandex Market"]={"ok":True,"orders":len(allorders),"metric":"non-cancelled orders; delivered sales need delivery-status reconciliation"}
for name,fn in [("Ozon",ozon),("Wildberries",wildberries),("Yandex Market",yandex)]:
    try: fn()
    except Exception as exc:
        status[name]={"ok":False,"error":str(exc)}
        print(f"FAILED {name}: {type(exc).__name__}: {exc}",file=sys.stderr)
save("status.json",status)
with (OUT/"events.csv").open("w",encoding="utf-8-sig",newline="") as f:
    writer=csv.DictWriter(f,fieldnames=["platform","sku","title","quantity","kind","order_id"],delimiter=";")
    writer.writeheader();writer.writerows(rows)
totals=collections.defaultdict(int)
for x in rows: totals[(x["platform"],x["sku"],x["title"])]+=x["quantity"]
with (OUT/"sku_by_marketplace.csv").open("w",encoding="utf-8-sig",newline="") as f:
    writer=csv.writer(f,delimiter=";");writer.writerow(["Площадка","Артикул МП","Название","Количество, шт","Метрика"])
    for (platform,sku,title),qty in sorted(totals.items()):
        writer.writerow([platform,sku,title,qty,status.get(platform,{}).get("metric","")])
print(json.dumps(status,ensure_ascii=False,indent=2))
if not all(x.get("ok") for x in status.values()):sys.exit(1)
