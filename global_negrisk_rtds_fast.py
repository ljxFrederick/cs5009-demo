#!/usr/bin/env python3
import asyncio, concurrent.futures as cf, json, time, urllib.parse, urllib.request
from datetime import datetime, timezone
from Crypto.Hash import keccak
import websockets

G="https://gamma-api.polymarket.com"; C="https://clob.polymarket.com"
RTDS="wss://ws-live-data.polymarket.com/"
RPCS=["https://polygon-rpc.com","https://polygon.drpc.org"]
ADAPTER="0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
MAXN=1500; RUN_SECONDS=90; TEST_SIZES=(1,5,10,25); DEC=1_000_000
UA="nr-rtds-fast/1.0"

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
def kt(sig):
    h=keccak.new(digest_bits=256);h.update(sig.encode());return "0x"+h.hexdigest()
OM1=kt("OrdersMatched(bytes32,address,uint256,uint256,uint256,uint256)").lower()
OM2=kt("OrdersMatched(bytes32,address,uint8,uint256,uint256,uint256)").lower()
OF1=kt("OrderFilled(bytes32,address,address,uint256,uint256,uint256,uint256,uint256)").lower()
OF2=kt("OrderFilled(bytes32,address,address,uint8,uint256,uint256,uint256,uint256,bytes32,bytes32)").lower()
SEL=kt("getFeeBips(bytes32)")[:10]
def words(data):
    h=str(data).removeprefix("0x")
    return [int(h[i:i+64],16) for i in range(0,len(h),64) if len(h[i:i+64])==64]

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
    if str(x.get("version") or "").lower()!="v1":return None
    outs=[str(v) for v in j(x.get("outcomes"))];tids=[str(v) for v in j(x.get("clobTokenIds"))]
    mp={o.casefold():t for o,t in zip(outs,tids)}
    if len(outs)!=2 or "yes" not in mp or "no" not in mp:return None
    fs=x.get("feeSchedule") or {}
    fr=None
    if isinstance(fs,dict) and fs.get("rate") is not None:
        try:fr=float(fs["rate"])
        except:pass
    return {"q":str(x.get("question") or ""),"slug":str(x.get("slug") or ""),"cid":str(x.get("conditionId") or ""),
            "yes":mp["yes"],"no":mp["no"],"fee":fr,"fees":bool(x.get("feesEnabled")),
            "active":bool(x.get("active")),"closed":bool(x.get("closed"))}
def conv_fee(mid):
    if not mid:return None
    try:return int(rpc("eth_call",[{"to":ADAPTER,"data":SEL+mid.lower().removeprefix("0x").rjust(64,"0")},"latest"]),16)/10000
    except:return None
def market_fee(m):
    if m["fee"] is not None:return m["fee"]
    try:
        x=get(C+"/clob-markets/"+urllib.parse.quote(m["cid"]));fd=x.get("fd")
        return float(fd.get("r") or 0) if isinstance(fd,dict) else 0.0
    except:return 0.07 if m["fees"] else 0.0
def sell_yes(m,qty):
    x=get(C+"/book?token_id="+urllib.parse.quote(m["yes"]))
    bids=sorted([(float(v["price"]),float(v["size"])) for v in x.get("bids",[]) if float(v.get("size",0))>0],reverse=True)
    r=market_fee(m);rem=qty;out=0.0
    for p,s in bids:
        t=min(rem,s);out+=t*(p-r*p*(1-p));rem-=t
        if rem<=1e-12:return out,bids[:4],r
    return None,bids[:4],r

