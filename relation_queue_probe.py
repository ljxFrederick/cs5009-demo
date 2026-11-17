#!/usr/bin/env python3
import json,math,urllib.parse,urllib.request
from datetime import datetime,timezone

G="https://gamma-api.polymarket.com";C="https://clob.polymarket.com";UA="relation-queue-probe/1.0"
# A => B
PAIRS=[
("metamask-fdv-above-3b-one-day-after-launch-363-663-664-569-222","metamask-fdv-above-1b-one-day-after-launch-978-851-628-634"),
("metamask-fdv-above-3b-one-day-after-launch-363-663-664-569-222","metamask-fdv-above-2b-one-day-after-launch-222-955-573-228"),
("standx-fdv-above-5b-one-day-after-launch-536-723-718-827-974-669","standx-fdv-above-2b-one-day-after-launch-143-135-174-596-974-988"),
("standx-fdv-above-3b-one-day-after-launch-573-228-889-488-762-236","standx-fdv-above-2b-one-day-after-launch-143-135-174-596-974-988"),
("will-anthropic-ipo-by-december-15-2026","will-anthropic-ipo-by-december-31-2026-546-128-719"),
("will-anthropic-ipo-by-november-20-2026","will-anthropic-ipo-by-november-25-2026"),
]
BUFFER=0.001

def get(url):
    req=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=20) as r:return json.loads(r.read().decode())
def j(x):
    if isinstance(x,(list,dict)):return x
    if isinstance(x,str):
        try:return json.loads(x)
        except:return []
    return []
def m(slug):
    a=get(G+"/markets?slug="+urllib.parse.quote(slug));x=a[0]
    outs=[str(v) for v in j(x.get("outcomes"))];tids=[str(v) for v in j(x.get("clobTokenIds"))]
    mp={o.casefold():t for o,t in zip(outs,tids)}
    info=get(C+"/clob-markets/"+urllib.parse.quote(str(x.get("conditionId") or "")))
    fd=info.get("fd");fee=float(fd.get("r") or 0) if isinstance(fd,dict) else 0
    return {"q":x.get("question"),"slug":slug,"yes":mp["yes"],"no":mp["no"],"fee":fee,
            "desc":x.get("description"),"end":x.get("endDate"),"source":x.get("resolutionSource")}
def book(tok):
    x=get(C+"/book?token_id="+urllib.parse.quote(tok))
    bids=sorted([(float(z["price"]),float(z["size"])) for z in x.get("bids",[]) if float(z.get("size",0))>0],reverse=True)
    asks=sorted([(float(z["price"]),float(z["size"])) for z in x.get("asks",[]) if float(z.get("size",0))>0])
    t=get(C+"/tick-size?token_id="+urllib.parse.quote(tok))
    return bids,asks,float(t.get("minimum_tick_size") or .01)
def buy1(asks,r):
    if not asks:return None
    # Walk enough gross shares to receive 1 net share after fee-in-shares.
    rem=1.;cost=0.
    for p,s in asks:
        f=1-r*(1-p);cap=s*f;n=min(rem,cap);cost+=(n/f)*p;rem-=n
        if rem<=1e-12:return cost
    return None
def eval_maker(tok,taker_cost):
    bids,asks,tick=book(tok)
    if not bids or not asks or taker_cost is None:return None
    bid,queue=bids[0];ask=asks[0][0]
    edge=1-bid-taker_cost
    jump=round(bid+tick,10)
    jump_edge=1-jump-taker_cost if jump<ask-1e-12 else None
    max_by=math.floor((1-taker_cost-BUFFER+1e-12)/tick)*tick
    mx=round(min(max_by,ask-tick),10)
    mx_edge=1-mx-taker_cost if mx>bid+1e-12 else None
    return {"bid":bid,"queue":queue,"ask":ask,"tick":tick,"join_edge":edge,
            "jump":jump if jump<ask-1e-12 else None,"jump_edge":jump_edge,
            "max_maker":mx if mx_edge is not None else None,"max_edge":mx_edge,
            "top_bids":bids[:5],"top_asks":asks[:5]}

rows=[]
for sa,sb in PAIRS:
    try:
        A=m(sa);B=m(sb)
        noA=book(A["no"]);yesB=book(B["yes"])
        ca=buy1(noA[1],A["fee"]);cb=buy1(yesB[1],B["fee"])
        r1=eval_maker(A["no"],cb) # maker NO(A), taker YES(B)
        r2=eval_maker(B["yes"],ca) # maker YES(B), taker NO(A)
        rows.append({"A":A["q"],"A_slug":sa,"B":B["q"],"B_slug":sb,
                     "maker_NO_A":r1,"maker_YES_B":r2,
                     "taker_cost_NO_A":ca,"taker_cost_YES_B":cb,
                     "rules_exact_desc":A["desc"]==B["desc"],"same_source":A["source"]==B["source"]})
    except Exception as e:rows.append({"A_slug":sa,"B_slug":sb,"error":repr(e)})
print("REL_QUEUE_SUMMARY",json.dumps({"utc":datetime.now(timezone.utc).isoformat(),"pairs":rows},separators=(",",":")),flush=True)
