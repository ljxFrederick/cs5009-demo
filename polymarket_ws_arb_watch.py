#!/usr/bin/env python3
import asyncio, json, math, time, urllib.request, urllib.parse
from collections import defaultdict
from datetime import datetime, timezone

import websockets

GAMMA="https://gamma-api.polymarket.com"
CLOB="https://clob.polymarket.com"
WS="wss://ws-subscriptions-clob.polymarket.com/ws/market"
ASSETS=("btc","eth","sol")
DURS=(5,15)
SIZES=(1,5,10,25,50,100)
RUN_SECONDS=90
UA="polymarket-ws-arb-watch/1.0"

def get_json(url, timeout=15):
    req=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=timeout) as r:
        return json.loads(r.read().decode())

def j(x):
    if isinstance(x,(list,dict)): return x
    if isinstance(x,str):
        try:return json.loads(x)
        except:return []
    return []

def event_for_slug(slug):
    try:
        ev=get_json(f"{GAMMA}/events/slug/{urllib.parse.quote(slug)}")
        ms=ev.get("markets") or []
        if not ms:return None
        m=ms[0]
        outs=[str(v) for v in j(m.get("outcomes"))]
        tids=[str(v) for v in j(m.get("clobTokenIds"))]
        if len(outs)!=2 or len(tids)!=2:return None
        mp={o.casefold():t for o,t in zip(outs,tids)}
        a=mp.get("up") or mp.get("yes")
        b=mp.get("down") or mp.get("no")
        if not a or not b:return None
        cid=str(m.get("conditionId") or "")
        info=get_json(f"{CLOB}/clob-markets/{urllib.parse.quote(cid)}") if cid else {}
        fd=info.get("fd")
        fee=float(fd.get("r") or 0.0) if isinstance(fd,dict) else 0.0
        return {"slug":slug,"question":m.get("question"),"a":a,"b":b,"fee":fee,"cid":cid}
    except Exception as e:
        return None

