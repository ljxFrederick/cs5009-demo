#!/usr/bin/env python3
import concurrent.futures as cf,json,math,urllib.parse,urllib.request
from datetime import datetime,timezone

G="https://gamma-api.polymarket.com";C="https://clob.polymarket.com";UA="global-binary-maker-lock/1.0"
MAXN=1500;SIZES=(1,5,10,25);BUF=0.0005

def get(url,timeout=20):
    req=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=timeout) as r:return json.loads(r.read().decode())
def j(x):
    if isinstance(x,(list,dict)):return x
    if isinstance(x,str):
        try:return json.loads(x)
        except:return []
    return []
def markets():
    out=[];off=0
    while len(out)<MAXN:
        n=min(100,MAXN-len(out))
        q=urllib.parse.urlencode({"active":"true","closed":"false","limit":n,"offset":off,"order":"volume24hr","ascending":"false"})
        a=get(G+"/markets?"+q)
        if not a:break
        out.extend(a);off+=len(a)
        if len(a)<n:break
    rr=[]
    for x in out:
        outs=[str(v) for v in j(x.get("outcomes"))];tids=[str(v) for v in j(x.get("clobTokenIds"))]
        if len(outs)!=2 or len(tids)!=2:continue
        mp={o.casefold():t for o,t in zip(outs,tids)}
        if "yes" not in mp or "no" not in mp:continue
        rr.append({"q":str(x.get("question") or ""),"slug":str(x.get("slug") or ""),
          "cid":str(x.get("conditionId") or ""),"yes":mp["yes"],"no":mp["no"],
          "fees":bool(x.get("feesEnabled")),"v24":float(x.get("volume24hr") or 0),
          "liq":float(x.get("liquidityNum") or x.get("liquidity") or 0),
          "end":str(x.get("endDate") or "")})
    return rr
def fetch(m):
    def b(t):
        x=get(C+"/book?token_id="+urllib.parse.quote(t))
        bids=sorted([(float(z["price"]),float(z["size"])) for z in x.get("bids",[]) if float(z.get("size",0))>0],reverse=True)
        asks=sorted([(float(z["price"]),float(z["size"])) for z in x.get("asks",[]) if float(z.get("size",0))>0])
        tick=get(C+"/tick-size?token_id="+urllib.parse.quote(t))
        return bids,asks,float(tick.get("minimum_tick_size") or .01)
    y=b(m["yes"]);n=b(m["no"])
    info=get(C+"/clob-markets/"+urllib.parse.quote(m["cid"]))
    fd=info.get("fd");r=float(fd.get("r") or 0) if isinstance(fd,dict) else 0.
    return m["cid"],y,n,r
def buy(asks,q,r):
    rem=q;cost1=0
    for p,s in asks:
        f=1-r*(1-p);cap=s*f;nn=min(rem,cap);cost1+=(nn/f)*p;rem-=nn
        if rem<=1e-12:break
    if rem>1e-12:return None
    rem=q;cost2=0
    for p,s in asks:
        t=min(rem,s);cost2+=t*(p+r*p*(1-p));rem-=t
        if rem<=1e-12:break
    if rem>1e-12:return None
    return max(cost1,cost2)
def sell(bids,q,r):
    rem=q;out=0
    for p,s in bids:
        t=min(rem,s);out+=t*(p-r*p*(1-p));rem-=t
        if rem<=1e-12:return out
    return None

ms=markets();data={}
with cf.ThreadPoolExecutor(max_workers=40) as ex:
    fut={ex.submit(fetch,m):m for m in ms}
    for f in cf.as_completed(fut):
        try:
            cid,y,n,r=f.result();data[cid]=(y,n,r)
        except:pass

pos=[]
for m in ms:
    if m["cid"] not in data:continue
    y,n,r=data[m["cid"]]
    for maker_label,own,other in [("YES",y,n),("NO",n,y)]:
        bids,asks,tick=own; obids,oasks,_=other
        if bids and asks and oasks:
            bid,queue=bids[0];ask=asks[0][0]
            for mode,px in [("JOIN",bid),("JUMP",round(bid+tick,10) if bid+tick<ask-1e-12 else None)]:
                if px is None:continue
                for q in SIZES:
                    hc=buy(oasks,q,r)
                    if hc is None:continue
                    p=q-px*q-hc
                    if p>0:
                        pos.append({"kind":"MAKER_BUY_THEN_TAKER_BUY","market":m["q"],"slug":m["slug"],
                          "maker_leg":maker_label,"mode":mode,"maker_price":px,"queue_at_best":queue,
                          "tick":tick,"fee":r,"size":q,"profit":p,"roi":p/(px*q+hc),
                          "v24":m["v24"],"liq":m["liq"],"end":m["end"]})
        # Pre-hold complete set: maker SELL at best ask, then taker SELL complement.
        if bids and asks and obids:
            bid=bids[0][0];ask,queue_ask=asks[0]
            for mode,px in [("JOIN",ask),("JUMP",round(ask-tick,10) if ask-tick>bid+1e-12 else None)]:
                if px is None:continue
                for q in SIZES:
                    hp=sell(obids,q,r)
                    if hp is None:continue
                    p=px*q+hp-q
                    if p>0:
                        pos.append({"kind":"MAKER_SELL_THEN_TAKER_SELL","market":m["q"],"slug":m["slug"],
                          "maker_leg":maker_label,"mode":mode,"maker_price":px,"queue_at_best":queue_ask,
                          "tick":tick,"fee":r,"size":q,"profit":p,"roi_on_complete_set":p/q,
                          "v24":m["v24"],"liq":m["liq"],"end":m["end"]})
# dedupe/rank: execution relevance = jump first, volume, profit per unit
for z in pos:z["profit_per_share"]=z["profit"]/z["size"]
pos.sort(key=lambda z:(z["mode"]=="JUMP",z["v24"],z["profit_per_share"]),reverse=True)
print("GLOBAL_BINARY_LOCK_SUMMARY",json.dumps({
 "utc":datetime.now(timezone.utc).isoformat(),"markets":len(ms),"books":len(data),
 "positive_count":len(pos),"jump_positive":sum(x["mode"]=="JUMP" for x in pos),
 "top_by_execution":pos[:100],
 "top_by_edge":sorted(pos,key=lambda z:z["profit_per_share"],reverse=True)[:50],
 "top_by_volume":sorted(pos,key=lambda z:z["v24"],reverse=True)[:50]
},separators=(",",":")),flush=True)
