#!/usr/bin/env python3
import json,time,urllib.parse,urllib.request
from datetime import datetime,timezone
G="https://gamma-api.polymarket.com";D="https://data-api.polymarket.com/v2";UA="relation-fill-history/1.0"
PAIRS=[
("MetaMask 3B=>1B","metamask-fdv-above-3b-one-day-after-launch-363-663-664-569-222","metamask-fdv-above-1b-one-day-after-launch-978-851-628-634",[0.023,0.024,0.025,0.026,0.027,0.028]),
("MetaMask 3B=>2B","metamask-fdv-above-3b-one-day-after-launch-363-663-664-569-222","metamask-fdv-above-2b-one-day-after-launch-222-955-573-228",[0.023,0.024,0.025,0.026,0.027,0.028]),
("StandX 5B=>2B","standx-fdv-above-5b-one-day-after-launch-536-723-718-827-974-669","standx-fdv-above-2b-one-day-after-launch-143-135-174-596-974-988",[0.008,0.009,0.010,0.011]),
]
def get(url):
    req=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=20) as r:return json.loads(r.read().decode())
def j(x):
    if isinstance(x,(list,dict)):return x
    if isinstance(x,str):
        try:return json.loads(x)
        except:return []
    return []
def market(slug):
    x=get(G+"/markets?slug="+urllib.parse.quote(slug))[0]
    outs=[str(v) for v in j(x.get("outcomes"))]; tids=[str(v) for v in j(x.get("clobTokenIds"))]
    mp={o.lower():t for o,t in zip(outs,tids)}
    return {"cid":str(x["conditionId"]),"yes":mp["yes"],"no":mp["no"],"q":x["question"]}
def trades(cid,maxn=10000):
    out=[];cur=None
    while len(out)<maxn:
        q={"condition":cid,"limit":min(1000,maxn-len(out))}
        if cur:q["cursor"]=cur
        z=get(D+"/trades?"+urllib.parse.urlencode(q)); d=z.get("data") or []; out+=d
        cur=(z.get("pagination") or {}).get("next_cursor")
        if not d or not cur:break
    return out
now=time.time()
for name,sa,sb,prices in PAIRS:
    A=market(sa);B=market(sb)
    rows=trades(B["cid"])
    yes=[]
    for x in rows:
        tok=str(x.get("token_id") or "")
        if tok!=B["yes"] and str(x.get("outcome") or "").lower()!="yes":continue
        try:p=float(x["price"]);q=float(x.get("size") or 0);ts=float(x["timestamp"])
        except:continue
        yes.append((ts,str(x.get("side") or "").upper(),p,q))
    ans=[]
    for mp in prices:
        # Maker BUY YES at mp is filled by incoming SELL YES at <=mp.
        fs=[x for x in yes if x[1]=="SELL" and x[2]<=mp+1e-12]
        hs={}
        for label,sec in [("1h",3600),("6h",21600),("24h",86400),("7d",604800),("30d",2592000)]:
            z=[x for x in fs if now-x[0]<=sec]
            hs[label]={"events":len(z),"shares":sum(x[3] for x in z),"last_age_s":min([now-x[0] for x in z],default=None)}
        ans.append({"maker_price":mp,"horizons":hs,"recent":sorted(fs,reverse=True)[:5]})
    print("REL_FILL_HISTORY",json.dumps({"name":name,"A":A["q"],"B":B["q"],"rows":len(rows),"yes_trades":len(yes),"prices":ans},separators=(",",":")),flush=True)