def discover():
    now=int(time.time())
    out=[]
    for asset in ASSETS:
        for mins in DURS:
            step=mins*60
            cur=(now//step)*step
            for rs in (cur,cur+step):
                slug=f"{asset}-updown-{mins}m-{rs}"
                m=event_for_slug(slug)
                if m:
                    m.update(asset=asset,mins=mins,round_start=rs,round_end=rs+step)
                    out.append(m)
    dedup={m["slug"]:m for m in out}
    return list(dedup.values())

def levels(items):
    out={}
    for x in items or []:
        p=float(x["price"]); s=float(x["size"])
        if s>0:out[p]=s
    return out

class Book:
    def __init__(self):
        self.bids={}
        self.asks={}
        self.best_bid=None
        self.best_ask=None
        self.snapshot=False
    def apply(self, kind, body):
        if kind=="book":
            self.bids=levels(body.get("bids"))
            self.asks=levels(body.get("asks"))
            self.snapshot=True
        elif kind=="price_change" and self.snapshot:
            side=body.get("side")
            p=float(body["price"]); s=float(body["size"])
            d=self.bids if side=="BUY" else self.asks
            if s==0:d.pop(p,None)
            else:d[p]=s
        elif kind=="best_bid_ask":
            if body.get("best_bid") is not None:self.best_bid=float(body["best_bid"])
            if body.get("best_ask") is not None:self.best_ask=float(body["best_ask"])
    def bbo(self):
        bid=max(self.bids) if self.bids else self.best_bid
        ask=min(self.asks) if self.asks else self.best_ask
        return bid,ask

def buy_cost_usdc(asks,target,r):
    rem=target; cost=0.0
    for p,s in sorted(asks.items()):
        t=min(rem,s)
        cost += t*(p + r*p*(1-p))
        rem -= t
        if rem<=1e-12:return cost
    return None

def buy_cost_sharefee(asks,target,r):
    rem=target; cost=0.0
    for p,s in sorted(asks.items()):
        f=1-r*(1-p)
        cap=s*f
        tn=min(rem,cap)
        cost += (tn/f)*p
        rem -= tn
        if rem<=1e-12:return cost
    return None

def robust_buy_cost(asks,target,r):
    a=buy_cost_usdc(asks,target,r)
    b=buy_cost_sharefee(asks,target,r)
    if a is None or b is None:return None
    return max(a,b)

def sell_proceeds(bids,target,r):
    rem=target; out=0.0
    for p,s in sorted(bids.items(),reverse=True):
        t=min(rem,s)
        out += t*(p-r*p*(1-p))
        rem -= t
        if rem<=1e-12:return out
    return None

def parse_messages(payload):
    if payload in ("PONG","PING"):return []
    val=json.loads(payload)
    arr=val if isinstance(val,list) else [val]
    rows=[]
    for msg in arr:
        if not isinstance(msg,dict):continue
        kind=msg.get("event_type",msg.get("type"))
        body=msg.get("payload",msg)
        if not isinstance(body,dict):continue
        if kind=="price_change":
            changes=body.get("price_changes") or [body]
            for ch in changes:
                x=dict(body);x.update(ch)
                rows.append((kind,x))
        else:
            rows.append((kind,body))
    return rows

def aid(body):
    return str(body.get("asset_id") or body.get("assetId") or body.get("token_id") or body.get("tokenId") or "")

async def main():
    markets=discover()
    token_to_market={}
    for m in markets:
        token_to_market[m["a"]]=(m,"a")
        token_to_market[m["b"]]=(m,"b")
    tokens=list(token_to_market)
    print("WS_SCAN_START",datetime.now(timezone.utc).isoformat(),"markets",len(markets),"tokens",len(tokens),flush=True)
    for m in markets: print("MARKET",json.dumps(m,separators=(",",":")),flush=True)
    if not tokens:
        print("NO_MARKETS");return

    books={t:Book() for t in tokens}
    start=time.monotonic(); deadline=start+RUN_SECONDS
    frames=events=checks=0
    first_ready={}
    # state per (slug, kind, size)
    active={}
    episodes=[]
    best_margin=defaultdict(lambda:-1e9)
    worst_gap=defaultdict(lambda:1e9)
    min_raw_buy=defaultdict(lambda:1e9)
    max_raw_sell=defaultdict(lambda:-1e9)

    async with websockets.connect(WS,ping_interval=None,max_size=4_000_000) as ws:
        await ws.send(json.dumps({"assets_ids":tokens,"type":"market","custom_feature_enabled":True}))
        next_ping=time.monotonic()+10
        while time.monotonic()<deadline:
            if time.monotonic()>=next_ping:
                await ws.send("PING"); next_ping=time.monotonic()+10
            try:
                payload=await asyncio.wait_for(ws.recv(),timeout=min(1,max(.01,deadline-time.monotonic())))
            except asyncio.TimeoutError:
                continue
            frames+=1
            if payload=="PONG":continue
            now=time.monotonic()
            wall=time.time()
            for kind,body in parse_messages(payload):
                events+=1
                token=aid(body)
                if token not in books:continue
                books[token].apply(kind,body)
                m,leg=token_to_market[token]
                A=books[m["a"]];B=books[m["b"]]
                if not (A.snapshot and B.snapshot):continue
                if m["slug"] not in first_ready:first_ready[m["slug"]]=wall
                checks+=1
                abid,aask=A.bbo(); bbid,bask=B.bbo()
                if aask is not None and bask is not None:
                    raw=aask+bask
                    min_raw_buy[m["slug"]]=min(min_raw_buy[m["slug"]],raw)
                if abid is not None and bbid is not None:
                    raw=abid+bbid
                    max_raw_sell[m["slug"]]=max(max_raw_sell[m["slug"]],raw)
                for s in SIZES:
                    bc1=robust_buy_cost(A.asks,s,m["fee"])
                    bc2=robust_buy_cost(B.asks,s,m["fee"])
                    if bc1 is not None and bc2 is not None:
                        margin=s-(bc1+bc2)
                        key=(m["slug"],"BUY",s)
                        best_margin[key]=max(best_margin[key],margin)
                        if margin>0 and key not in active:
                            active[key]={"start_mono":now,"start_wall":wall,"peak":margin,"updates":1}
                        elif margin>0:
                            active[key]["peak"]=max(active[key]["peak"],margin);active[key]["updates"]+=1
                        elif key in active:
                            z=active.pop(key);z.update(slug=m["slug"],kind="BUY",size=s,end_wall=wall,duration_ms=(now-z["start_mono"])*1000)
                            episodes.append(z)
                    sp1=sell_proceeds(A.bids,s,m["fee"])
                    sp2=sell_proceeds(B.bids,s,m["fee"])
                    if sp1 is not None and sp2 is not None:
                        margin=sp1+sp2-s
                        key=(m["slug"],"SELL",s)
                        best_margin[key]=max(best_margin[key],margin)
                        if margin>0 and key not in active:
                            active[key]={"start_mono":now,"start_wall":wall,"peak":margin,"updates":1}
                        elif margin>0:
                            active[key]["peak"]=max(active[key]["peak"],margin);active[key]["updates"]+=1
                        elif key in active:
                            z=active.pop(key);z.update(slug=m["slug"],kind="SELL",size=s,end_wall=wall,duration_ms=(now-z["start_mono"])*1000)
                            episodes.append(z)

    end=time.monotonic(); wall=time.time()
    for (slug,kind,s),z in list(active.items()):
        z.update(slug=slug,kind=kind,size=s,end_wall=wall,duration_ms=(end-z["start_mono"])*1000,censored=True)
        episodes.append(z)

    summaries=[]
    for m in markets:
        slug=m["slug"]
        bm=[]
        for k,v in best_margin.items():
            if k[0]==slug:
                bm.append({"kind":k[1],"size":k[2],"best_profit":v,"best_roi_vs_payout":v/k[2]})
        bm.sort(key=lambda x:x["best_profit"],reverse=True)
        summaries.append({
            "slug":slug,"fee":m["fee"],
            "ready":slug in first_ready,
            "min_raw_ask_sum":None if min_raw_buy[slug]>=1e8 else min_raw_buy[slug],
            "max_raw_bid_sum":None if max_raw_sell[slug]<=-1e8 else max_raw_sell[slug],
            "best_fee_adjusted":bm[:6],
            "episodes":sum(1 for e in episodes if e["slug"]==slug)
        })
    print("WS_SCAN_SUMMARY",json.dumps({
        "utc_end":datetime.now(timezone.utc).isoformat(),
        "duration_s":end-start,"frames":frames,"events":events,"checks":checks,
        "markets":summaries,
        "episode_count":len(episodes),
        "episodes":sorted(episodes,key=lambda e:e["peak"],reverse=True)[:50]
    },separators=(",",":")),flush=True)

if __name__=="__main__":
    asyncio.run(main())
