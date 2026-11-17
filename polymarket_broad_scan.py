#!/usr/bin/env python3
import concurrent.futures as cf
import json, re, time, urllib.parse, urllib.request
from datetime import datetime, timezone

GAMMA="https://gamma-api.polymarket.com"
CLOB="https://clob.polymarket.com"
UA="Mozilla/5.0 polymarket-broad-arb-check/1.0"
MAX_FEE=0.07
FEE_RATES={}
SIZES=[1,5,10,25,50,100]

def get_json(url, timeout=20):
    last=None
    for i in range(4):
        try:
            req=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"application/json"})
            with urllib.request.urlopen(req,timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            last=e; time.sleep(0.35*(2**i))
    raise last

def j(x):
    if isinstance(x,(list,dict)): return x
    if isinstance(x,str):
        try:return json.loads(x)
        except:return []
    return []

def market_rec(x):
    outs=[str(v) for v in j(x.get("outcomes"))]
    tids=[str(v) for v in j(x.get("clobTokenIds"))]
    if len(outs)!=2 or len(tids)!=2:return None
    d={str(o).casefold():t for o,t in zip(outs,tids)}
    if "yes" not in d or "no" not in d:return None
    return {
      "id":str(x.get("id") or ""),"condition_id":str(x.get("conditionId") or ""),"question":str(x.get("question") or ""),
      "slug":str(x.get("slug") or ""),"yes":d["yes"],"no":d["no"],
      "fees":bool(x.get("feesEnabled",False)),
      "end":str(x.get("endDate") or ""),"resolution":str(x.get("resolutionSource") or ""),
      "raw":x
    }

def parse_book(raw):
    bids=sorted([(float(a["price"]),float(a["size"])) for a in raw.get("bids",[]) if float(a.get("size",0))>0],key=lambda z:z[0],reverse=True)
    asks=sorted([(float(a["price"]),float(a["size"])) for a in raw.get("asks",[]) if float(a.get("size",0))>0],key=lambda z:z[0])
    return bids,asks

def fetch_book(tid):
    return tid,parse_book(get_json(f"{CLOB}/book?token_id={urllib.parse.quote(tid)}"))

def fetch_fee_info(m):
    cid=m["condition_id"]
    raw=get_json(f"{CLOB}/clob-markets/{urllib.parse.quote(cid)}")
    fd=raw.get("fd")
    rate=float(fd.get("r") or 0.0) if isinstance(fd,dict) else 0.0
    return cid, rate, fd

def rfee(m):
    return FEE_RATES.get(m["condition_id"], 0.0 if not m["fees"] else MAX_FEE)

def buy_net_factor(p,r): return 1-r*(1-p)

def buy_cost_share_fee(asks,target,r):
    # Legacy/help-center interpretation: fee collected in shares.
    rem=target; cost=0.0
    for p,sz in asks:
        f=buy_net_factor(p,r)
        cap=sz*f
        tn=min(rem,cap)
        cost += (tn/f)*p
        rem -= tn
        if rem<=1e-9:return cost
    return None

def buy_cost_usdc_fee(asks,target,r):
    # V2 exchange / live-fill interpretation: full shares received, fee debited in collateral.
    rem=target; cost=0.0
    for p,sz in asks:
        take=min(rem,sz)
        cost += take*(p + r*p*(1-p))
        rem -= take
        if rem<=1e-9:return cost
    return None

def buy_cost(asks,target,r):
    # Robust scanner: use the more expensive of both plausible collection mechanisms.
    a=buy_cost_share_fee(asks,target,r)
    b=buy_cost_usdc_fee(asks,target,r)
    if a is None or b is None:return None
    return max(a,b)

def sell_net(p,r): return p-r*p*(1-p)
def sell_proceeds(bids,target,r):
    rem=target; out=0.0
    for p,sz in bids:
        t=min(rem,sz); out += t*sell_net(p,r); rem-=t
        if rem<=1e-9:return out
    return None

def list_markets(maxn=600):
    out=[]; off=0
    while len(out)<maxn:
        n=min(100,maxn-len(out))
        q=urllib.parse.urlencode({"active":"true","closed":"false","limit":n,"offset":off,"order":"volumeNum","ascending":"false"})
        arr=get_json(f"{GAMMA}/markets?{q}")
        if not arr:break
        out.extend(arr); off += len(arr)
        if len(arr)<n:break
    return out

def list_events(maxn=300):
    out=[]; off=0
    while len(out)<maxn:
        n=min(100,maxn-len(out))
        q=urllib.parse.urlencode({"active":"true","closed":"false","limit":n,"offset":off})
        arr=get_json(f"{GAMMA}/events?{q}")
        if not arr:break
        out.extend(arr); off += len(arr)
        if len(arr)<n:break
    return out

THRESH=re.compile(r"(?P<prefix>\b(?:above|over|higher than|greater than|at least)\s*\$?)\s*(?P<k>[0-9][0-9,]*(?:\.[0-9]+)?)",re.I)

def thresh_sig(m):
    q=m["question"]
    z=THRESH.search(q)
    if not z:return None
    k=float(z.group("k").replace(",",""))
    normalized=(q[:z.start("k")]+"<K>"+q[z.end("k"):]).casefold()
    normalized=re.sub(r"\s+"," ",normalized).strip()
    return normalized,k

def main():
    print("BROAD_SCAN_UTC",datetime.now(timezone.utc).isoformat(),flush=True)
    raw=list_markets(600)
    ms=[m for x in raw if (m:=market_rec(x)) is not None]
    print("MARKETS_DISCOVERED",len(raw),"BINARY_CLOB",len(ms),flush=True)

    tids=sorted({t for m in ms for t in (m["yes"],m["no"])})
    books={}
    with cf.ThreadPoolExecutor(max_workers=40) as ex:
        futs=[ex.submit(fetch_book,t) for t in tids]
        for f in cf.as_completed(futs):
            try:
                tid,b=f.result(); books[tid]=b
            except Exception:
                pass
    print("BOOKS_FETCHED",len(books),"TOKENS_REQUESTED",len(tids),flush=True)

    fee_markets=[m for m in ms if m["condition_id"]]
    fee_meta={}
    with cf.ThreadPoolExecutor(max_workers=40) as ex:
        futs=[ex.submit(fetch_fee_info,m) for m in fee_markets]
        for f in cf.as_completed(futs):
            try:
                cid,rate,fd=f.result()
                FEE_RATES[cid]=rate
                fee_meta[cid]=fd
            except Exception:
                pass
    vals=sorted(set(FEE_RATES.values()))
    print("FD_RATES_FETCHED",len(FEE_RATES),"MARKETS_REQUESTED",len(fee_markets),
          "UNIQUE_RATES",vals,flush=True)

    complete=[]
    for m in ms:
        if m["yes"] not in books or m["no"] not in books:continue
        yb,ya=books[m["yes"]]; nb,na=books[m["no"]]; r=rfee(m)
        top={"yb":yb[0][0] if yb else None,"ya":ya[0][0] if ya else None,"nb":nb[0][0] if nb else None,"na":na[0][0] if na else None}
        for s in SIZES:
            yc=buy_cost(ya,s,r); nc=buy_cost(na,s,r)
            if yc is not None and nc is not None:
                p=s-yc-nc
                if p>0:
                    complete.append({"kind":"BUY_COMPLETE_SET","slug":m["slug"],"q":m["question"],"size":s,"profit":p,"roi":p/(yc+nc),"fee_assumption":r,"top":top})
            yp=sell_proceeds(yb,s,r); np=sell_proceeds(nb,s,r)
            if yp is not None and np is not None:
                p=yp+np-s
                if p>0:
                    complete.append({"kind":"SELL_COMPLETE_SET","slug":m["slug"],"q":m["question"],"size":s,"profit":p,"roi":p/s,"fee_assumption":r,"top":top})
    complete.sort(key=lambda x:x["profit"],reverse=True)
    print("COMPLETE_SET_POSITIVE",len(complete),flush=True)
    for x in complete[:30]:print("COMPLETE_ARB",json.dumps(x,separators=(",",":")),flush=True)

    groups={}
    for m in ms:
        sg=thresh_sig(m)
        if not sg:continue
        norm,k=sg
        key=(norm,m["end"],m["resolution"])
        groups.setdefault(key,[]).append((k,m))
    impl=[]
    for key,xs in groups.items():
        if len(xs)<2:continue
        xs=sorted(xs,key=lambda z:z[0])
        for i in range(len(xs)):
            low,ml=xs[i]
            for j2 in range(i+1,len(xs)):
                high,mh=xs[j2]
                if ml["yes"] not in books or mh["no"] not in books:continue
                la=books[ml["yes"]][1]; ha=books[mh["no"]][1]
                for s in SIZES:
                    c1=buy_cost(la,s,rfee(ml)); c2=buy_cost(ha,s,rfee(mh))
                    if c1 is None or c2 is None:continue
                    p=s-c1-c2
                    if p>0:
                        impl.append({"kind":"IMPLICATION","low":low,"high":high,
                          "low_slug":ml["slug"],"high_slug":mh["slug"],
                          "low_q":ml["question"],"high_q":mh["question"],
                          "size":s,"profit_floor":p,"roi_floor":p/(c1+c2),
                          "same_end":ml["end"],"same_resolution":ml["resolution"]})
    impl.sort(key=lambda x:x["profit_floor"],reverse=True)
    print("IMPLICATION_POSITIVE",len(impl),flush=True)
    for x in impl[:30]:print("IMPL_ARB",json.dumps(x,separators=(",",":")),flush=True)

    # NegRisk event: buy one net YES in every mutually-exclusive exhaustive outcome.
    # Derive NegRisk events from the already-fetched active market records, then
    # fetch each full event by slug. This avoids Gamma /events listing 406s.
    event_slugs={}
    for x in raw:
        for ev0 in (x.get("events") or []):
            if ev0.get("negRisk") and not ev0.get("negRiskAugmented") and ev0.get("slug"):
                event_slugs[str(ev0["slug"])]=True
    events=[]
    def fetch_event_slug(slug):
        return get_json(f"{GAMMA}/events/slug/{urllib.parse.quote(slug)}")
    if event_slugs:
        with cf.ThreadPoolExecutor(max_workers=20) as ex:
            futs={ex.submit(fetch_event_slug,s):s for s in event_slugs}
            for fut in cf.as_completed(futs):
                try:
                    ev=fut.result()
                    if isinstance(ev,dict):
                        events.append(ev)
                except Exception as e:
                    print("EVENT_FETCH_ERROR",futs[fut],repr(e),flush=True)
    print("NEGRISK_EVENT_SLUGS_DERIVED",len(event_slugs),"EVENTS_FETCHED",len(events),flush=True)
    nr=[]
    nr_events=0
    for ev in events:
        if not ev.get("negRisk") or ev.get("negRiskAugmented"):continue
        em=[]
        for x in ev.get("markets") or []:
            m=market_rec(x)
            if m: em.append(m)
        if len(em)<2 or len(em)>30:continue
        nr_events+=1
        missing=[m["yes"] for m in em if m["yes"] not in books]
        if missing:
            with cf.ThreadPoolExecutor(max_workers=min(20,len(missing))) as ex:
                futs=[ex.submit(fetch_book,t) for t in missing]
                for f in cf.as_completed(futs):
                    try:
                        tid,b=f.result();books[tid]=b
                    except Exception:pass
        if any(m["yes"] not in books for m in em):continue
        for s in (1,5,10,25):
            total=0.0; ok=True
            for m in em:
                c=buy_cost(books[m["yes"]][1],s,rfee(m))
                if c is None:ok=False;break
                total+=c
            if ok:
                p=s-total
                if p>0:
                    nr.append({"kind":"NEGRISK_BUY_ALL_YES","event":str(ev.get("title") or ev.get("question") or ev.get("slug") or ""),
                      "event_slug":str(ev.get("slug") or ""),"outcomes":len(em),"size":s,
                      "cost":total,"profit":p,"roi":p/total})
    nr.sort(key=lambda x:x["profit"],reverse=True)
    print("NEGRISK_EVENTS_CHECKED",nr_events,"NEGRISK_POSITIVE",len(nr),flush=True)
    for x in nr[:30]:print("NEGRISK_ARB",json.dumps(x,separators=(",",":")),flush=True)

    summary={"utc":datetime.now(timezone.utc).isoformat(),"markets":len(ms),"books":len(books),
             "complete_count":len(complete),"implication_count":len(impl),
             "negrisk_events":nr_events,"negrisk_count":len(nr),
             "best_complete":complete[:10],"best_implication":impl[:10],"best_negrisk":nr[:10]}
    print("BROAD_SUMMARY",json.dumps(summary,separators=(",",":")),flush=True)

if __name__=="__main__":main()
