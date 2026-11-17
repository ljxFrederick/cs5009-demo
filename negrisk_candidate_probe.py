#!/usr/bin/env python3
import json,time,urllib.parse,urllib.request
from datetime import datetime,timezone

G="https://gamma-api.polymarket.com";C="https://clob.polymarket.com";D="https://data-api.polymarket.com"
SLUG="lal-ray-bil-2026-10-10"; UA="nr-candidate-probe/1.0"

def get(url):
    req=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=20) as r:return json.loads(r.read().decode())
def j(x):
    if isinstance(x,(list,dict)):return x
    if isinstance(x,str):
        try:return json.loads(x)
        except:return []
    return []
def levels(x,side):
    arr=[(float(v["price"]),float(v["size"])) for v in x.get(side,[]) if float(v.get("size",0))>0]
    return sorted(arr,reverse=(side=="bids"))
def sell_yes(bids,amt,r):
    rem=amt;out=0
    for p,s in bids:
        t=min(rem,s);out+=t*(p-r*p*(1-p));rem-=t
        if rem<=1e-12:return out
    return None

e=get(G+"/events/slug/"+urllib.parse.quote(SLUG))
ms=[]
for x in e.get("markets") or []:
    outs=[str(v) for v in j(x.get("outcomes"))];tids=[str(v) for v in j(x.get("clobTokenIds"))]
    mp={o.casefold():t for o,t in zip(outs,tids)}
    if "yes" not in mp or "no" not in mp:continue
    info=get(C+"/clob-markets/"+urllib.parse.quote(str(x.get("conditionId") or "")))
    fd=info.get("fd");fee=float(fd.get("r") or 0) if isinstance(fd,dict) else 0
    y=get(C+"/book?token_id="+urllib.parse.quote(mp["yes"]))
    n=get(C+"/book?token_id="+urllib.parse.quote(mp["no"]))
    yb,ya=levels(y,"bids"),levels(y,"asks");nb,na=levels(n,"bids"),levels(n,"asks")
    trades=[]
    try:
        trades=get(D+"/trades?condition_id="+urllib.parse.quote(str(x.get("conditionId") or ""))+"&limit=200")
        if isinstance(trades,dict):trades=trades.get("data") or trades.get("trades") or []
    except Exception as ex:
        trades=[{"error":repr(ex)}]
    ms.append({"q":x.get("question"),"slug":x.get("slug"),"cid":x.get("conditionId"),"fee":fee,
      "yes_token":mp["yes"],"no_token":mp["no"],"yes_bids":yb,"yes_asks":ya,"no_bids":nb,"no_asks":na,"trades":trades})

now=time.time()
for m in ms:
    recent=[]
    side_counts={}
    for t in m["trades"] if isinstance(m["trades"],list) else []:
        if "error" in t:continue
        ts=t.get("timestamp") or t.get("createdAt") or t.get("created_at")
        try:
            z=float(ts)
            if z>1e12:z/=1000
        except:z=None
        side=str(t.get("side") or "")
        out=str(t.get("outcome") or "")
        key=side+":"+out;side_counts[key]=side_counts.get(key,0)+1
        recent.append({"ts":z,"age_s":None if z is None else now-z,"side":side,"outcome":out,
          "price":t.get("price"),"size":t.get("size"),"asset":t.get("asset") or t.get("asset_id")})
    recent.sort(key=lambda x:(x["ts"] or 0),reverse=True)
    print("MARKET_STATE",json.dumps({"q":m["q"],"slug":m["slug"],"fee":m["fee"],
      "yes_top":{"bid":m["yes_bids"][:3],"ask":m["yes_asks"][:3]},
      "no_top":{"bid":m["no_bids"][:3],"ask":m["no_asks"][:3]},
      "trades_returned":len(recent),"trade_side_counts":side_counts,"recent_trades":recent[:12]},separators=(",",":")),flush=True)

print("CANDIDATE_CALC_UTC",datetime.now(timezone.utc).isoformat(),flush=True)
for i,m in enumerate(ms):
    nb=m["no_bids"]
    if not nb:continue
    maker_join=nb[0][0];queue_ahead=nb[0][1]
    complement=0;ok=True
    for k,o in enumerate(ms):
        if k==i:continue
        p=sell_yes(o["yes_bids"],1,o["fee"])
        if p is None:ok=False;break
        complement+=p
    if not ok:continue
    # infer tick from top book price grid, fallback 0.01
    prices=sorted(set([p for p,s in m["no_bids"][:8]+m["no_asks"][:8]]))
    diffs=[round(prices[z+1]-prices[z],10) for z in range(len(prices)-1) if prices[z+1]>prices[z]]
    tick=min(diffs) if diffs else 0.01
    improve=maker_join+tick
    best_ask=m["no_asks"][0][0] if m["no_asks"] else None
    if best_ask is not None and improve>=best_ask:improve=None
    print("CANDIDATE",json.dumps({"maker_NO":m["q"],"join_bid":maker_join,"queue_ahead_shares":queue_ahead,
      "tick":tick,"improve_one_tick":improve,
      "complement_yes_net_bid_proceeds":complement,
      "join_profit_after_conversion":complement-maker_join,
      "improve_profit_after_conversion":None if improve is None else complement-improve,
      "conversion_fee_bips":0},separators=(",",":")),flush=True)
