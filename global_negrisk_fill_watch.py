#!/usr/bin/env python3
import asyncio, concurrent.futures as cf, json, time, urllib.parse, urllib.request
from collections import defaultdict
from datetime import datetime, timezone
import websockets
from Crypto.Hash import keccak

G="https://gamma-api.polymarket.com"; C="https://clob.polymarket.com"
WS="wss://ws-subscriptions-clob.polymarket.com/ws/market"
RPCS=["https://polygon-rpc.com","https://polygon.drpc.org"]
ADAPTER="0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
MAXN=1500; RUN_SECONDS=180; TEST_SIZES=(1,5,10); UA="nr-global-fill-watch/1.0"

def get(url,timeout=20):
    req=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=timeout) as r:return json.loads(r.read().decode())
def rpc(method,params):
    data=json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}).encode();last=None
    for u in RPCS:
        try:
            req=urllib.request.Request(u,data=data,headers={"User-Agent":UA,"Content-Type":"application/json"},method="POST")
            with urllib.request.urlopen(req,timeout=15) as r:x=json.loads(r.read().decode())
            if "result" in x:return x["result"]
            last=x
        except Exception as e:last=repr(e)
    raise RuntimeError(last)
def j(x):
    if isinstance(x,(list,dict)):return x
    if isinstance(x,str):
        try:return json.loads(x)
        except:return []
    return []
def all_markets():
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
    outs=[str(v) for v in j(x.get("outcomes"))];tids=[str(v) for v in j(x.get("clobTokenIds"))]
    if len(outs)!=2 or len(tids)!=2:return None
    mp={o.casefold():t for o,t in zip(outs,tids)}
    if "yes" not in mp or "no" not in mp:return None
    cid=str(x.get("conditionId") or "")
    try:
        info=get(C+"/clob-markets/"+urllib.parse.quote(cid))
        fd=info.get("fd");fee=float(fd.get("r") or 0) if isinstance(fd,dict) else 0
    except: fee=0.07 if x.get("feesEnabled") else 0.0
    return {"q":str(x.get("question") or ""),"slug":str(x.get("slug") or ""),"cid":cid,
            "yes":mp["yes"],"no":mp["no"],"fee":fee,"active":bool(x.get("active")),"closed":bool(x.get("closed"))}
def sell(token,qty,fee):
    b=get(C+"/book?token_id="+urllib.parse.quote(token))
    bids=sorted([(float(v["price"]),float(v["size"])) for v in b.get("bids",[]) if float(v.get("size",0))>0],reverse=True)
    rem=qty;out=0.0
    for p,s in bids:
        t=min(rem,s);out+=t*(p-fee*p*(1-p));rem-=t
        if rem<=1e-12:return out,bids[:3]
    return None,bids[:3]
def conv_fee(mid,selector):
    if not mid:return 0.0
    data="0x"+selector+mid.lower().removeprefix("0x").rjust(64,"0")
    try:return int(rpc("eth_call",[{"to":ADAPTER,"data":data},"latest"]),16)/10000
    except:return None

