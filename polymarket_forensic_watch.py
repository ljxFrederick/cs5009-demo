#!/usr/bin/env python3
import asyncio, json, time, urllib.request, urllib.parse
from datetime import datetime, timezone
import websockets

GAMMA="https://gamma-api.polymarket.com"
CLOB="https://clob.polymarket.com"
WS="wss://ws-subscriptions-clob.polymarket.com/ws/market"
RUN_SECONDS=90
UA="polymarket-forensic-arb-check/1.0"
SIZES=(1,5,10)

def request_json(url, method="GET", body=None, timeout=10):
    data=None; headers={"User-Agent":UA,"Accept":"application/json"}
    if body is not None:
        data=json.dumps(body).encode();headers["Content-Type"]="application/json"
    req=urllib.request.Request(url,data=data,headers=headers,method=method)
    with urllib.request.urlopen(req,timeout=timeout) as r:
        return json.loads(r.read().decode())

def j(x):
    if isinstance(x,(list,dict)):return x
    if isinstance(x,str):
        try:return json.loads(x)
        except:return []
    return []

def load_round(ts):
    slug=f"btc-updown-5m-{ts}"
    ev=request_json(f"{GAMMA}/events/slug/{urllib.parse.quote(slug)}")
    m=(ev.get("markets") or [None])[0]
    if not m:return None
    outs=[str(v) for v in j(m.get("outcomes"))];tids=[str(v) for v in j(m.get("clobTokenIds"))]
    mp={o.casefold():t for o,t in zip(outs,tids)}
    a=mp.get("up") or mp.get("yes");b=mp.get("down") or mp.get("no")
    cid=str(m.get("conditionId") or "")
    info=request_json(f"{CLOB}/clob-markets/{urllib.parse.quote(cid)}")
    fd=info.get("fd");fee=float(fd.get("r") or 0) if isinstance(fd,dict) else 0.0
    return {"slug":slug,"a":a,"b":b,"cid":cid,"fee":fee,"question":m.get("question")}

def levels(items):
    out={}
    for x in items or []:
        p=float(x["price"]);s=float(x["size"])
        if s>0:out[p]=s
    return out

class Book:
    def __init__(self):
        self.bids={};self.asks={};self.snapshot=False
    def apply(self,kind,b):
        if kind=="book":
            self.bids=levels(b.get("bids"));self.asks=levels(b.get("asks"));self.snapshot=True
        elif kind=="price_change" and self.snapshot:
            p=float(b["price"]);s=float(b["size"]);d=self.bids if b.get("side")=="BUY" else self.asks
            if s==0:d.pop(p,None)
            else:d[p]=s
    def top(self,n=5):
        return {"bids":sorted(self.bids.items(),reverse=True)[:n],"asks":sorted(self.asks.items())[:n]}

def buy_cost(asks,target,r):
    rem=target;cost=0.0
    for p,s in sorted(asks.items()):
        f=1-r*(1-p)
        cap=s*f;n=min(rem,cap);gross=n/f
        cost+=gross*p;rem-=n
        if rem<=1e-12:return cost
    return None

def sell_proceeds(bids,target,r):
    rem=target;out=0.0
    for p,s in sorted(bids.items(),reverse=True):
        t=min(rem,s);out+=t*(p-r*p*(1-p));rem-=t
        if rem<=1e-12:return out
    return None

def margins(A,B,r):
    out={}
    for s in SIZES:
        c1=buy_cost(A.asks,s,r);c2=buy_cost(B.asks,s,r)
        p1=sell_proceeds(A.bids,s,r);p2=sell_proceeds(B.bids,s,r)
        out[str(s)]={
          "buy":None if c1 is None or c2 is None else s-c1-c2,
          "sell":None if p1 is None or p2 is None else p1+p2-s
        }
    return out

def parse(payload):
    if payload in ("PING","PONG"):return []
    v=json.loads(payload);arr=v if isinstance(v,list) else [v];rows=[]
    for msg in arr:
        if not isinstance(msg,dict):continue
        kind=msg.get("event_type",msg.get("type"));body=msg.get("payload",msg)
        if not isinstance(body,dict):continue
        if kind=="price_change":
            for ch in body.get("price_changes") or [body]:
                x=dict(body);x.update(ch);rows.append((kind,x))
        else:rows.append((kind,body))
    return rows

