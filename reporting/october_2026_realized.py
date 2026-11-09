"""Accounting-basis October 2026: ONLY realization reports, never ordered units."""
import os,json,time,pathlib,requests,csv,zipfile,io,collections,sys
OUT=pathlib.Path("artifacts/october_2026_realized");OUT.mkdir(parents=True,exist_ok=True)
def save(name,obj):
 (OUT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")
def req(method,url,**kw):
 for attempt in range(5):
  r=requests.request(method,url,timeout=120,**kw)
  if r.status_code in (429,500,502,503,504):
   time.sleep(2**attempt*3);continue
  if r.status_code>=400:raise RuntimeError(f"HTTP {r.status_code}: {r.text[:400]}")
  return r
 raise RuntimeError("API retries exhausted")
status={}
def ozon():
 h={"Client-Id":os.environ["OZON_CLIENT_ID"],"Api-Key":os.environ["OZON_API_KEY"]}
 obj=req("POST","https://api-seller.ozon.ru/v2/finance/realization",headers=h,json={"month":10,"year":2026}).json()
 save("ozon_realization.json",obj)
 items=obj.get("result",{}).get("rows",[])
 grouped=collections.defaultdict(lambda:[0,0,"",""])
 for row in items:
  item=row.get("item") or {}
  sku=str(item.get("offer_id") or item.get("sku") or "")
  if not sku:continue
  a=grouped[sku]
  a[0]+=int((row.get("delivery_commission") or {}).get("quantity") or 0)
  a[1]+=int((row.get("return_commission") or {}).get("quantity") or 0)
  a[2]=item.get("name","")
  a[3]=str(item.get("sku",""))
 with (OUT/"ozon_realized_sku.csv").open("w",newline="",encoding="utf-8-sig") as f:
  w=csv.writer(f,delimiter=";");w.writerow(["offer_id","name","ozon_sku","realized","returned","net"])
  for sku,(sold,ret,title,osku) in sorted(grouped.items()):w.writerow([sku,title,osku,sold,ret,sold-ret])
 status["Ozon"]={"ok":True,"report_rows":len(items),"sku_count":len(grouped),"sold":sum(x[0] for x in grouped.values()),"returned":sum(x[1] for x in grouped.values())}
def wb():
 token=os.getenv("WB_FINANCE_API_KEY") or os.getenv("WB_API_KEY")
 if not token:raise RuntimeError("No WB finance token configured")
 # Financial report API replaced /api/v5/supplier/reportDetailByPeriod in July 2026.
 payload={"dateFrom":"2026-10-01","dateTo":"2026-10-31","period":"weekly","limit":100000,"rrdId":0}
 allrows=[]
 for page in range(12):
  response=req("POST","https://finance-api.wildberries.ru/api/finance/v1/sales-reports/detailed",headers={"Authorization":token},json=payload)
  if response.status_code==204:break
  chunk=response.json()
  if not isinstance(chunk,list):raise RuntimeError("unexpected finance report payload: "+str(chunk)[:350])
  if not chunk:break
  allrows.extend(chunk)
  if len(chunk)<100000:break
  payload["rrdId"]=int(chunk[-1]["rrdId"])
  time.sleep(62)
 save("wb_realization.json",allrows)
 grouped=collections.defaultdict(lambda:[0,0,0])
 kinds=collections.Counter()
 for x in allrows:
  op=x.get("supplierOperName","")
  kinds[op]+=1
  sku=str(x.get("vendorCode") or x.get("nmId") or "")
  if not sku:continue
  if op in ("Продажа","Возврат"):
   qty=int(x.get("quantity") or 1)
   grouped[sku][0 if op=="Продажа" else 1]+=qty
   grouped[sku][2]+=1
 with (OUT/"wb_realized_sku.csv").open("w",newline="",encoding="utf-8-sig") as f:
  w=csv.writer(f,delimiter=";");w.writerow(["seller_article","sales","returns","net","report_lines"])
  for sku,(s,r,n) in sorted(grouped.items()):w.writerow([sku,s,r,s-r,n])
 status["Wildberries"]={"ok":True,"report_rows":len(allrows),"sku_count":len(grouped),"sales":sum(v[0] for v in grouped.values()),"returns":sum(v[1] for v in grouped.values()),"operation_types":dict(kinds)}
def yandex():
 h={"Api-Key":os.environ["YANDEX_MARKET_API_KEY"]}
 obj=req("GET","https://api.partner.market.yandex.ru/v2/campaigns",headers=h).json()
 save("ym_campaigns.json",obj)
 campaigns=obj.get("campaigns") or obj.get("result",{}).get("campaigns") or []
 output=[]
 for campaign in campaigns:
  cid=campaign.get("id")
  if not cid:continue
  try:
   generated=req("POST","https://api.partner.market.yandex.ru/v2/reports/goods-realization/generate",headers=h,params={"format":"JSON"},json={"campaignId":cid,"year":2026,"month":10}).json()
   save(f"ym_generate_{cid}.json",generated)
   rid=generated.get("result",{}).get("reportId") or generated.get("reportId")
   if not rid:raise RuntimeError("no reportId "+str(generated)[:300])
   output.append({"campaign":cid,"report_id":rid})
  except Exception as e:output.append({"campaign":cid,"error":str(e)})
 save("ym_requests.json",output)
 for o in output:
  if not o.get("report_id"):continue
  rid=o["report_id"]; cid=o["campaign"]
  for attempt in range(12):
   info=req("GET",f"https://api.partner.market.yandex.ru/v2/reports/info/{rid}",headers=h).json()
   save(f"ym_info_{cid}.json",info)
   result=info.get("result") or {}
   if result.get("status")=="DONE":
    link=result.get("file") or result.get("url")
    if link:
     raw=req("GET",link).content
     (OUT/f"ym_realization_{cid}.zip").write_bytes(raw)
    break
   if result.get("status") in ("FAILED","CANCELLED"):break
   time.sleep(12)
 status["Yandex Market"]={"ok":True,"campaigns":output,"note":"Realization reports saved as ZIP; inspect delivered/returned sections before calculating net"}
for name,fn in [("Ozon",ozon),("Wildberries",wb),("Yandex Market",yandex)]:
 try:fn()
 except Exception as e:status[name]={"ok":False,"error":str(e)}
save("status.json",status)
print(json.dumps(status,ensure_ascii=False))
if not all(v.get("ok") for v in status.values()):sys.exit(1)
