#!/usr/bin/env python3
import concurrent.futures as cf, json, time, urllib.parse, urllib.request
from datetime import datetime, timezone
from Crypto.Hash import keccak

D="https://data-api.polymarket.com/v2"
RPCS=["https://polygon-rpc.com","https://polygon.drpc.org"]
DEC=1_000_000
UA="hist-taker-sell-classifier/1.0"
TARGETS=[
 {"name":"Athletic NO","cid":"0x3e4d20cb627ab0bcfab0e7fb48753765b3436dd121999befd2596572a6d2dc36","token":"647195517645297636552322833889363807010013971390938336792067770402830369875","maker":0.42,"queue":0.0},
 {"name":"Rayo NO","cid":"0xa0996a2e100ea502040335bf116a5ce635fa214d25b8ecd6e8eeb0d1541575de","token":"43772591337014668082527374634748211091796600357150642300932543951704905553674","maker":0.73,"queue":52.5},
 {"name":"Draw NO","cid":"0x5a5587793833810ba3e06d42174c0adfacf00c63037a2a10a487096b8c206eaf","token":"26093081623143772965592281278997914238821034493056490423171592634614361183979","maker":0.61,"queue":10.0},
 {"name":"Fed Jan +25 NO","cid":"0x50642b28be893e8752649e9ae6d687558fc5dcfeeecd10713cf59ef6ea0edce4","token":"63396423986062034636625373404202970920080078602349826866595954726582462497358","maker":0.37,"queue":9265.64},
 {"name":"Fed Jan no-change NO","cid":"0xf6e7c89ec4b5d3c887ed9b70dfbcac9392e287367783ce475d32e8c16ed9f61c","token":"36342864926284644003283595741257595655709757799681943153551982143131732331338","maker":0.63,"queue":1117.58},
 {"name":"Fed Dec +25 NO","cid":"0x8a4196617dcb703e49d9bb36d3847bf6a75fd364dbd19f8d408ac944bf1a03d8","token":"94483965432909797688470702512207472940215538148754820061518518664128018848687","maker":0.08,"queue":36343.05},
]
def get(url):
    req=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=20) as r:return json.loads(r.read().decode())
def rpc(method,params):
    data=json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}).encode();last=None
    for u in RPCS:
        try:
            req=urllib.request.Request(u,data=data,headers={"User-Agent":UA,"Content-Type":"application/json"},method="POST")
            with urllib.request.urlopen(req,timeout=20) as r:x=json.loads(r.read().decode())
            if "result" in x:return x["result"]
            last=x
        except Exception as e:last=repr(e)
    raise RuntimeError(last)
def kt(sig):
    h=keccak.new(digest_bits=256);h.update(sig.encode());return "0x"+h.hexdigest()
OM1=kt("OrdersMatched(bytes32,address,uint256,uint256,uint256,uint256)").lower()
OM2=kt("OrdersMatched(bytes32,address,uint8,uint256,uint256,uint256)").lower()
OF1=kt("OrderFilled(bytes32,address,address,uint256,uint256,uint256,uint256,uint256)").lower()
OF2=kt("OrderFilled(bytes32,address,address,uint8,uint256,uint256,uint256,uint256,bytes32,bytes32)").lower()
def words(data):
    h=str(data).removeprefix("0x")
    return [int(h[i:i+64],16) for i in range(0,len(h),64) if len(h[i:i+64])==64]
