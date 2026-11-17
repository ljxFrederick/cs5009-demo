#!/usr/bin/env python3
import concurrent.futures as cf, json, math, urllib.parse, urllib.request
from datetime import datetime, timezone

G="https://gamma-api.polymarket.com"; C="https://clob.polymarket.com"
MAXN=1500; UA="nr-jump-queue-scan/1.0"; SAFETY=0.001

def get(url,timeout=20):
    req=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=timeout) as r:return json.loads(r.read().decode())
def j(x):
    if isinstance(x,(list,dict)):return x
    if isinstance(x,str):
        try:return json.loads(x)
        except:return []
    return []
def list_markets():
    out=[];off=0
    while len(out)<MAXN:
        n=min(100,MAXN-len(out))
        q=urllib.parse.urlencode({"active":"true","closed":"false","limit":n,"offset":off,"order":"volumeNum","ascending":"false"})
        a=get(f"{G}/markets?{q}")
        if not a:break
        out.extend(a);off+=len(a)
        if len(a)<n:break
    return out
def mrec(x):
    outs=[str(v) for v in j(x.get("outcomes"))]; tids=[str(v) for v in j(x.get("clobTokenIds"))]
    if len(outs)!=2 or len(tids)!=2:return None
    mp={o.casefold():t for o,t in zip(outs,tids)}
    if "yes" not in mp or "no" not in mp:return None
    cid=str(x.get("conditionId") or "")
    return {"q":str(x.get("question") or ""),"slug":str(x.get("slug") or ""),
            "cid":cid,"yes":mp["yes"],"no":mp["no"],
            "active":bool(x.get("active")),"closed":bool(x.get("closed")),
            "fees":bool(x.get("feesEnabled"))}
def book(t):
    x=get(C+"/book?token_id="+urllib.parse.quote(t))
    bids=sorted([(float(v["price"]),float(v["size"])) for v in x.get("bids",[]) if float(v.get("size",0))>0],reverse=True)
    asks=sorted([(float(v["price"]),float(v["size"])) for v in x.get("asks",[]) if float(v.get("size",0))>0])
    return bids,asks
def info(m):
    x=get(C+"/clob-markets/"+urllib.parse.quote(m["cid"]))
    fd=x.get("fd"); fee=float(fd.get("r") or 0) if isinstance(fd,dict) else 0.0
    try:
        t=get(C+"/tick-size?token_id="+urllib.parse.quote(m["no"]))
        tick=float(t.get("minimum_tick_size") or 0.01)
    except: tick=0.01
    return fee,tick
def sell(bids,qty,r):
    rem=qty;out=0.0
    for p,s in bids:
        t=min(rem,s);out+=t*(p-r*p*(1-p));rem-=t
        if rem<=1e-12:return out
    return None

raw=list_markets()
slugs={}
for x in raw:
    for e in x.get("events") or []:
        if e.get("negRisk") and not e.get("negRiskAugmented") and e.get("slug"):
            slugs[str(e["slug"])]=1
evs=[]
with cf.ThreadPoolExecutor(max_workers=24) as ex:
    fut={ex.submit(get,G+"/events/slug/"+urllib.parse.quote(s)):s for s in slugs}
    for f in cf.as_completed(fut):
        try:
            e=f.result()
            if isinstance(e,dict):evs.append(e)
        except:pass

events=[]
for e in evs:
    xs=e.get("markets") or []
    ms=[m for x in xs if (m:=mrec(x))]
    if len(ms)<2 or len(ms)!=len(xs) or not all(m["active"] and not m["closed"] for m in ms):continue
    events.append((e,ms))

tids={t for _,ms in events for m in ms for t in (m["yes"],m["no"])}
books={}
with cf.ThreadPoolExecutor(max_workers=50) as ex:
    fut={ex.submit(book,t):t for t in tids}
    for f in cf.as_completed(fut):
        try:books[fut[f]]=f.result()
        except:pass
meta={}
allm=[m for _,ms in events for m in ms]
with cf.ThreadPoolExecutor(max_workers=30) as ex:
    fut={ex.submit(info,m):m["cid"] for m in allm}
    for f in cf.as_completed(fut):
        try:meta[fut[f]]=f.result()
        except:pass

rows=[]
for e,ms in events:
    if any(m["yes"] not in books or m["no"] not in books or m["cid"] not in meta for m in ms):continue
    for i,m in enumerate(ms):
        nb,na=books[m["no"]]; 
        if not nb or not na:continue
        comp=0.0;legs=[];ok=True
        for j2,o in enumerate(ms):
            if j2==i:continue
            fee,_=meta[o["cid"]]
            p=sell(books[o["yes"]][0],1.0,fee)
            if p is None:ok=False;break
            comp+=p;legs.append({"q":o["q"],"net_bid_proceeds":p})
        if not ok:continue
        best_bid,queue=nb[0]; best_ask=na[0][0]; _,tick=meta[m["cid"]]
        join_edge=comp-best_bid
        jump=round(best_bid+tick,10)
        jump_valid=jump < best_ask-1e-12
        jump_edge=(comp-jump) if jump_valid else None
        # Highest maker price that leaves SAFETY edge, capped one tick below ask.
        max_by_edge=math.floor((comp-SAFETY+1e-12)/tick)*tick
        max_maker=min(max_by_edge,best_ask-tick)
        max_maker=round(max_maker,10)
        max_edge=comp-max_maker if max_maker>best_bid+1e-12 else None
        rows.append({
          "event":str(e.get("title") or e.get("slug") or ""),"event_slug":str(e.get("slug") or ""),
          "maker_market":m["q"],"maker_slug":m["slug"],"cid":m["cid"],"no_token":m["no"],
          "best_bid":best_bid,"queue_ahead_if_join":queue,"best_ask":best_ask,"tick":tick,
          "complement_yes_net_bid_proceeds_1":comp,
          "join_edge_1":join_edge,
          "jump_price":jump if jump_valid else None,"jump_edge_1":jump_edge,
          "max_maker_price_with_0p1c_buffer":max_maker if max_edge is not None else None,
          "edge_at_max_maker":max_edge,
          "queue_bypass_possible":bool(max_edge is not None and max_edge>0),
          "legs":legs
        })
rows.sort(key=lambda z:(z["queue_bypass_possible"], z["edge_at_max_maker"] or -99, z["join_edge_1"]),reverse=True)
jumpers=[x for x in rows if x["queue_bypass_possible"]]
joiners=[x for x in rows if x["join_edge_1"]>0]
print("JUMP_SCAN_SUMMARY",json.dumps({
 "utc":datetime.now(timezone.utc).isoformat(),"events":len(events),"markets":len(rows),
 "positive_join":len(joiners),"queue_bypass_positive":len(jumpers),
 "best_jumpers":jumpers[:30],"best_joiners":joiners[:30]
},separators=(",",":")),flush=True)