def classify(receipt,target_ints):
    orders=[];fills=[]
    for lg in receipt.get("logs") or []:
        ts=[str(x).lower() for x in lg.get("topics") or []]
        if not ts:continue
        w=words(lg.get("data","0x"));t0=ts[0]
        if t0==OM1 and len(w)>=4:
            ma,ta,mf,tf=w[:4]
            if ma in target_ints and ta==0:
                orders.append({"source":"OrdersMatchedV1","side":"SELL","token":ma,"qty":mf/DEC,"price":tf/mf if mf else None})
            elif ma==0 and ta in target_ints:
                orders.append({"source":"OrdersMatchedV1","side":"BUY","token":ta,"qty":tf/DEC,"price":mf/tf if tf else None})
        elif t0==OM2 and len(w)>=4:
            si,tok,mf,tf=w[:4]
            if tok in target_ints:
                side="SELL" if si==1 else "BUY"
                orders.append({"source":"OrdersMatchedV2","side":side,"token":tok,
                  "qty":(mf if side=="SELL" else tf)/DEC,
                  "price":(tf/mf if side=="SELL" and mf else mf/tf if side=="BUY" and tf else None)})
        elif t0==OF1 and len(w)>=5:
            ma,ta,mf,tf,fee=w[:5]
            if ma==0 and ta in target_ints: # resting maker BUY; aggressor SELL
                fills.append({"source":"OrderFilledV1Fallback","side":"SELL","token":ta,"qty":tf/DEC,"price":mf/tf if tf else None})
            elif ma in target_ints and ta==0: # resting maker SELL; aggressor BUY
                fills.append({"source":"OrderFilledV1Fallback","side":"BUY","token":ma,"qty":mf/DEC,"price":tf/mf if mf else None})
        elif t0==OF2 and len(w)>=5:
            si,tok,mf,tf,fee=w[:5]
            if tok in target_ints:
                maker_side="SELL" if si==1 else "BUY";aggr="BUY" if maker_side=="SELL" else "SELL"
                fills.append({"source":"OrderFilledV2Fallback","side":aggr,"token":tok,
                  "qty":(mf if maker_side=="SELL" else tf)/DEC,
                  "price":(tf/mf if maker_side=="SELL" and mf else mf/tf if maker_side=="BUY" and tf else None)})
    # OrdersMatched explicitly identifies taker; if present for a token, ignore OrderFilled fallback for that token.
    explicit={x["token"] for x in orders}
    return orders+[x for x in fills if x["token"] not in explicit]

async def build():
    raw=await asyncio.to_thread(list_markets);slugs={}
    for x in raw:
        for e in x.get("events") or []:
            if e.get("negRisk") and not e.get("negRiskAugmented") and e.get("slug"):slugs[str(e["slug"])]=1
    evs=[]
    with cf.ThreadPoolExecutor(max_workers=24) as ex:
        fut={ex.submit(get,G+"/events/slug/"+urllib.parse.quote(s)):s for s in slugs}
        for f in cf.as_completed(fut):
            try:
                e=f.result()
                if isinstance(e,dict):evs.append(e)
            except:pass
    temp=[]
    for e in evs:
        xs=e.get("markets") or [];ms=[m for x in xs if (m:=mrec(x))]
        if len(ms)>=2 and len(ms)==len(xs) and all(m["active"] and not m["closed"] for m in ms):
            mid=str(e.get("negRiskMarketID") or e.get("negRiskMarketId") or "")
            temp.append((e,ms,mid))
    mids={}
    with cf.ThreadPoolExecutor(max_workers=20) as ex:
        fut={ex.submit(conv_fee,mid):mid for e,ms,mid in temp if mid}
        for f in cf.as_completed(fut):
            try:mids[fut[f]]=f.result()
            except:pass
    events=[];no_map={}
    for e,ms,mid in temp:
        fee=mids.get(mid)
        if fee is None:continue
        ev={"title":str(e.get("title") or ""),"slug":str(e.get("slug") or ""),"conv_fee":fee,"markets":ms}
        events.append(ev)
        for idx,m in enumerate(ms):no_map[m["no"]]={"event":ev,"idx":idx,"market":m}
    return raw,events,no_map

async def hedge(ev,idx,qty):
    oq=qty*(1-ev["conv_fee"]);pairs=[(m,asyncio.to_thread(sell_yes,m,oq)) for j2,m in enumerate(ev["markets"]) if j2!=idx]
    total=0;legs=[]
    for m,t in pairs:
        try:pro,bids,r=await t
        except Exception as ex:return None,{"error":repr(ex)}
        if pro is None:return None,{"error":"depth","q":m["q"],"bids":bids}
        total+=pro;legs.append({"q":m["q"],"proceeds":pro,"fee":r,"bids":bids})
    return total,legs

