#!/usr/bin/env python3
import json,urllib.parse,urllib.request
G="https://gamma-api.polymarket.com"; C="https://clob.polymarket.com"
SLUGS=[
"lal-ray-bil-2026-10-10-bil",
"will-the-fed-increase-interest-rates-by-25-bps-after-the-january-2027-meeting-20260729233815506",
"will-there-be-no-change-in-fed-interest-rates-after-the-january-2027-meeting-20260729233815505",
]
def get(url):
    req=urllib.request.Request(url,headers={"User-Agent":"version-probe/1.0","Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=15) as r:return json.loads(r.read().decode())
for slug in SLUGS:
    a=get(G+"/markets?slug="+urllib.parse.quote(slug));m=a[0] if a else {}
    keys=["id","slug","question","conditionId","questionID","version","positionIds","clobTokenIds",
          "negRisk","negRiskMarketID","enableOrderBook","feesEnabled","umaEndDate"]
    g={k:m.get(k) for k in keys}
    cid=str(m.get("conditionId") or "")
    cm={}
    for path in ("/clob-markets/","/markets/"):
        if not cid:continue
        try:
            x=get(C+path+urllib.parse.quote(cid))
            cm[path]={k:x.get(k) for k in ["condition_id","conditionId","version","tokens","position_ids","positionIds","market_type","neg_risk","enable_order_book","fd"]}
            cm[path]["keys"]=list(x.keys())[:80]
        except Exception as e: cm[path]={"error":repr(e)}
    print("VERSION_JSON",json.dumps({"gamma":g,"clob":cm},separators=(",",":")),flush=True)
