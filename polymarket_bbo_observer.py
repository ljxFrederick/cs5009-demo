#!/usr/bin/env python3
import json,time,threading,urllib.parse,urllib.request
from datetime import datetime,timezone
import websocket

G="https://gamma-api.polymarket.com"; C="https://clob.polymarket.com"
WS="wss://ws-subscriptions-clob.polymarket.com/ws/market"; UA="pm-bbo-arb/1.0"
RUN_SECONDS=120; ASSETS=("btc","eth","sol"); DURS=(5,15)

def get(url,timeout=10):
    req=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=timeout) as r:return json.loads(r.read().decode())
def j(x):
    if isinstance(x,(list,dict)):return x
    if isinstance(x,str):
        try:return json.loads(x)
        except:return []
    return []
def slug(a,d,now=None):
    now=int(now or time.time()); step=d*60
    return f"{a}-updown-{d}m-{(now//step)*step}"
def market(s):
    e=get(G+"/events/slug/"+urllib.parse.quote(s)); ms=e.get("markets") or []
    if not ms:return None
    x=ms[0]; outs=[str(v) for v in j(x.get("outcomes"))]; tids=[str(v) for v in j(x.get("clobTokenIds"))]
    mp={o.casefold():t for o,t in zip(outs,tids)}
    a=mp.get("up") or mp.get("yes"); b=mp.get("down") or mp.get("no")
    if not a or not b:return None
    info=get(C+"/clob-markets/"+urllib.parse.quote(str(x.get("conditionId") or "")))
    fd=info.get("fd"); r=float(fd.get("r") or 0) if isinstance(fd,dict) else 0
    return {"slug":s,"a":a,"b":b,"r":r,"q":x.get("question")}
def topbook(t):
    x=get(C+"/book?token_id="+urllib.parse.quote(t))
    bids=[(float(z["price"]),float(z["size"])) for z in x.get("bids",[]) if float(z.get("size",0))>0]
    asks=[(float(z["price"]),float(z["size"])) for z in x.get("asks",[]) if float(z.get("size",0))>0]
    bids.sort(reverse=True); asks.sort()
    return {"bid":bids[0] if bids else None,"ask":asks[0] if asks else None}
def robust_buy_cost(p,r):
    # max of share-withheld and USDC-debited interpretations for 1 net share
    share=(p/(1-r*(1-p))) if (1-r*(1-p))>0 else 9
    usdc=p+r*p*(1-p)
    return max(share,usdc)
def sell_net(p,r):return p-r*p*(1-p)
def rest_confirm(m):
    A=topbook(m["a"]);B=topbook(m["b"]);r=m["r"];out={}
    if A["ask"] and B["ask"]:
        edge=1-robust_buy_cost(A["ask"][0],r)-robust_buy_cost(B["ask"][0],r)
        out["buy"]={"profit1":edge,"a":A["ask"],"b":B["ask"]}
    if A["bid"] and B["bid"]:
        edge=sell_net(A["bid"][0],r)+sell_net(B["bid"][0],r)-1
        out["sell"]={"profit1":edge,"a":A["bid"],"b":B["bid"]}
    return out

ms=[m for a in ASSETS for d in DURS if (m:=market(slug(a,d))) is not None]
bytok={}
for m in ms:
    bytok[m["a"]]=m;bytok[m["b"]]=m
bbo={}; frames=0; changes=0; books=0; raw=0; confirmed=[]; errors=[]; last_confirm={}
start=time.time()