async def main():
    raw=await asyncio.to_thread(all_markets)
    slugs={}
    for x in raw:
        for e in x.get("events") or []:
            if e.get("negRisk") and not e.get("negRiskAugmented") and e.get("slug"):
                slugs[str(e["slug"])]=1
    evs=[]
    with cf.ThreadPoolExecutor(max_workers=20) as ex:
        fut={ex.submit(get,G+"/events/slug/"+urllib.parse.quote(s)):s for s in slugs}
        for f in cf.as_completed(fut):
            try:
                e=f.result()
                if isinstance(e,dict):evs.append(e)
            except:pass

    k=keccak.new(digest_bits=256);k.update(b"getFeeBips(bytes32)");selector=k.hexdigest()[:8]
    events=[];no_map={}
    for e in evs:
        if not e.get("negRisk") or e.get("negRiskAugmented"):continue
        ms=[]
        for x in e.get("markets") or []:
            m=await asyncio.to_thread(mrec,x)
            if m:ms.append(m)
        if len(ms)<2 or len(ms)!=(len(e.get("markets") or [])) or not all(m["active"] and not m["closed"] for m in ms):continue
        mid=str(e.get("negRiskMarketID") or e.get("negRiskMarketId") or "")
        cfv=await asyncio.to_thread(conv_fee,mid,selector)
        if cfv is None:continue
        ev={"slug":str(e.get("slug") or ""),"title":str(e.get("title") or ""),"mid":mid,"conv_fee":cfv,"markets":ms}
        events.append(ev)
        for idx,m in enumerate(ms):no_map[m["no"]]=(ev,idx,m)

    tokens=list(no_map)
    print("GLOBAL_NR_START",datetime.now(timezone.utc).isoformat(),
          json.dumps({"events":len(events),"no_tokens":len(tokens),
                      "conv_fees":sorted(set(e["conv_fee"] for e in events))},separators=(",",":")),flush=True)
    if not tokens:return
    stats=defaultdict(int); positives=[]; all_sells=[]; frames=0;trade_events=0
    start=time.monotonic();deadline=start+RUN_SECONDS;next_ping=start+10

    # one connection is enough for ~241 NO tokens; split if discovery grows materially
    chunks=[tokens[i:i+250] for i in range(0,len(tokens),250)]
    async def watch(chunk):
        nonlocal frames,trade_events
        async with websockets.connect(WS,ping_interval=None,max_size=8_000_000) as ws:
            await ws.send(json.dumps({"assets_ids":chunk,"type":"market","custom_feature_enabled":True}))
            np=time.monotonic()+10
            while time.monotonic()<deadline:
                now=time.monotonic()
                if now>=np:
                    await ws.send("PING");np=now+10
                try:p=await asyncio.wait_for(ws.recv(),timeout=.5)
                except asyncio.TimeoutError:continue
                if p in ("PING","PONG"):continue
                frames+=1
                try:v=json.loads(p);arr=v if isinstance(v,list) else [v]
                except:continue
                for msg in arr:
                    if not isinstance(msg,dict):continue
                    kind=msg.get("event_type",msg.get("type"));b=msg.get("payload",msg)
                    if kind!="last_trade_price" or not isinstance(b,dict):continue
                    tok=str(b.get("asset_id") or b.get("assetId") or "")
                    if tok not in no_map:continue
                    trade_events+=1
                    side=str(b.get("side") or "").upper()
                    try:px=float(b.get("price"));sz=float(b.get("size") or 0)
                    except:continue
                    ev,idx,m=no_map[tok]
                    stats["no_trade_events"]+=1
                    stats["side_"+side]+=1
                    if side!="SELL":continue
                    stats["sell_no_events"]+=1
                    rec={"utc":datetime.now(timezone.utc).isoformat(),"event":ev["title"],"event_slug":ev["slug"],
                         "maker_market":m["q"],"trade_price":px,"trade_size":sz,"conv_fee":ev["conv_fee"],"sizes":[]}
                    for q in TEST_SIZES:
                        if sz+1e-12<q:continue
                        qout=q*(1-ev["conv_fee"]);comp=0;legs=[];ok=True
                        for j2,o in enumerate(ev["markets"]):
                            if j2==idx:continue
                            try:pro,bids=await asyncio.to_thread(sell,o["yes"],qout,o["fee"])
                            except Exception as ex:
                                ok=False;break
                            if pro is None:ok=False;break
                            comp+=pro;legs.append({"q":o["q"],"proceeds":pro,"top_bids":bids})
                        if ok:
                            prof=comp-q*px
                            row={"qty":q,"cost":q*px,"complement_net":comp,"profit":prof,
                                 "roi_cash":prof/(q*px) if px>0 else None}
                            rec["sizes"].append(row)
                            if prof>0:
                                stats["positive_size_tests"]+=1
                                positives.append({**rec,"positive_row":row})
                    all_sells.append(rec)
                    print("GLOBAL_NR_SELL",json.dumps(rec,separators=(",",":")),flush=True)

    await asyncio.gather(*(watch(c) for c in chunks))
    positives.sort(key=lambda z:z["positive_row"]["profit"],reverse=True)
    print("GLOBAL_NR_SUMMARY",json.dumps({
      "utc_end":datetime.now(timezone.utc).isoformat(),"duration_s":time.monotonic()-start,
      "events":len(events),"no_tokens":len(tokens),"frames":frames,"trade_events":trade_events,
      "stats":dict(stats),"sell_no_records":len(all_sells),"positive_records":len(positives),
      "best_positive":positives[:30],"sample_sells":all_sells[:30]
    },separators=(",",":")),flush=True)

if __name__=="__main__":asyncio.run(main())
