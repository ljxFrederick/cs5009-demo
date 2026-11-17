#!/usr/bin/env python3
import asyncio, concurrent.futures as cf, json, time, urllib.parse, urllib.request
from datetime import datetime, timezone
from Crypto.Hash import keccak
import websockets

G="https://gamma-api.polymarket.com"
C="https://clob.polymarket.com"
RTDS="wss://ws-live-data.polymarket.com/"
RPCS=["https://polygon-rpc.com","https://polygon.drpc.org"]
ADAPTER="0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
MAXN=1500
RUN_SECONDS=45
TEST_SIZES=(1,5,10,25)
UA="global-negrisk-rtds-receipt-watch/1.0"
DEC=1_000_000

def get(url, timeout=20):
    req=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=timeout) as r:
        return json.loads(r.read().decode())

def rpc(method,params,timeout=20):
    body=json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}).encode()
    last=None
    for url in RPCS:
        try:
            req=urllib.request.Request(url,data=body,headers={"User-Agent":UA,"Content-Type":"application/json"},method="POST")
            with urllib.request.urlopen(req,timeout=timeout) as r:
                x=json.loads(r.read().decode())
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

def ktopic(sig):
    h=keccak.new(digest_bits=256);h.update(sig.encode());return "0x"+h.hexdigest()

TOPIC_V1=ktopic("OrdersMatched(bytes32,address,uint256,uint256,uint256,uint256)")
TOPIC_V2=ktopic("OrdersMatched(bytes32,address,uint8,uint256,uint256,uint256)")
SEL_FEE=ktopic("getFeeBips(bytes32)")[:10]

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
    if len(outs)!=2 or len(tids)!=2:return None
    mp={o.casefold():t for o,t in zip(outs,tids)}
    if "yes" not in mp or "no" not in mp:return None
    cid=str(x.get("conditionId") or "")
    try:
        info=get(C+"/clob-markets/"+urllib.parse.quote(cid))
        fd=info.get("fd");fee=float(fd.get("r") or 0) if isinstance(fd,dict) else 0.0
    except:
        fee=0.07 if x.get("feesEnabled") else 0.0
    return {"q":str(x.get("question") or ""),"slug":str(x.get("slug") or ""),
            "cid":cid,"yes":mp["yes"],"no":mp["no"],"fee":fee,
            "active":bool(x.get("active")),"closed":bool(x.get("closed")),"version":"v1"}

def conversion_fee(mid):
    if not mid:return None
    data=SEL_FEE+mid.lower().removeprefix("0x").rjust(64,"0")
    try:return int(rpc("eth_call",[{"to":ADAPTER,"data":data},"latest"]),16)/10000
    except:return None

def sell_yes(token,qty,fee):
    x=get(C+"/book?token_id="+urllib.parse.quote(token))
    bids=sorted([(float(v["price"]),float(v["size"])) for v in x.get("bids",[]) if float(v.get("size",0))>0],reverse=True)
    rem=qty;out=0.0
    for p,s in bids:
        take=min(rem,s);out+=take*(p-fee*p*(1-p));rem-=take
        if rem<=1e-12:return out,bids[:4]
    return None,bids[:4]

def words(data):
    h=data.lower().removeprefix("0x")
    return [int(h[i:i+64],16) for i in range(0,len(h),64) if len(h[i:i+64])==64]

def parse_taker_matches(receipt,target_tokens):
    out=[]
    for lg in receipt.get("logs") or []:
        topics=[str(x).lower() for x in lg.get("topics") or []]
        if not topics:continue
        t0=topics[0]
        w=words(str(lg.get("data") or "0x"))
        if t0==TOPIC_V1.lower() and len(w)>=4:
            maker_asset,taker_asset,maker_amt,taker_amt=w[:4]
            tok=None;side=None;qty_raw=0;price=None
            if maker_asset in target_tokens and taker_asset==0:
                tok=maker_asset;side="SELL";qty_raw=maker_amt
                price=(taker_amt/maker_amt) if maker_amt else None
            elif maker_asset==0 and taker_asset in target_tokens:
                tok=taker_asset;side="BUY";qty_raw=taker_amt
                price=(maker_amt/taker_amt) if taker_amt else None
            if tok is not None:
                out.append({"abi":"v1","token":str(tok),"side":side,"qty":qty_raw/DEC,
                            "price":price,"raw":[maker_asset,taker_asset,maker_amt,taker_amt],
                            "address":lg.get("address")})
        elif t0==TOPIC_V2.lower() and len(w)>=4:
            side_i,tok,maker_amt,taker_amt=w[:4]
            if tok not in target_tokens:continue
            side="SELL" if side_i==1 else "BUY"
            if side=="SELL":
                qty_raw=maker_amt;price=(taker_amt/maker_amt) if maker_amt else None
            else:
                qty_raw=taker_amt;price=(maker_amt/taker_amt) if taker_amt else None
            out.append({"abi":"v2","token":str(tok),"side":side,"qty":qty_raw/DEC,
                        "price":price,"raw":[side_i,tok,maker_amt,taker_amt],
                        "address":lg.get("address")})
    return out

async def build_universe():
    raw=await asyncio.to_thread(list_markets)
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
    events=[];no_map={}
    for e in evs:
        xs=e.get("markets") or []
        ms=[m for x in xs if (m:=mrec(x))]
        if len(ms)<2 or len(ms)!=len(xs):continue
        if not all(m["active"] and not m["closed"] for m in ms):continue
        mid=str(e.get("negRiskMarketID") or e.get("negRiskMarketId") or "")
        cfv=await asyncio.to_thread(conversion_fee,mid)
        if cfv is None:continue
        ev={"slug":str(e.get("slug") or ""),"title":str(e.get("title") or ""),
            "mid":mid,"conv_fee":cfv,"markets":ms}
        events.append(ev)
        for idx,m in enumerate(ms):
            no_map[m["no"]]={"event":ev,"idx":idx,"market":m}
    return raw,events,no_map