def maybe(m,src):
    global raw
    A=bbo.get(m["a"],{});B=bbo.get(m["b"],{});r=m["r"]
    candidates=[]
    if A.get("ask") is not None and B.get("ask") is not None:
        edge=1-robust_buy_cost(A["ask"],r)-robust_buy_cost(B["ask"],r)
        candidates.append(("BUY",edge))
    if A.get("bid") is not None and B.get("bid") is not None:
        edge=sell_net(A["bid"],r)+sell_net(B["bid"],r)-1
        candidates.append(("SELL",edge))
    if not candidates:return
    kind,edge=max(candidates,key=lambda z:z[1])
    if edge<=0:return
    raw+=1
    now=time.time()
    if now-last_confirm.get(m["slug"],0)<0.05:return
    last_confirm[m["slug"]]=now
    try:
        rc=rest_confirm(m)
        vals=[(k,v["profit1"]) for k,v in rc.items()]
        best=max(vals,key=lambda z:z[1]) if vals else None
        rec={"utc":datetime.now(timezone.utc).isoformat(),"slug":m["slug"],"src":src,
             "raw":{"kind":kind,"profit1":edge},"rest":rc}
        if best and best[1]>0:
            confirmed.append(rec);print("CONFIRMED",json.dumps(rec,separators=(",",":")),flush=True)
        else:
            print("RAW_ONLY",json.dumps(rec,separators=(",",":")),flush=True)
    except Exception as e: errors.append(repr(e))

def on_open(ws):
    ws.send(json.dumps({"type":"market","assets_ids":list(bytok.keys()),"markets":[],"initial_dump":True}))
    print("OPEN",len(bytok),flush=True)
    def ping():
        while time.time()-start<RUN_SECONDS+5:
            time.sleep(15)
            try:ws.send("PING")
            except:return
    threading.Thread(target=ping,daemon=True).start()
def on_message(ws,msg):
    global frames,changes,books
    if msg in ("PING","PONG"):return
    frames+=1
    try:v=json.loads(msg)
    except:return
    arr=v if isinstance(v,list) else [v]
    for e in arr:
        if not isinstance(e,dict):continue
        typ=e.get("event_type") or e.get("type")
        if typ=="book":
            books+=1;tid=str(e.get("asset_id") or "")
            if tid not in bytok:continue
            bs=[float(z["price"]) for z in e.get("bids",[]) if float(z.get("size",0))>0]
            ass=[float(z["price"]) for z in e.get("asks",[]) if float(z.get("size",0))>0]
            bbo[tid]={"bid":max(bs) if bs else None,"ask":min(ass) if ass else None}
            maybe(bytok[tid],"book")
        elif typ=="price_change":
            pcs=e.get("price_changes") or e.get("changes") or []
            for z in pcs:
                changes+=1;tid=str(z.get("asset_id") or e.get("asset_id") or "")
                if tid not in bytok:continue
                q=bbo.setdefault(tid,{})
                if z.get("best_bid") is not None:q["bid"]=float(z["best_bid"])
                if z.get("best_ask") is not None:q["ask"]=float(z["best_ask"])
                maybe(bytok[tid],"price_change")
def on_error(ws,e):errors.append(repr(e));print("ERR",repr(e),flush=True)
def on_close(ws,c,r):print("CLOSE",c,r,flush=True)

print("START",datetime.now(timezone.utc).isoformat(),json.dumps([{"slug":m["slug"],"r":m["r"]} for m in ms],separators=(",",":")),flush=True)
ws=websocket.WebSocketApp(WS,on_open=on_open,on_message=on_message,on_error=on_error,on_close=on_close)
th=threading.Thread(target=lambda:ws.run_forever(ping_interval=0),daemon=True);th.start()
for _ in range(RUN_SECONDS//10):
    time.sleep(10)
    print("HB",json.dumps({"elapsed":round(time.time()-start,1),"frames":frames,"changes":changes,"books":books,"raw":raw,"confirmed":len(confirmed)},separators=(",",":")),flush=True)
try:ws.close()
except:pass
print("BBO_SUMMARY",json.dumps({"start":datetime.fromtimestamp(start,timezone.utc).isoformat(),
 "end":datetime.now(timezone.utc).isoformat(),"seconds":RUN_SECONDS,"markets":len(ms),
 "frames":frames,"price_changes":changes,"book_events":books,"raw_positive":raw,
 "confirmed_positive":len(confirmed),"confirmed":confirmed[:50],"errors":errors[:20]},separators=(",",":")),flush=True)
