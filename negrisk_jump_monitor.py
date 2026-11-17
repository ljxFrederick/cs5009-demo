#!/usr/bin/env python3
import concurrent.futures as cf, json, time, urllib.parse, urllib.request
from collections import defaultdict
from datetime import datetime,timezone
from Crypto.Hash import keccak

G="https://gamma-api.polymarket.com"; C="https://clob.polymarket.com"
RPCS=["https://polygon-rpc.com","https://polygon.drpc.org"]
ADAPTER="0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
MAXN=1500; RUN=75; STEP=5; QTY=1.0; UA="nr-jump-monitor/1.0"

def req(url,method="GET",body=None,timeout=25):
    data=None;h={"User-Agent":UA,"Accept":"application/json"}
    if body is not None:data=json.dumps(body).encode();h["Content-Type"]="application/json"
    r=urllib.request.Request(url,data=data,headers=h,method=method)
    with urllib.request.urlopen(r,timeout=timeout) as x:return json.loads(x.read().decode())
def rpc(method,params):
    data=json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}).encode();last=None
    for u in RPCS:
        try:
            r=urllib.request.Request(u,data=data,headers={"User-Agent":UA,"Content-Type":"application/json"},method="POST")
            with urllib.request.urlopen(r,timeout=20) as x:z=json.loads(x.read().decode())
            if "result" in z:return z["result"]
            last=z
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
        n=min(100,MAXN-len(out));q=urllib.parse.urlencode({"active":"true","closed":"false","limit":n,"offset":off,"order":"volumeNum","ascending":"false"})
        a=req(f"{G}/markets?{q}")
        if not a:break
        out.extend(a);off+=len(a)
        if len(a)<n:break
    return out
def event(slug):return req(f"{G}/events/slug/{urllib.parse.quote(slug)}")
def mrec(x):
    if str(x.get("version") or "").lower()!="v1":return None
    outs=[str(v) for v in j(x.get("outcomes"))];tids=[str(v) for v in j(x.get("clobTokenIds"))]
    if len(outs)!=2 or len(tids)!=2:return None
    mp={o.casefold():t for o,t in zip(outs,tids)}
    if "yes" not in mp or "no" not in mp:return None
    cid=str(x.get("conditionId") or "")
    try:
        z=req(C+"/clob-markets/"+urllib.parse.quote(cid));fd=z.get("fd")
        fee=float(fd.get("r") or 0) if isinstance(fd,dict) else 0.0
        tick=float(z.get("minimum_tick_size") or z.get("min_tick_size") or z.get("tick_size") or x.get("orderPriceMinTickSize") or .01)
    except:
        fee=.07 if x.get("feesEnabled") else 0.0; tick=float(x.get("orderPriceMinTickSize") or .01)
    return {"q":str(x.get("question") or ""),"slug":str(x.get("slug") or ""),"cid":cid,
      "yes":mp["yes"],"no":mp["no"],"fee":fee,"tick":tick,"active":bool(x.get("active")),"closed":bool(x.get("closed"))}
def fee_selector():
    h=keccak.new(digest_bits=256);h.update(b"getFeeBips(bytes32)");return "0x"+h.hexdigest()[:8]
SEL=fee_selector()
def conv_fee(mid):
    if not mid:return None
    try:return int(rpc("eth_call",[{"to":ADAPTER,"data":SEL+mid.lower().removeprefix("0x").rjust(64,"0")},"latest"]),16)/10000
    except:return None
def fetch_books(tokens):
    out={}
    for i in range(0,len(tokens),100):
        chunk=tokens[i:i+100]
        a=req(C+"/books","POST",[{"token_id":t} for t in chunk])
        for x in a:
            tok=str(x.get("asset_id") or "")
            bids=sorted([(float(v["price"]),float(v["size"])) for v in x.get("bids",[]) if float(v.get("size",0))>0],reverse=True)
            asks=sorted([(float(v["price"]),float(v["size"])) for v in x.get("asks",[]) if float(v.get("size",0))>0])
            out[tok]=(bids,asks)
    return out
