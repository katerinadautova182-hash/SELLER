"""Inspect posting details for control Ozon postings."""
from ozon_export.client import OzonClient

POSTINGS=[
"31596582-0419-1",
"0221557140-0150-1",
"16847541-0208-1",
"05779053-0995-1",
]

def pick_dates(obj):
    out={}
    def walk(x,path=""):
        if isinstance(x,dict):
            for k,v in x.items():
                p=f"{path}.{k}" if path else k
                lk=k.lower()
                if any(t in lk for t in ("date","time","created","deliv","status")) and not isinstance(v,(dict,list)):
                    out[p]=v
                walk(v,p)
        elif isinstance(x,list):
            for i,v in enumerate(x):
                walk(v,f"{path}[{i}]")
    walk(obj)
    return out

def main():
    c=OzonClient()
    for p in POSTINGS:
        print("POSTING",p)
        ok=False
        for endpoint,payload in [
            ("/v2/posting/fbo/get",{"posting_number":p,"with":{"analytics_data":True,"financial_data":True,"legal_info":False}}),
            ("/v3/posting/fbs/get",{"posting_number":p,"with":{"analytics_data":True,"financial_data":True,"legal_info":False}})
        ]:
            try:
                data=c.post(endpoint,payload)
                print("SCHEME",endpoint)
                print("DATES",pick_dates(data))
                ok=True
                break
            except Exception as e:
                print("MISS",endpoint,type(e).__name__)
        if not ok:
            print("NO POSTING DETAILS")

if __name__=="__main__":
    main()