async def main():
    tbuild=time.perf_counter();raw,events,no_map=await build();build_s=time.perf_counter()-tbuild
    target_ints={int(x) for x in no_map}
    print("FAST_NR_START",datetime.now(timezone.utc).isoformat(),json.dumps({
      "build_s":build_s,"active":len(raw),"events":len(events),"no_tokens":len(no_map),
      "conv_fees":sorted(set(e["conv_fee"] for e in events))},separators=(",",":")),flush=True)
    seen=set();activity=0;target_rows=0;classified=0;tsell=0;positive=[];records=[]
    start=time.monotonic();deadline=start+RUN_SECONDS;np=start+4
    async with websockets.connect(RTDS,ping_interval=None,max_size=12_000_000) as ws:
        await ws.send(json.dumps({"action":"subscribe","subscriptions":[{"topic":"activity","type":"trades","filters":""}]}))
        while time.monotonic()<deadline:
            now=time.monotonic()
            if now>=np:await ws.send("PING");np=now+4
            try:p=await asyncio.wait_for(ws.recv(),timeout=.5)
            except asyncio.TimeoutError:continue
            if p in ("PING","PONG"):continue
            try:v=json.loads(p)
            except:continue
            for msg in (v if isinstance(v,list) else [v]):
                if not isinstance(msg,dict) or msg.get("topic")!="activity" or msg.get("type")!="trades":continue
                b=msg.get("payload")
                if not isinstance(b,dict):continue
                activity+=1;tok=str(b.get("asset") or "")
                if tok not in no_map:continue
                target_rows+=1;tx=str(b.get("transactionHash") or "")
                if not tx or tx in seen:continue
                seen.add(tx)
                print("FAST_TARGET_ACTIVITY",json.dumps({"tx":tx,"asset":tok,"side":b.get("side"),"price":b.get("price"),
                      "size":b.get("size"),"event":b.get("eventSlug"),"market":b.get("slug")},separators=(",",":")),flush=True)
                try:rec=await asyncio.to_thread(rpc,"eth_getTransactionReceipt",[tx])
                except Exception as ex:continue
                if not rec:continue
                matches=classify(rec,target_ints)
                for mt in matches:
                    s_tok=str(mt["token"])
                    if s_tok not in no_map:continue
                    classified+=1
                    info=no_map[s_tok];ev=info["event"];idx=info["idx"];m=info["market"]
                    row={"tx":tx,"event":ev["title"],"market":m["q"],"match":mt,"tests":[]}
                    if mt["side"]=="SELL" and mt["price"] is not None:
                        tsell+=1
                        for q in TEST_SIZES:
                            if mt["qty"]+1e-9<q:continue
                            t0=time.perf_counter();comp,legs=await hedge(ev,idx,q);hm=(time.perf_counter()-t0)*1000
                            if comp is None:row["tests"].append({"qty":q,"error":legs,"hedge_ms":hm});continue
                            cost=q*mt["price"];prof=comp-cost
                            z={"qty":q,"cost":cost,"complement":comp,"profit_before_gas":prof,
                               "roi_cash":prof/cost if cost else None,"hedge_ms":hm,"legs":legs}
                            row["tests"].append(z)
                            if prof>0:positive.append({**row,"positive_test":z})
                    records.append(row)
                    print("FAST_CLASSIFIED",json.dumps(row,separators=(",",":"),ensure_ascii=False),flush=True)
    positive.sort(key=lambda x:x["positive_test"]["profit_before_gas"],reverse=True)
    print("FAST_NR_SUMMARY",json.dumps({"utc":datetime.now(timezone.utc).isoformat(),"build_s":build_s,
      "duration_s":time.monotonic()-start,"activity":activity,"target_rows":target_rows,"unique_txs":len(seen),
      "classified":classified,"taker_sell":tsell,"positive_tests":len(positive),
      "best":positive[:20],"records":records[:30]},separators=(",",":"),ensure_ascii=False),flush=True)

if __name__=="__main__":asyncio.run(main())