def classify(rec,target):
    ti=int(target); explicit=[]; fallback=[]
    for lg in rec.get("logs") or []:
        topics=[str(x).lower() for x in lg.get("topics") or []]
        if not topics:continue
        w=words(lg.get("data","0x")); t0=topics[0]
        if t0==OM1 and len(w)>=4:
            ma,ta,mf,tf=w[:4]
            if ma==ti and ta==0: explicit.append(("SELL",mf/DEC,tf/mf if mf else None,"OM1"))
            elif ma==0 and ta==ti: explicit.append(("BUY",tf/DEC,mf/tf if tf else None,"OM1"))
        elif t0==OM2 and len(w)>=4:
            si,tok,mf,tf=w[:4]
            if tok==ti:
                side="SELL" if si==1 else "BUY"
                explicit.append((side,(mf if side=="SELL" else tf)/DEC,
                    tf/mf if side=="SELL" and mf else mf/tf if side=="BUY" and tf else None,"OM2"))
        elif t0==OF1 and len(w)>=5:
            ma,ta,mf,tf,_=w[:5]
            if ma==0 and ta==ti: fallback.append(("SELL",tf/DEC,mf/tf if tf else None,"OF1"))
            elif ma==ti and ta==0: fallback.append(("BUY",mf/DEC,tf/mf if mf else None,"OF1"))
        elif t0==OF2 and len(w)>=5:
            si,tok,mf,tf,_=w[:5]
            if tok==ti:
                maker_side="SELL" if si==1 else "BUY"; aggr="BUY" if maker_side=="SELL" else "SELL"
                fallback.append((aggr,(mf if maker_side=="SELL" else tf)/DEC,
                    tf/mf if maker_side=="SELL" and mf else mf/tf if maker_side=="BUY" and tf else None,"OF2"))
    return explicit if explicit else fallback
def fetch_all(cid,cap=10000):
    rows=[];cursor=None
    while len(rows)<cap:
        q={"condition":cid,"limit":min(1000,cap-len(rows))}
        if cursor:q["cursor"]=cursor
        x=get(D+"/trades?"+urllib.parse.urlencode(q)); data=x.get("data") or []
        rows.extend(data); cursor=(x.get("pagination") or {}).get("next_cursor")
        if not cursor or not data:break
    return rows

now=time.time()
for t in TARGETS:
    try:rows=fetch_all(t["cid"])
    except Exception as e:
        print("HIST_ERR",t["name"],repr(e),flush=True);continue
    cand={}
    for x in rows:
        tok=str(x.get("token_id") or x.get("asset") or "")
        outcome=str(x.get("outcome") or "")
        side=str(x.get("side") or "").upper()
        try:px=float(x.get("price")); sz=float(x.get("size") or 0); ts=float(x.get("timestamp"))
        except:continue
        if tok!=t["token"] and outcome.casefold()!="no":continue
        if side!="SELL" or px>t["maker"]+1e-12:continue
        tx=str(x.get("transaction_hash") or x.get("transactionHash") or "")
        if tx:cand[tx]={"tx":tx,"participant_price":px,"participant_size":sz,"ts":ts}
    verified=[]
    with cf.ThreadPoolExecutor(max_workers=16) as ex:
        fut={ex.submit(rpc,"eth_getTransactionReceipt",[tx]):tx for tx in cand}
        for f in cf.as_completed(fut):
            tx=fut[f]
            try:rec=f.result()
            except:continue
            for side,qty,px,src in classify(rec,t["token"]):
                if side=="SELL" and px is not None and px<=t["maker"]+1e-12:
                    verified.append({**cand[tx],"chain_side":side,"chain_qty":qty,"chain_price":px,"source":src})
    horizons={}
    for label,secs in [("1h",3600),("6h",21600),("24h",86400),("7d",604800),("30d",2592000)]:
        z=[x for x in verified if now-x["ts"]<=secs]
        shares=sum(x["chain_qty"] for x in z)
        rate=shares/(secs/3600)
        horizons[label]={"events":len(z),"shares":shares,"shares_per_hour":rate,
            "queue_hours_at_hist_rate":(t["queue"]/rate) if rate>0 and t["queue"]>0 else (0 if t["queue"]==0 else None)}
    verified.sort(key=lambda x:x["ts"],reverse=True)
    print("HIST_TAKER_SUMMARY",json.dumps({
      "utc":datetime.now(timezone.utc).isoformat(),"name":t["name"],"maker":t["maker"],"queue":t["queue"],
      "api_rows":len(rows),"participant_candidate_txs":len(cand),"verified_taker_sell_events":len(verified),
      "horizons":horizons,"recent":verified[:20]},separators=(",",":")),flush=True)
