#!/usr/bin/env python3
import json,time,urllib.request
from collections import defaultdict
from datetime import datetime,timezone

rows=[]
for page in range(1,16):
    u=f"https://api.geckoterminal.com/api/v2/networks/base/pools?page={page}"
    try:
        req=urllib.request.Request(u,headers={"User-Agent":"base-pool-spread/1.0","Accept":"application/json"})
        with urllib.request.urlopen(req,timeout=20) as r:x=json.loads(r.read().decode())
        rows.extend(x.get("data") or [])
    except Exception as e:
        print("PAGE_ERR",page,repr(e),flush=True)
    time.sleep(.35)

pairs=defaultdict(list)
for p in rows:
    a=p.get("attributes") or {}; rel=p.get("relationships") or {}
    bt=((rel.get("base_token") or {}).get("data") or {}).get("id","").replace("base_","").lower()
    qt=((rel.get("quote_token") or {}).get("data") or {}).get("id","").replace("base_","").lower()
    dex=((rel.get("dex") or {}).get("data") or {}).get("id","")
    if not bt or not qt or bt==qt:continue
    try:
        pq=float(a.get("base_token_price_quote_token"))
        reserve=float(a.get("reserve_in_usd") or 0)
        vol5=float((a.get("volume_usd") or {}).get("m5") or 0)
        vol1=float((a.get("volume_usd") or {}).get("h1") or 0)
        tx5=sum(int(x) for k,x in ((a.get("transactions") or {}).get("m5") or {}).items() if k in ("buys","sells"))
    except:continue
    if pq<=0:continue
    t0,t1=sorted([bt,qt])
    # normalized = units of t1 per one t0
    norm=pq if bt==t0 else 1/pq
    pairs[(t0,t1)].append({"pool":a.get("address"),"name":a.get("name"),"dex":dex,
                           "price":norm,"reserve":reserve,"vol5":vol5,"vol1":vol1,"tx5":tx5,
                           "base":bt,"quote":qt,"raw_pq":pq})
cand=[]
for pair,xs in pairs.items():
    liquid=[x for x in xs if x["reserve"]>=5000]
    if len(liquid)<2:continue
    lo=min(liquid,key=lambda z:z["price"]);hi=max(liquid,key=lambda z:z["price"])
    spread=hi["price"]/lo["price"]-1
    if spread<=0:continue
    cand.append({"pair":pair,"spread_pct":spread*100,"spread_bps":spread*10000,
                 "low":lo,"high":hi,"pools_seen":len(xs),"liquid_pools":len(liquid),
                 "min_reserve":min(lo["reserve"],hi["reserve"]),
                 "combined_h1_vol":lo["vol1"]+hi["vol1"]})
cand.sort(key=lambda z:(z["spread_bps"],z["min_reserve"]),reverse=True)
active=sorted(cand,key=lambda z:(z["combined_h1_vol"],z["spread_bps"]),reverse=True)
print("POOL_SPREAD_SUMMARY",json.dumps({"utc":datetime.now(timezone.utc).isoformat(),
 "pools":len(rows),"pairs":len(pairs),"candidates":cand[:100],"active_candidates":active[:100]},separators=(",",":")),flush=True)
