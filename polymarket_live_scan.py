#!/usr/bin/env python3
import json, math, time, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timezone

GAMMA="https://gamma-api.polymarket.com"
CLOB="https://clob.polymarket.com"
UA="Mozilla/5.0 polymarket-live-arb-check/1.0"
FEE_RATE=0.07
SIZES=[1,5,10,25,50,100,250,500]

def get_json(url, timeout=15):
    last=None
    for i in range(4):
        try:
            req=urllib.request.Request(url, headers={"User-Agent":UA,"Accept":"application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            last=e
            time.sleep(0.5*(2**i))
    raise last

def j(x):
    if isinstance(x,(list,dict)): return x
    if isinstance(x,str):
        try:return json.loads(x)
        except:return []
    return []

def parse_book(raw):
    bids=sorted([(float(x["price"]),float(x["size"])) for x in raw.get("bids",[])],
                key=lambda z:z[0], reverse=True)
    asks=sorted([(float(x["price"]),float(x["size"])) for x in raw.get("asks",[])],
                key=lambda z:z[0])
    return bids,asks

def buy_net_factor(p,r):
    # BUY fee has USD-equivalent C*r*p*(1-p), collected in shares.
    # shares withheld = fee_usd / p = C*r*(1-p)
    return 1-r*(1-p)

def buy_cost_for_net(asks,target,r):
    rem=target; cost=0.0; gross=0.0
    for p,sz in asks:
        f=buy_net_factor(p,r)
        cap=sz*f
        take_net=min(rem,cap)
        take_gross=take_net/f
        cost += take_gross*p
        gross += take_gross
        rem -= take_net
        if rem <= 1e-9:
            return {"cost":cost,"gross":gross}
    return None

def sell_net_per_share(p,r):
    return p-r*p*(1-p)

def sell_proceeds(bids,target,r):
    rem=target; net=0.0; gross_notional=0.0
    for p,sz in bids:
        take=min(rem,sz)
        net += take*sell_net_per_share(p,r)
        gross_notional += take*p
        rem -= take
        if rem <= 1e-9:
            return {"net":net,"vwap":gross_notional/target}
    return None

def market_from_slug(slug):
    u=f"{GAMMA}/events/slug/{urllib.parse.quote(slug)}"
    ev=get_json(u)
    ms=ev.get("markets") or []
    if not ms: return None
    m=ms[0]
    outcomes=j(m.get("outcomes")); tids=[str(x) for x in j(m.get("clobTokenIds"))]
    if len(outcomes)!=2 or len(tids)!=2:return None
    return {"slug":slug,"question":m.get("question"),"outcomes":outcomes,"tids":tids,
            "feesEnabled":bool(m.get("feesEnabled",False)),"market":m}

def outcome_token(m,label):
    for o,t in zip(m["outcomes"],m["tids"]):
        if str(o).casefold()==label.casefold():return t
    # Up/Down markets sometimes still use Up/Down, not Yes/No
    return None

def scan_one(asset,mins,round_start):
    slug=f"{asset}-updown-{mins}m-{round_start}"
    m=market_from_slug(slug)
    if not m:
        return {"slug":slug,"error":"market_not_found"}
    up=outcome_token(m,"Up") or outcome_token(m,"Yes")
    down=outcome_token(m,"Down") or outcome_token(m,"No")
    if not up or not down:
        return {"slug":slug,"error":"tokens_not_found","outcomes":m["outcomes"]}
    ub=parse_book(get_json(f"{CLOB}/book?token_id={urllib.parse.quote(up)}"))
    db=parse_book(get_json(f"{CLOB}/book?token_id={urllib.parse.quote(down)}"))
    r=FEE_RATE if m["feesEnabled"] else 0.0
    out={"slug":slug,"question":m["question"],"asset":asset,"minutes":mins,
         "round_start":round_start,"round_end":round_start+mins*60,
         "feesEnabled":m["feesEnabled"],"feeRate":r,
         "up_token":up,"down_token":down}
    out["top"]={
        "up_bid": ub[0][0][0] if ub[0] else None,
        "up_bid_size":ub[0][0][1] if ub[0] else None,
        "up_ask": ub[1][0][0] if ub[1] else None,
        "up_ask_size":ub[1][0][1] if ub[1] else None,
        "down_bid":db[0][0][0] if db[0] else None,
        "down_bid_size":db[0][0][1] if db[0] else None,
        "down_ask":db[1][0][0] if db[1] else None,
        "down_ask_size":db[1][0][1] if db[1] else None,
    }
    opp=[]
    for s in SIZES:
        a=buy_cost_for_net(ub[1],s,r); b=buy_cost_for_net(db[1],s,r)
        if a and b:
            p=s-a["cost"]-b["cost"]
            opp.append({"kind":"BUY_COMPLETE_SET","size":s,"cost":a["cost"]+b["cost"],
                        "profit":p,"roi":p/(a["cost"]+b["cost"])})
        a=sell_proceeds(ub[0],s,r); b=sell_proceeds(db[0],s,r)
        if a and b:
            p=a["net"]+b["net"]-s
            opp.append({"kind":"SELL_COMPLETE_SET","size":s,"net_proceeds":a["net"]+b["net"],
                        "profit":p,"roi":p/s})
    out["opportunities"]=sorted(opp,key=lambda x:x["profit"],reverse=True)
    out["positive"]=[x for x in out["opportunities"] if x["profit"]>0]
    return out

def main():
    now=int(time.time())
    print("SCAN_UTC",datetime.now(timezone.utc).isoformat(),flush=True)
    print("EPOCH",now,flush=True)
    results=[]
    for asset in ("btc","eth","sol"):
        for mins in (5,15):
            step=mins*60
            rs=(now//step)*step
            try:
                res=scan_one(asset,mins,rs)
            except Exception as e:
                res={"slug":f"{asset}-updown-{mins}m-{rs}","error":repr(e)}
            results.append(res)
            print("MARKET_JSON",json.dumps(res,separators=(",",":")),flush=True)
    positives=[]
    for r in results:
        for x in r.get("positive",[]):
            positives.append({"slug":r["slug"],**x})
    positives.sort(key=lambda x:x["profit"],reverse=True)
    print("POSITIVE_COUNT",len(positives),flush=True)
    for x in positives[:50]:
        print("ARB_JSON",json.dumps(x,separators=(",",":")),flush=True)
    print("SUMMARY_JSON",json.dumps({"scan_utc":datetime.now(timezone.utc).isoformat(),
          "markets":len(results),"positive_count":len(positives),
          "best":positives[:20]},separators=(",",":")),flush=True)

if __name__=="__main__":
    main()
