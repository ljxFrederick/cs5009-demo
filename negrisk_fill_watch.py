#!/usr/bin/env python3
import asyncio,json,time,urllib.parse,urllib.request
from datetime import datetime,timezone
import websockets

G="https://gamma-api.polymarket.com";C="https://clob.polymarket.com"
WS="wss://ws-subscriptions-clob.polymarket.com/ws/market"
SLUG="lal-ray-bil-2026-10-10"; TARGET_SUB="Athletic Club win"
RUN_SECONDS=120; MAKER_PRICE=0.42; UA="nr-fill-watch/1.0"

def get(url):
    req=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=15) as r:return json.loads(r.read().decode())
def j(x):
    if isinstance(x,(list,dict)):return x
    if isinstance(x,str):
        try:return json.loads(x)
        except:return []
    return []
def lv(items):
    return sorted([(float(v["price"]),float(v["size"])) for v in items or [] if float(v.get("size",0))>0],key=lambda z:z[0])
def sell_yes(token,amt,fee):
    b=get(C+"/book?token_id="+urllib.parse.quote(token))
    bids=sorted([(float(v["price"]),float(v["size"])) for v in b.get("bids",[])],reverse=True)
    rem=amt;out=0
    for p,s in bids:
        t=min(rem,s);out+=t*(p-fee*p*(1-p));rem-=t
        if rem<=1e-12:return out,bids[:5]
    return None,bids[:5]

async def main():
    e=get(G+"/events/slug/"+urllib.parse.quote(SLUG))
    ms=[]
    for x in e.get("markets") or []:
        outs=[str(v) for v in j(x.get("outcomes"))];tids=[str(v) for v in j(x.get("clobTokenIds"))]
        mp={o.casefold():t for o,t in zip(outs,tids)}
        if "yes" not in mp or "no" not in mp:continue
        info=get(C+"/clob-markets/"+urllib.parse.quote(str(x.get("conditionId") or "")))
        fd=info.get("fd");fee=float(fd.get("r") or 0) if isinstance(fd,dict) else 0
        ms.append({"q":str(x.get("question")),"yes":mp["yes"],"no":mp["no"],"fee":fee})
    target=next(m for m in ms if TARGET_SUB.casefold() in m["q"].casefold())
    tokens=[t for m in ms for t in (m["yes"],m["no"])]
    target_no=target["no"]
    print("FILL_WATCH_START",datetime.now(timezone.utc).isoformat(),
          json.dumps({"target":target,"maker_price":MAKER_PRICE,"markets":ms},separators=(",",":")),flush=True)

    frames=0;trades=0;target_no_trades=0;potential_fills=0
    edge_samples=[];fill_events=[]
    start=time.monotonic();deadline=start+RUN_SECONDS;next_ping=start+10;next_sample=start
    async with websockets.connect(WS,ping_interval=None,max_size=4_000_000) as ws:
        await ws.send(json.dumps({"assets_ids":tokens,"type":"market","custom_feature_enabled":True}))
        while time.monotonic()<deadline:
            now=time.monotonic()
            if now>=next_ping:
                await ws.send("PING");next_ping=now+10
            if now>=next_sample:
                try:
                    comp=0;legs=[]
                    for m in ms:
                        if m is target:continue
                        p,bids=await asyncio.to_thread(sell_yes,m["yes"],1,m["fee"])
                        legs.append({"q":m["q"],"proceeds":p,"bids":bids})
                        if p is None:comp=None;break
                        comp+=p
                    if comp is not None:
                        edge=comp-MAKER_PRICE
                        edge_samples.append((time.time(),edge,comp))
                        print("EDGE_SAMPLE",json.dumps({"utc":datetime.now(timezone.utc).isoformat(),
                              "maker":MAKER_PRICE,"complement_proceeds":comp,"profit":edge,"legs":legs},
                              separators=(",",":")),flush=True)
                except Exception as ex:
                    print("EDGE_ERROR",repr(ex),flush=True)
                next_sample=now+5
            try:
                payload=await asyncio.wait_for(ws.recv(),timeout=.5)
            except asyncio.TimeoutError:
                continue
            if payload in ("PING","PONG"):continue
            frames+=1
            try:
                v=json.loads(payload);arr=v if isinstance(v,list) else [v]
            except:continue
            for msg in arr:
                if not isinstance(msg,dict):continue
                kind=msg.get("event_type",msg.get("type"));b=msg.get("payload",msg)
                if not isinstance(b,dict):continue
                if kind!="last_trade_price":continue
                tok=str(b.get("asset_id") or b.get("assetId") or "")
                trades+=1
                if tok!=target_no:continue
                target_no_trades+=1
                side=str(b.get("side") or "").upper()
                try:px=float(b.get("price"));sz=float(b.get("size") or 0)
                except:continue
                rec={"utc":datetime.now(timezone.utc).isoformat(),"side":side,"price":px,"size":sz}
                if side=="SELL" and px<=MAKER_PRICE+1e-12:
                    potential_fills+=1
                    try:
                        comp=0;legs=[]
                        for m in ms:
                            if m is target:continue
                            p,bids=await asyncio.to_thread(sell_yes,m["yes"],min(1,sz),m["fee"])
                            legs.append({"q":m["q"],"proceeds":p,"bids":bids})
                            if p is None:comp=None;break
                            comp+=p
                        if comp is not None:
                            qty=min(1,sz)
                            rec.update({"qty_tested":qty,"complement_proceeds":comp,
                                        "lock_profit":comp-MAKER_PRICE*qty,"legs":legs})
                    except Exception as ex:rec["hedge_error"]=repr(ex)
                fill_events.append(rec)
                print("TARGET_TRADE",json.dumps(rec,separators=(",",":")),flush=True)

    vals=[x[1] for x in edge_samples]
    print("FILL_WATCH_SUMMARY",json.dumps({
       "utc_end":datetime.now(timezone.utc).isoformat(),"duration_s":time.monotonic()-start,
       "frames":frames,"all_last_trade_events":trades,"target_no_trade_events":target_no_trades,
       "potential_fill_events_at_or_below_042":potential_fills,
       "edge_samples_n":len(vals),"edge_min":min(vals) if vals else None,
       "edge_max":max(vals) if vals else None,"edge_mean":sum(vals)/len(vals) if vals else None,
       "target_trades":fill_events},separators=(",",":")),flush=True)

if __name__=="__main__":asyncio.run(main())