def aid(b):return str(b.get("asset_id") or b.get("assetId") or "")

def rest_books(tokens):
    t0=time.perf_counter()
    raw=request_json(f"{CLOB}/books",method="POST",body=[{"token_id":t} for t in tokens])
    dt=(time.perf_counter()-t0)*1000
    out={}
    for x in raw:
        tok=str(x.get("asset_id") or "")
        b=Book();b.bids=levels(x.get("bids"));b.asks=levels(x.get("asks"));b.snapshot=True
        out[tok]=b
    return out,dt,raw

async def main():
    now=int(time.time());cur=(now//300)*300
    ms=[]
    for rs in (cur,cur+300):
        try:
            m=load_round(rs)
            if m:ms.append(m)
        except Exception:pass
    if not ms:
        print("NO_MARKET");return
    tmap={}
    for m in ms:tmap[m["a"]]=(m,"a");tmap[m["b"]]=(m,"b")
    books={t:Book() for t in tmap};tokens=list(tmap)
    print("FORENSIC_START",datetime.now(timezone.utc).isoformat(),json.dumps(ms,separators=(",",":")),flush=True)
    deadline=time.monotonic()+RUN_SECONDS;next_ping=time.monotonic()+10
    positive_state={m["slug"]:False for m in ms};captures=[];frames=0
    async with websockets.connect(WS,ping_interval=None,max_size=4_000_000) as ws:
        await ws.send(json.dumps({"assets_ids":tokens,"type":"market","custom_feature_enabled":True}))
        while time.monotonic()<deadline:
            if time.monotonic()>=next_ping:
                await ws.send("PING");next_ping=time.monotonic()+10
            try:p=await asyncio.wait_for(ws.recv(),timeout=.5)
            except asyncio.TimeoutError:continue
            if p=="PONG":continue
            frames+=1;rows=parse(p);affected=set();raw_changes=[]
            for kind,b in rows:
                tok=aid(b)
                if tok not in books:continue
                m,_=tmap[tok];affected.add(m["slug"])
                if kind=="price_change":
                    raw_changes.append({k:b.get(k) for k in ("asset_id","price","size","side","best_bid","best_ask","hash")})
                books[tok].apply(kind,b)
            for m in ms:
                if m["slug"] not in affected:continue
                A=books[m["a"]];B=books[m["b"]]
                if not(A.snapshot and B.snapshot):continue
                mm=margins(A,B,m["fee"])
                pos=any((v["buy"] is not None and v["buy"]>0) or (v["sell"] is not None and v["sell"]>0) for v in mm.values())
                if pos and not positive_state[m["slug"]] and len(captures)<12:
                    ws_time=datetime.now(timezone.utc).isoformat()
                    ws_top={"a":A.top(8),"b":B.top(8)}
                    try:
                        rb,lat,raw=await asyncio.to_thread(rest_books,[m["a"],m["b"]])
                        RA=rb.get(m["a"]);RB=rb.get(m["b"])
                        rmm=margins(RA,RB,m["fee"]) if RA and RB else None
                        rest_top=None if not RA or not RB else {"a":RA.top(8),"b":RB.top(8)}
                        cap={"slug":m["slug"],"ws_utc":ws_time,"rest_latency_ms":lat,
                             "ws_margins":mm,"rest_margins":rmm,"ws_top":ws_top,"rest_top":rest_top,
                             "frame_changes":raw_changes[:30]}
                    except Exception as e:
                        cap={"slug":m["slug"],"ws_utc":ws_time,"ws_margins":mm,"ws_top":ws_top,
                             "rest_error":repr(e),"frame_changes":raw_changes[:30]}
                    captures.append(cap)
                    print("FORENSIC_CAPTURE",json.dumps(cap,separators=(",",":")),flush=True)
                positive_state[m["slug"]]=pos
    print("FORENSIC_SUMMARY",json.dumps({"utc_end":datetime.now(timezone.utc).isoformat(),
      "frames":frames,"capture_count":len(captures),"captures":captures},separators=(",",":")),flush=True)

if __name__=="__main__":asyncio.run(main())
