#!/usr/bin/env python3
import asyncio, json, time, urllib.request, urllib.parse
from collections import defaultdict
from datetime import datetime, timezone
import websockets

GAMMA="https://gamma-api.polymarket.com"
CLOB="https://clob.polymarket.com"
WS="wss://ws-subscriptions-clob.polymarket.com/ws/market"
ASSETS=("btc","eth","sol")
DURS=(5,15)
SIZES=(1,5,10,25)
HORIZONS=(0.0,0.01,0.05,0.10)
RUN_SECONDS=60
UA="polymarket-frame-atomic-hybrid-watch/1.0"

def get_json(url, timeout=15):
    req=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=timeout) as r:
        return json.loads(r.read().decode())

def j(x):
    if isinstance(x,(list,dict)):return x
    if isinstance(x,str):
        try:return json.loads(x)
        except:return []
    return []

def load_market(slug):
    try:
        ev=get_json(f"{GAMMA}/events/slug/{urllib.parse.quote(slug)}")
        ms=ev.get("markets") or []
        if not ms:return None
        m=ms[0]
        outs=[str(v) for v in j(m.get("outcomes"))]
        tids=[str(v) for v in j(m.get("clobTokenIds"))]
        if len(outs)!=2 or len(tids)!=2:return None
        mp={o.casefold():t for o,t in zip(outs,tids)}
        a=mp.get("up") or mp.get("yes"); b=mp.get("down") or mp.get("no")
        if not a or not b:return None
        cid=str(m.get("conditionId") or "")
        info=get_json(f"{CLOB}/clob-markets/{urllib.parse.quote(cid)}")
        fd=info.get("fd"); fee=float(fd.get("r") or 0) if isinstance(fd,dict) else 0.0
        return {"slug":slug,"question":m.get("question"),"a":a,"b":b,"fee":fee,"cid":cid}
    except Exception:return None

