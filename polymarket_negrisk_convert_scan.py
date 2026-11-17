#!/usr/bin/env python3
import concurrent.futures as cf
import json, urllib.parse, urllib.request
from datetime import datetime,timezone

G="https://gamma-api.polymarket.com";C="https://clob.polymarket.com"
UA="polymarket-negrisk-convert-scan/1.0"
MAXN=1500
SIZES=(1,5,10)

def get(url,timeout=20):
    req=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=timeout) as r:return json.loads(r.read().decode())

def j(x):
    if isinstance(x,(list,dict)):return x
    if isinstance(x,str):
        try:return json.loads(x)
        except:return []
    return []

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

def event(slug):
    return get(f"{G}/events/slug/{urllib.parse.quote(slug)}")

def mrec(x):
    outs=[str(v) for v in j(x.get("outcomes"))];tids=[str(v) for v in j(x.get("clobTokenIds"))]
    if len(outs)!=2 or len(tids)!=2:return None
    mp={o.casefold():t for o,t in zip(outs,tids)}
    if "yes" not in mp or "no" not in mp:return None
    return {"slug":str(x.get("slug") or ""),"q":str(x.get("question") or ""),
      "yes":mp["yes"],"no":mp["no"],"cid":str(x.get("conditionId") or ""),
      "active":bool(x.get("active",False)),"closed":bool(x.get("closed",False)),
      "fees":bool(x.get("feesEnabled",False))}

def book(t):
    x=get(f"{C}/book?token_id={urllib.parse.quote(t)}")
    bids=sorted([(float(v["price"]),float(v["size"])) for v in x.get("bids",[]) if float(v.get("size",0))>0],reverse=True)
    asks=sorted([(float(v["price"]),float(v["size"])) for v in x.get("asks",[]) if float(v.get("size",0))>0])
    return bids,asks

def fee(m):
    try:
        x=get(f"{C}/clob-markets/{urllib.parse.quote(m['cid'])}")
        fd=x.get("fd");return float(fd.get("r") or 0) if isinstance(fd,dict) else 0.0
    except:return 0.07 if m["fees"] else 0.0

def buy(asks,target,r):
    rem=target;cost=0.0
    for p,s in asks:
        f=1-r*(1-p);cap=s*f;n=min(rem,cap);cost+=(n/f)*p;rem-=n
        if rem<=1e-12:return cost
    return None

def sell(bids,target,r):
    rem=target;out=0.0
    for p,s in bids:
        t=min(rem,s);out+=t*(p-r*p*(1-p));rem-=t
        if rem<=1e-12:return out
    return None

def main():
    raw=list_markets()
    slugs={}
    for x in raw:
        for e in x.get("events") or []:
            if e.get("negRisk") and not e.get("negRiskAugmented") and e.get("slug"):
                slugs[str(e["slug"])]=1
    print("NR_START",datetime.now(timezone.utc).isoformat(),"active_markets",len(raw),"event_slugs",len(slugs),flush=True)
    evs=[]
    with cf.ThreadPoolExecutor(max_workers=25) as ex:
        fut={ex.submit(event,s):s for s in slugs}
        for f in cf.as_completed(fut):
            try:
                e=f.result()
                if isinstance(e,dict):evs.append(e)
            except Exception:pass

    candidates=[]
    for e in evs:
        if not e.get("negRisk") or e.get("negRiskAugmented"):continue
        ms=[m for x in (e.get("markets") or []) if (m:=mrec(x))]
        # Fail closed: converter basket research only when every question shown by event is binary,
        # active, nonclosed and tokenized.
        if len(ms)<2 or len(ms)!=(len(e.get("markets") or [])):continue
        if not all(m["active"] and not m["closed"] for m in ms):continue
        candidates.append((e,ms))
    print("NR_EVENTS_FETCHED",len(evs),"FULL_ACTIVE_EVENTS",len(candidates),flush=True)

    tids={t for e,ms in candidates for m in ms for t in (m["yes"],m["no"])}
    books={}
    with cf.ThreadPoolExecutor(max_workers=50) as ex:
        fut={ex.submit(book,t):t for t in tids}
        for f in cf.as_completed(fut):
            try:books[fut[f]]=f.result()
            except:pass
    allm={m["cid"]:m for e,ms in candidates for m in ms}
    fees={}
    with cf.ThreadPoolExecutor(max_workers=30) as ex:
        fut={ex.submit(fee,m):cid for cid,m in allm.items()}
        for f in cf.as_completed(fut):
            try:fees[fut[f]]=f.result()
            except:pass
    print("NR_BOOKS",len(books),"TOKENS",len(tids),"FEES",len(fees),flush=True)

    results=[]
    # Identity for subset S of NOs:
    # sum_{i in S} NO_i == (|S|-1) collateral + sum_{j notin S} YES_j
    # Supported adapter direction is NO -> collateral + complementary YES.
    # Ignoring adapter conversion fee here; any positive is a candidate requiring on-chain fee validation.
    for e,ms in candidates:
        if any(m["yes"] not in books or m["no"] not in books for m in ms):continue
        n=len(ms)
        for size in SIZES:
            vals=[];ok=True
            total_sell_yes=0.0
            for m in ms:
                r=fees.get(m["cid"],0.07 if m["fees"] else 0.0)
                buy_no=buy(books[m["no"]][1],size,r)
                sell_yes=sell(books[m["yes"]][0],size,r)
                if buy_no is None or sell_yes is None:
                    ok=False;break
                vals.append((m,buy_no,sell_yes, size-buy_no-sell_yes))
                total_sell_yes+=sell_yes
            if not ok:continue
            # Objective = -size + totalSellYES + sum_{i in S}(size-buyNO_i-sellYES_i)
            # For nonempty S, optimal subset includes every positive incremental term;
            # if none are positive, include the single least-negative one.
            positive=[v for v in vals if v[3]>0]
            chosen=positive if positive else [max(vals,key=lambda z:z[3])]
            profit=-size+total_sell_yes+sum(v[3] for v in chosen)
            if profit>0:
                results.append({
                  "event":str(e.get("title") or e.get("slug") or ""),"event_slug":str(e.get("slug") or ""),
                  "n":n,"size":size,"profit_before_convert_fee":profit,
                  "roi_on_gross_no_cost":profit/sum(v[1] for v in chosen),
                  "subset_NO":[{"q":v[0]["q"],"slug":v[0]["slug"],"buy_no_cost":v[1],"foregone_yes_bid":v[2],"increment":v[3]} for v in chosen],
                  "complement_YES":[{"q":v[0]["q"],"slug":v[0]["slug"],"sell_yes_proceeds":v[2]} for v in vals if v not in chosen],
                  "collateral_out":size*(len(chosen)-1),
                  "conversion_fee_assumed":0.0
                })
    results.sort(key=lambda x:x["profit_before_convert_fee"],reverse=True)
    print("NR_POSITIVE",len(results),flush=True)
    for x in results[:50]:print("NR_ARB",json.dumps(x,separators=(",",":")),flush=True)
    print("NR_SUMMARY",json.dumps({"utc":datetime.now(timezone.utc).isoformat(),
      "events":len(candidates),"positive":len(results),"best":results[:20]},separators=(",",":")),flush=True)

if __name__=="__main__":main()