async def hedge(ev,idx,qty):
    out_qty=qty*(1-ev["conv_fee"])
    legs=[];total=0.0
    tasks=[]
    for j2,m in enumerate(ev["markets"]):
        if j2==idx:continue
        tasks.append((m,asyncio.to_thread(sell_yes,m["yes"],out_qty,m["fee"])))
    for m,t in tasks:
        try:pro,bids=await t
        except Exception as ex:return None,{"error":repr(ex)}
        if pro is None:return None,{"error":"insufficient_depth","q":m["q"],"bids":bids}
        total+=pro;legs.append({"q":m["q"],"proceeds":pro,"top_bids":bids})
    return total,legs

async def main():
    raw,events,no_map=await build_universe()
    target_ints={int(t) for t in no_map}
    print("GLOBAL_RTDS_START",datetime.now(timezone.utc).isoformat(),
          json.dumps({"active_markets":len(raw),"events":len(events),"no_tokens":len(no_map),
                      "conversion_fees":sorted(set(e["conv_fee"] for e in events)),
                      "topic_v1":TOPIC_V1,"topic_v2":TOPIC_V2},separators=(",",":")),flush=True)
    if not no_map:return

    seen_tx=set();rtds_events=0;target_activity=0;receipts=0
    taker_sell_matches=0;taker_buy_matches=0;positive_tests=[];matched_records=[]
    start=time.monotonic();deadline=start+RUN_SECONDS;next_ping=start+4

    async with websockets.connect(RTDS,ping_interval=None,max_size=12_000_000) as ws:
        await ws.send(json.dumps({"action":"subscribe","subscriptions":[
            {"topic":"activity","type":"trades","filters":""}
        ]}))
        while time.monotonic()<deadline:
            now=time.monotonic()
            if now>=next_ping:
                await ws.send("PING");next_ping=now+4
            try:p=await asyncio.wait_for(ws.recv(),timeout=.5)
            except asyncio.TimeoutError:continue
            if p in ("PING","PONG"):continue
            try:v=json.loads(p)
            except:continue
            arr=v if isinstance(v,list) else [v]
            for msg in arr:
                if not isinstance(msg,dict) or msg.get("topic")!="activity" or msg.get("type")!="trades":continue
                b=msg.get("payload")
                if not isinstance(b,dict):continue
                rtds_events+=1
                tok=str(b.get("asset") or "")
                if tok not in no_map:continue
                target_activity+=1
                tx=str(b.get("transactionHash") or "")
                if not tx or tx in seen_tx:continue
                seen_tx.add(tx)
                arrival_ms=time.time()*1000
                try:rec=await asyncio.to_thread(rpc,"eth_getTransactionReceipt",[tx])
                except Exception as ex:
                    print("GLOBAL_RTDS_RECEIPT_ERR",tx,repr(ex),flush=True);continue
                if not rec:continue
                receipts+=1
                matches=parse_taker_matches(rec,target_ints)
                for mt in matches:
                    if mt["token"] not in no_map:continue
                    info=no_map[mt["token"]];ev=info["event"];idx=info["idx"];m=info["market"]
                    if mt["side"]=="SELL":taker_sell_matches+=1
                    else:taker_buy_matches+=1
                    row={"tx":tx,"event":ev["title"],"event_slug":ev["slug"],"market":m["q"],
                         "match":mt,"rtds_outer_ts":msg.get("timestamp"),"rtds_payload":{
                           "side":b.get("side"),"price":b.get("price"),"size":b.get("size"),
                           "outcome":b.get("outcome"),"asset":b.get("asset")},
                         "arrival_lag_ms":None if msg.get("timestamp") is None else arrival_ms-float(msg.get("timestamp")),
                         "tests":[]}
                    if mt["side"]=="SELL" and mt["price"] is not None:
                        for q in TEST_SIZES:
                            if mt["qty"]+1e-9<q:continue
                            t0=time.perf_counter()
                            comp,legs=await hedge(ev,idx,q)
                            hedge_ms=(time.perf_counter()-t0)*1000
                            if comp is None:
                                row["tests"].append({"qty":q,"hedge_error":legs,"hedge_query_ms":hedge_ms});continue
                            cost=q*mt["price"];profit=comp-cost
                            z={"qty":q,"maker_cost":cost,"complement_net":comp,"profit_before_gas":profit,
                               "roi_cash":profit/cost if cost>0 else None,"hedge_query_ms":hedge_ms,"legs":legs}
                            row["tests"].append(z)
                            if profit>0:positive_tests.append({**row,"positive_test":z})
                    matched_records.append(row)
                    print("GLOBAL_RTDS_MATCH",json.dumps(row,separators=(",",":"),ensure_ascii=False),flush=True)

    positive_tests.sort(key=lambda z:z["positive_test"]["profit_before_gas"],reverse=True)
    print("GLOBAL_RTDS_SUMMARY",json.dumps({
      "utc_end":datetime.now(timezone.utc).isoformat(),"duration_s":time.monotonic()-start,
      "events":len(events),"no_tokens":len(no_map),"rtds_trade_events":rtds_events,
      "target_no_activity_rows":target_activity,"unique_target_txs":len(seen_tx),"receipts":receipts,
      "taker_sell_matches":taker_sell_matches,"taker_buy_matches":taker_buy_matches,
      "matched_records":len(matched_records),"positive_tests":len(positive_tests),
      "best_positive":positive_tests[:30],"sample_matches":matched_records[:30]
    },separators=(",",":"),ensure_ascii=False),flush=True)

if __name__=="__main__":
    asyncio.run(main())