def discover():
    now=int(time.time()); out={}
    for asset in ASSETS:
        for mins in DURS:
            step=mins*60; cur=(now//step)*step
            for rs in (cur,cur+step):
                m=load_market(f"{asset}-updown-{mins}m-{rs}")
                if m:
                    m.update(asset=asset,mins=mins,round_start=rs,round_end=rs+step)
                    out[m["slug"]]=m
    return list(out.values())

def levels(items):
    d={}
    for x in items or []:
        p=float(x["price"]); s=float(x["size"])
        if s>0:d[p]=s
    return d

class Book:
    def __init__(self):
        self.bids={}; self.asks={}; self.snapshot=False
        self.best_bid=None; self.best_ask=None
    def apply(self,kind,b):
        if kind=="book":
            self.bids=levels(b.get("bids")); self.asks=levels(b.get("asks")); self.snapshot=True
        elif kind=="price_change" and self.snapshot:
            side=b.get("side"); p=float(b["price"]); s=float(b["size"])
            d=self.bids if side=="BUY" else self.asks
            if s==0:d.pop(p,None)
            else:d[p]=s
        elif kind=="best_bid_ask":
            if b.get("best_bid") is not None:self.best_bid=float(b["best_bid"])
            if b.get("best_ask") is not None:self.best_ask=float(b["best_ask"])
    def bbo(self):
        return (max(self.bids) if self.bids else self.best_bid,
                min(self.asks) if self.asks else self.best_ask)

def buy_cost_share_fee(asks,target,r):
    rem=target; cost=0.0
    for p,s in sorted(asks.items()):
        f=1-r*(1-p)
        cap=s*f
        n=min(rem,cap)
        gross=n/f
        cost+=gross*p; rem-=n
        if rem<=1e-12:return cost
    return None

def sell_proceeds(bids,target,r):
    rem=target; out=0.0
    for p,s in sorted(bids.items(),reverse=True):
        t=min(rem,s)
        out += t*(p-r*p*(1-p))
        rem-=t
        if rem<=1e-12:return out
    return None

def parse_frame(payload):
    if payload in ("PING","PONG"):return []
    v=json.loads(payload); arr=v if isinstance(v,list) else [v]; rows=[]
    for msg in arr:
        if not isinstance(msg,dict):continue
        kind=msg.get("event_type",msg.get("type")); body=msg.get("payload",msg)
        if not isinstance(body,dict):continue
        if kind=="price_change":
            changes=body.get("price_changes") or [body]
            for ch in changes:
                x=dict(body);x.update(ch);rows.append((kind,x))
        else: rows.append((kind,body))
    return rows

def aid(b):
    return str(b.get("asset_id") or b.get("assetId") or b.get("token_id") or b.get("tokenId") or "")

def hedge_margin(trade, market, leg, books, size):
    # Public side is treated as taker side, matching the user's existing settlement decoder convention.
    side=trade["side"]; px=trade["price"]
    other=market["b"] if leg=="a" else market["a"]
    ob=books[other]; r=market["fee"]
    if side=="SELL": # taker sold into our hypothetical maker BUY; we own 'size' of this token.
        c=buy_cost_share_fee(ob.asks,size,r)
        return None if c is None else size-(size*px+c)
    if side=="BUY": # taker bought from our hypothetical maker SELL; hedge by selling complement.
        p=sell_proceeds(ob.bids,size,r)
        return None if p is None else size*px+p-size
    return None

async def main():
    markets=discover()
    tmap={}
    for m in markets:
        tmap[m["a"]]=(m,"a");tmap[m["b"]]=(m,"b")
    tokens=list(tmap); books={t:Book() for t in tokens}
    print("ATOMIC_SCAN_START",datetime.now(timezone.utc).isoformat(),"markets",len(markets),"tokens",len(tokens),flush=True)
    if not tokens:return

    start=time.monotonic();deadline=start+RUN_SECONDS
    frames=events=0
    raw_min=defaultdict(lambda:1e9);raw_max=defaultdict(lambda:-1e9)
    double_best=defaultdict(lambda:-1e9)
    structural_hybrid_best=defaultdict(lambda:-1e9)
    trade_count=defaultdict(int)
    trade_size_total=defaultdict(float)
    horizon_stats={h:defaultdict(lambda:{"n":0,"pos":0,"best":-1e9,"sum_pos":0.0}) for h in HORIZONS}
    pending=[]
    frame_cross_buy=defaultdict(int); frame_cross_sell=defaultdict(int)

    async with websockets.connect(WS,ping_interval=None,max_size=4_000_000) as ws:
        await ws.send(json.dumps({"assets_ids":tokens,"type":"market","custom_feature_enabled":True}))
        next_ping=time.monotonic()+10
        while time.monotonic()<deadline:
            now=time.monotonic()
            if now>=next_ping:
                await ws.send("PING");next_ping=now+10
            try:payload=await asyncio.wait_for(ws.recv(),timeout=min(.5,max(.01,deadline-now)))
            except asyncio.TimeoutError:
                payload=None
            now=time.monotonic()
            if payload and payload!="PONG":
                frames+=1
                rows=parse_frame(payload);events+=len(rows)
                affected=set();trades=[]
                # Apply the whole WS frame first. No within-frame "tradable" states.
                for kind,b in rows:
                    tok=aid(b)
                    if tok not in books:continue
                    affected.add(tmap[tok][0]["slug"])
                    if kind=="last_trade_price":
                        try:
                            side=str(b.get("side") or "").upper()
                            price=float(b.get("price"))
                            size=float(b.get("size") or 0)
                            if side in ("BUY","SELL") and price>0 and size>0:
                                trades.append({"token":tok,"side":side,"price":price,"size":size})
                        except Exception:pass
                    books[tok].apply(kind,b)

                # Compute structural states only after all changes in the frame are installed.
                for slug in affected:
                    m=next(x for x in markets if x["slug"]==slug)
                    A=books[m["a"]];B=books[m["b"]]
                    if not (A.snapshot and B.snapshot):continue
                    abid,aask=A.bbo();bbid,bask=B.bbo()
                    if aask is not None and bask is not None:
                        x=aask+bask;raw_min[slug]=min(raw_min[slug],x)
                        if x<1:frame_cross_buy[slug]+=1
                    if abid is not None and bbid is not None:
                        x=abid+bbid;raw_max[slug]=max(raw_max[slug],x)
                        if x>1:frame_cross_sell[slug]+=1
                    for s in SIZES:
                        c1=buy_cost_share_fee(A.asks,s,m["fee"]);c2=buy_cost_share_fee(B.asks,s,m["fee"])
                        if c1 is not None and c2 is not None:
                            double_best[(slug,"BUY",s)]=max(double_best[(slug,"BUY",s)],s-c1-c2)
                        p1=sell_proceeds(A.bids,s,m["fee"]);p2=sell_proceeds(B.bids,s,m["fee"])
                        if p1 is not None and p2 is not None:
                            double_best[(slug,"SELL",s)]=max(double_best[(slug,"SELL",s)],p1+p2-s)
                        # Conditional maker-fill structural edge; maker rebate deliberately excluded.
                        if abid is not None:
                            c=buy_cost_share_fee(B.asks,s,m["fee"])
                            if c is not None: structural_hybrid_best[(slug,"MBUY_A",s)]=max(structural_hybrid_best[(slug,"MBUY_A",s)],s-(s*abid+c))
                        if bbid is not None:
                            c=buy_cost_share_fee(A.asks,s,m["fee"])
                            if c is not None: structural_hybrid_best[(slug,"MBUY_B",s)]=max(structural_hybrid_best[(slug,"MBUY_B",s)],s-(s*bbid+c))
                        if aask is not None:
                            p=sell_proceeds(B.bids,s,m["fee"])
                            if p is not None: structural_hybrid_best[(slug,"MSELL_A",s)]=max(structural_hybrid_best[(slug,"MSELL_A",s)],s*aask+p-s)
                        if bask is not None:
                            p=sell_proceeds(A.bids,s,m["fee"])
                            if p is not None: structural_hybrid_best[(slug,"MSELL_B",s)]=max(structural_hybrid_best[(slug,"MSELL_B",s)],s*bask+p-s)

                # Actual public-taker-flow proxy. Evaluate immediate and schedule delayed hedge states.
                for tr in trades:
                    m,leg=tmap[tr["token"]]; slug=m["slug"]
                    trade_count[(slug,tr["side"])]+=1;trade_size_total[(slug,tr["side"])]+=tr["size"]
                    for s in SIZES:
                        if tr["size"]+1e-12<s:continue
                        rec={"market":m,"leg":leg,"trade":tr,"size":s,"t0":now}
                        mar=hedge_margin(tr,m,leg,books,s)
                        if mar is not None:
                            st=horizon_stats[0.0][(slug,tr["side"],s)]
                            st["n"]+=1;st["best"]=max(st["best"],mar)
                            if mar>0:st["pos"]+=1;st["sum_pos"]+=mar
                        for h in HORIZONS[1:]:
                            pending.append((now+h,h,rec))

            # Evaluate delayed hedge proxy on first state observed at/after each horizon.
            if pending:
                keep=[]
                for target,h,rec in pending:
                    if now+1e-9<target:
                        keep.append((target,h,rec));continue
                    tr=rec["trade"];m=rec["market"];s=rec["size"];slug=m["slug"]
                    mar=hedge_margin(tr,m,rec["leg"],books,s)
                    if mar is not None:
                        st=horizon_stats[h][(slug,tr["side"],s)]
                        st["n"]+=1;st["best"]=max(st["best"],mar)
                        if mar>0:st["pos"]+=1;st["sum_pos"]+=mar
                pending=keep

    summaries=[]
    for m in markets:
        slug=m["slug"]
        db=[{"type":k[1],"size":k[2],"best_profit":v} for k,v in double_best.items() if k[0]==slug]
        hb=[{"type":k[1],"size":k[2],"best_profit":v} for k,v in structural_hybrid_best.items() if k[0]==slug]
        db.sort(key=lambda x:x["best_profit"],reverse=True);hb.sort(key=lambda x:x["best_profit"],reverse=True)
        flow=[]
        for h in HORIZONS:
            for k,st in horizon_stats[h].items():
                if k[0]!=slug:continue
                flow.append({"horizon_ms":int(h*1000),"taker_side":k[1],"size":k[2],**st})
        flow.sort(key=lambda x:(x["horizon_ms"],-x["best"]))
        summaries.append({
            "slug":slug,"fee":m["fee"],
            "min_atomic_ask_sum":None if raw_min[slug]>1e8 else raw_min[slug],
            "max_atomic_bid_sum":None if raw_max[slug]<-1e8 else raw_max[slug],
            "atomic_buy_cross_frames":frame_cross_buy[slug],"atomic_sell_cross_frames":frame_cross_sell[slug],
            "best_double_taker":db[:8],"best_structural_hybrid":hb[:8],
            "trade_counts":{"BUY":trade_count[(slug,"BUY")],"SELL":trade_count[(slug,"SELL")]},
            "trade_sizes":{"BUY":trade_size_total[(slug,"BUY")],"SELL":trade_size_total[(slug,"SELL")]},
            "fill_proxy":flow[:40]
        })
    print("ATOMIC_HYBRID_SUMMARY",json.dumps({
        "utc_end":datetime.now(timezone.utc).isoformat(),"duration_s":time.monotonic()-start,
        "frames":frames,"events":events,"markets":summaries
    },separators=(",",":")),flush=True)

if __name__=="__main__":asyncio.run(main())