def sell(bids,qty,r):
    rem=qty;out=0
    for p,s in bids:
        t=min(rem,s);out+=t*(p-r*p*(1-p));rem-=t
        if rem<=1e-12:return out
    return None

raw=all_markets();slugs={}
for x in raw:
    for e in x.get("events") or []:
        if e.get("negRisk") and not e.get("negRiskAugmented") and e.get("slug"):slugs[str(e["slug"])]=1
evs=[]
with cf.ThreadPoolExecutor(max_workers=20) as ex:
    fut=[ex.submit(event,s) for s in slugs]
    for f in cf.as_completed(fut):
        try:
            e=f.result()
            if isinstance(e,dict):evs.append(e)
        except:pass
events=[]
for e in evs:
    xs=e.get("markets") or [];ms=[m for x in xs if (m:=mrec(x))]
    if len(ms)<2 or len(ms)!=len(xs) or not all(m["active"] and not m["closed"] for m in ms):continue
    mid=str(e.get("negRiskMarketID") or e.get("negRiskMarketId") or "")
    cfv=conv_fee(mid)
    if cfv is None:continue
    events.append({"title":str(e.get("title") or ""),"slug":str(e.get("slug") or ""),"fee":cfv,"markets":ms})
tokens=sorted({t for e in events for m in e["markets"] for t in (m["yes"],m["no"])})
print("JMON_START",datetime.now(timezone.utc).isoformat(),json.dumps({"events":len(events),"markets":sum(len(e["markets"]) for e in events),"tokens":len(tokens),"run":RUN,"step":STEP},separators=(",",":")),flush=True)

samples=[];ever={}
end=time.monotonic()+RUN;seq=0
while time.monotonic()<end:
    t0=time.monotonic()
    try:bks=fetch_books(tokens)
    except Exception as ex:
        print("JMON_BOOK_ERR",repr(ex),flush=True);time.sleep(STEP);continue
    join=[];jump=[]
    for e in events:
        ms=e["markets"];out_qty=QTY*(1-e["fee"])
        yes_pro={}
        for m in ms:
            b=bks.get(m["yes"])
            yes_pro[m["slug"]]=None if not b else sell(b[0],out_qty,m["fee"])
        for m in ms:
            nb=bks.get(m["no"])
            if not nb or not nb[0] or not nb[1]:continue
            comp=0;ok=True
            for o in ms:
                if o is m:continue
                p=yes_pro.get(o["slug"])
                if p is None:ok=False;break
                comp+=p
            if not ok:continue
            bid=nb[0][0][0];queue=nb[0][0][1];ask=nb[1][0][0]
            jedge=comp-bid
            if jedge>0:
                join.append({"event":e["title"],"market":m["q"],"bid":bid,"ask":ask,"queue":queue,"tick":m["tick"],"comp":comp,"edge":jedge})
            post=round(bid+m["tick"],10)
            if post < ask-1e-12:
                edge=comp-post
                if edge>0:
                    z={"event":e["title"],"market":m["q"],"bid":bid,"ask":ask,"queue_at_old_bid":queue,
                       "post":post,"tick":m["tick"],"comp":comp,"edge":edge,"roi_cash":edge/post if post else None}
                    jump.append(z);ever[(e["slug"],m["slug"])]=z
    jump.sort(key=lambda x:x["edge"],reverse=True);join.sort(key=lambda x:x["edge"],reverse=True)
    row={"seq":seq,"utc":datetime.now(timezone.utc).isoformat(),"join_positive":len(join),"jump_positive":len(jump),"top_jump":jump[:10],"top_join":join[:10]}
    samples.append(row);print("JMON_SAMPLE",json.dumps(row,separators=(",",":")),flush=True)
    seq+=1
    dt=time.monotonic()-t0
    time.sleep(max(0,STEP-dt))
print("JMON_SUMMARY",json.dumps({"utc_end":datetime.now(timezone.utc).isoformat(),"samples":len(samples),
  "jump_counts":[x["jump_positive"] for x in samples],"join_counts":[x["join_positive"] for x in samples],
  "ever_jump_unique":len(ever),"ever_jump":list(ever.values()),"samples_data":samples},separators=(",",":")),flush=True)
