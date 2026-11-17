#!/usr/bin/env python3
import json,time,urllib.parse,urllib.request
from datetime import datetime,timezone

G="https://gamma-api.polymarket.com";C="https://clob.polymarket.com"
ASSETS=("btc","eth","sol");DURS=(5,15);UA="binary-maker-taker-arb/1.0"
SIZES=(1,5,10,25,50,100);BUFFER=0.0005

def get(url):
    req=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=15) as r:return json.loads(r.read().decode())
def j(x):
    if isinstance(x,(list,dict)):return x
    if isinstance(x,str):
        try:return json.loads(x)
        except:return []
    return []
def slug(a,d):
    now=int(time.time());step=d*60
    return f"{a}-updown-{d}m-{(now//step)*step}"
def market(s):
    e=get(G+"/events/slug/"+urllib.parse.quote(s));x=(e.get("markets") or [])[0]
    outs=[str(v) for v in j(x.get("outcomes"))];tids=[str(v) for v in j(x.get("clobTokenIds"))]
    mp={o.casefold():t for o,t in zip(outs,tids)}
    y=mp.get("up") or mp.get("yes");n=mp.get("down") or mp.get("no")
    info=get(C+"/clob-markets/"+urllib.parse.quote(str(x.get("conditionId") or "")))
    fd=info.get("fd");r=float(fd.get("r") or 0) if isinstance(fd,dict) else 0.
    return {"slug":s,"q":x.get("question"),"yes":y,"no":n,"r":r}
def book(t):
    x=get(C+"/book?token_id="+urllib.parse.quote(t))
    bids=sorted([(float(z["price"]),float(z["size"])) for z in x.get("bids",[]) if float(z.get("size",0))>0],reverse=True)
    asks=sorted([(float(z["price"]),float(z["size"])) for z in x.get("asks",[]) if float(z.get("size",0))>0])
    ts=get(C+"/tick-size?token_id="+urllib.parse.quote(t))
    return bids,asks,float(ts.get("minimum_tick_size") or .01)
def buy_cost(asks,target,r):
    # Conservative max of current known fee-collection conventions.
    rem=target;share=0.;usdc=0.
    for p,s in asks:
        f=1-r*(1-p)
        netcap=s*f
        take_net=min(rem,netcap)
        gross=take_net/f
        share+=gross*p
        rem-=take_net
        if rem<=1e-12:break
    if rem>1e-12:return None
    rem=target
    for p,s in asks:
        take=min(rem,s);usdc+=take*(p+r*p*(1-p));rem-=take
        if rem<=1e-12:break
    if rem>1e-12:return None
    return max(share,usdc)

rows=[]
for a in ASSETS:
  for d in DURS:
    try:
      m=market(slug(a,d));yb,ya,yt=book(m["yes"]);nb,na,nt=book(m["no"])
      for maker_name,mbids,masks,tick,other_asks in [
        ("YES",yb,ya,yt,na),("NO",nb,na,nt,ya)
      ]:
        if not mbids or not masks:continue
        bid,queue=mbids[0];ask=masks[0][0]
        jump=round(bid+tick,10)
        for mode,price in [("join",bid),("jump",jump if jump<ask-1e-12 else None)]:
          if price is None:continue
          vals=[]
          for q in SIZES:
            hc=buy_cost(other_asks,q,m["r"])
            if hc is None:continue
            profit=q-price*q-hc
            vals.append({"size":q,"profit":profit,"roi_on_total_cost":profit/(price*q+hc)})
          rows.append({"market":m["q"],"slug":m["slug"],"maker_leg":maker_name,"mode":mode,
                       "maker_price":price,"best_bid":bid,"queue_at_best":queue,"best_ask":ask,
                       "tick":tick,"fee_rate":m["r"],"other_best_ask":other_asks[0] if other_asks else None,
                       "tests":vals})
    except Exception as e:rows.append({"asset":a,"dur":d,"error":repr(e)})

pos=[]
for r in rows:
    for z in r.get("tests",[]):
        if z["profit"]>0:
            pos.append({**{k:v for k,v in r.items() if k!="tests"},**z})
pos.sort(key=lambda z:z["profit"],reverse=True)
print("BINARY_MAKER_TAKER_SUMMARY",json.dumps({
 "utc":datetime.now(timezone.utc).isoformat(),"rows":rows,
 "positive_count":len(pos),"positives":pos[:50]
},separators=(",",":")),flush=True)
