#!/usr/bin/env python3
import asyncio,json,time,urllib.parse,urllib.request
from datetime import datetime,timezone
import websockets

G="https://gamma-api.polymarket.com"; C="https://clob.polymarket.com"
WS="wss://ws-subscriptions-clob.polymarket.com/ws/market"
SLUG="fed-decision-in-january-20260729233815502"
RUN_SECONDS=120; UA="fed-nr-fill-watch/1.0"

def get(url):
    req=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=15) as r:return json.loads(r.read().decode())
def j(x):
    if isinstance(x,(list,dict)):return x
    if isinstance(x,str):
        try:return json.loads(x)
        except:return []
    return []
def book(token):
    x=get(C+"/book?token_id="+urllib.parse.quote(token))
    bids=sorted([(float(v["price"]),float(v["size"])) for v in x.get("bids",[]) if float(v.get("size",0))>0],reverse=True)
    asks=sorted([(float(v["price"]),float(v["size"])) for v in x.get("asks",[]) if float(v.get("size",0))>0])
    return bids,asks
def sell_yes(token,amt,fee):
    bids,_=book(token);rem=amt;out=0
    for p,s in bids:
        t=min(rem,s);out+=t*(p-fee*p*(1-p));rem-=t
        if rem<=1e-12:return out,bids[:4]
    return None,bids[:4]
def load():
    e=get(G+"/events/slug/"+urllib.parse.quote(SLUG)); ms=[]
    for x in e.get("markets") or []:
        outs=[str(v) for v in j(x.get("outcomes"))]; tids=[str(v) for v in j(x.get("clobTokenIds"))]
        mp={o.casefold():t for o,t in zip(outs,tids)}
        if "yes" not in mp or "no" not in mp:continue
        info=get(C+"/clob-markets/"+urllib.parse.quote(str(x.get("conditionId") or "")))
        fd=info.get("fd"); fee=float(fd.get("r") or 0) if isinstance(fd,dict) else 0
        ms.append({"q":str(x.get("question")),"slug":str(x.get("slug")),"yes":mp["yes"],"no":mp["no"],"fee":fee,
                   "volume24hr":x.get("volume24hr"),"volume":x.get("volume")})
    return e,ms

def candidate_state(ms,qty=1):
    out=[]
    for target in ms:
        nb,na=book(target["no"])
        if not nb:continue
        px=nb[0][0];qahead=nb[0][1];ask=na[0][0] if na else None
        comp=0;legs=[];ok=True
        for m in ms:
            if m is target:continue
            p,bids=sell_yes(m["yes"],qty,m["fee"])
            if p is None:ok=False;break
            comp+=p;legs.append({"q":m["q"],"proceeds":p,"top_bids":bids})
        if not ok:continue
        out.append({"target":target,"maker_price":px,"queue":qahead,"no_ask":ask,
                    "complement":comp,"profit":comp-px*qty,"roi_cash":(comp-px*qty)/(px*qty) if px else None,
                    "legs":legs})
    return sorted(out,key=lambda z:z["profit"],reverse=True)

async def main():
    e,ms=load(); init=candidate_state(ms)
    print("FED_WATCH_INIT",datetime.now(timezone.utc).isoformat(),
          json.dumps({"title":e.get("title"),"volume":e.get("volume"),"candidates":init},separators=(",",":")),flush=True)
    positives=[x for x in init if x["profit"]>0]
    if not positives:
        print("FED_WATCH_SUMMARY",json.dumps({"reason":"no_positive_candidate","init":init},separators=(",",":")));return
    target=positives[0]; target_no=target["target"]["no"]; maker=target["maker_price"]
    tokens=[t for m in ms for t in (m["yes"],m["no"])]
    start=time.monotonic();deadline=start+RUN_SECONDS;next_ping=start+10;next_sample=start
    frames=trades=target_events=potential=0; samples=[];tevents=[]
    async with websockets.connect(WS,ping_interval=None,max_size=4_000_000) as ws:
        await ws.send(json.dumps({"assets_ids":tokens,"type":"market","custom_feature_enabled":True}))
        while time.monotonic()<deadline:
            now=time.monotonic()
            if now>=next_ping:
                await ws.send("PING");next_ping=now+10
            if now>=next_sample:
                try:
                    cs=await asyncio.to_thread(candidate_state,ms,1)
                    row=next((x for x in cs if x["target"]["no"]==target_no),None)
                    if row:
                        samples.append(row["profit"])
                        print("FED_EDGE_SAMPLE",json.dumps({"utc":datetime.now(timezone.utc).isoformat(),**row},separators=(",",":")),flush=True)
                except Exception as ex: print("FED_EDGE_ERR",repr(ex),flush=True)
                next_sample=now+3
            try:payload=await asyncio.wait_for(ws.recv(),timeout=.5)
            except asyncio.TimeoutError:continue
            if payload in ("PING","PONG"):continue
            frames+=1
            try:v=json.loads(payload);arr=v if isinstance(v,list) else [v]
            except:continue
            for msg in arr:
                if not isinstance(msg,dict):continue
                kind=msg.get("event_type",msg.get("type"));b=msg.get("payload",msg)
                if kind!="last_trade_price" or not isinstance(b,dict):continue
                trades+=1
                tok=str(b.get("asset_id") or b.get("assetId") or "")
                if tok!=target_no:continue
                target_events+=1
                side=str(b.get("side") or "").upper()
                try:px=float(b.get("price"));sz=float(b.get("size") or 0)
                except:continue
                rec={"utc":datetime.now(timezone.utc).isoformat(),"side":side,"price":px,"size":sz}
                if side=="SELL" and px<=maker+1e-12:
                    potential+=1
                    cs=await asyncio.to_thread(candidate_state,ms,min(1,sz))
                    row=next((x for x in cs if x["target"]["no"]==target_no),None)
                    rec["hedge_snapshot"]=row
                tevents.append(rec)
                print("FED_TARGET_TRADE",json.dumps(rec,separators=(",",":")),flush=True)
    print("FED_WATCH_SUMMARY",json.dumps({
      "utc_end":datetime.now(timezone.utc).isoformat(),"duration_s":time.monotonic()-start,
      "selected":{"q":target["target"]["q"],"maker_price":maker,"initial_queue":target["queue"],
                  "initial_profit":target["profit"],"initial_roi_cash":target["roi_cash"]},
      "frames":frames,"all_trade_events":trades,"target_no_trade_events":target_events,
      "potential_fill_events":potential,"samples_n":len(samples),
      "edge_min":min(samples) if samples else None,"edge_max":max(samples) if samples else None,
      "edge_mean":sum(samples)/len(samples) if samples else None,"target_trades":tevents
    },separators=(",",":")),flush=True)

if __name__=="__main__":asyncio.run(main())
