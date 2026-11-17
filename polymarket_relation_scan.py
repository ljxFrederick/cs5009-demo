#!/usr/bin/env python3
import concurrent.futures as cf
import json,re,time,urllib.parse,urllib.request
from collections import defaultdict
from datetime import datetime,timezone

GAMMA="https://gamma-api.polymarket.com";CLOB="https://clob.polymarket.com"
UA="polymarket-cross-market-identity-scan/1.0"
SIZES=(1,5,10,25)
MAXN=1500

def get_json(url,timeout=20,method="GET",body=None):
    data=None;h={"User-Agent":UA,"Accept":"application/json"}
    if body is not None:data=json.dumps(body).encode();h["Content-Type"]="application/json"
    req=urllib.request.Request(url,data=data,headers=h,method=method)
    with urllib.request.urlopen(req,timeout=timeout) as r:return json.loads(r.read().decode())

def j(x):
    if isinstance(x,(list,dict)):return x
    if isinstance(x,str):
        try:return json.loads(x)
        except:return []
    return []

def norm(s):
    return re.sub(r"\s+"," ",re.sub(r"[^a-z0-9$%<>.=+-]+"," ",s.casefold())).strip()

def rec(x):
    outs=[str(v) for v in j(x.get("outcomes"))];tids=[str(v) for v in j(x.get("clobTokenIds"))]
    if len(outs)!=2 or len(tids)!=2:return None
    mp={o.casefold():t for o,t in zip(outs,tids)}
    if "yes" not in mp or "no" not in mp:return None
    return {"slug":str(x.get("slug") or ""),"q":str(x.get("question") or ""),
      "qn":norm(str(x.get("question") or "")),"yes":mp["yes"],"no":mp["no"],
      "cid":str(x.get("conditionId") or ""),"end":str(x.get("endDate") or ""),
      "source":str(x.get("resolutionSource") or ""),"desc":str(x.get("description") or ""),
      "fees":bool(x.get("feesEnabled",False))}

def markets():
    out=[];off=0
    while len(out)<MAXN:
        n=min(100,MAXN-len(out))
        q=urllib.parse.urlencode({"active":"true","closed":"false","limit":n,"offset":off,"order":"volumeNum","ascending":"false"})
        a=get_json(f"{GAMMA}/markets?{q}")
        if not a:break
        out.extend(a);off+=len(a)
        if len(a)<n:break
    return [m for x in out if (m:=rec(x))]

UP=re.compile(r"\b(above|over|higher than|greater than|at least|more than)\s*\$?([0-9][0-9,]*(?:\.[0-9]+)?)",re.I)
DN=re.compile(r"\b(below|under|lower than|less than|at most|fewer than)\s*\$?([0-9][0-9,]*(?:\.[0-9]+)?)",re.I)
DATE_PAT=[
 re.compile(r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?",re.I),
 re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
]

def threshold_sig(m):
    q=m["q"]
    for direction,pat in (("UP",UP),("DN",DN)):
        z=pat.search(q)
        if z:
            k=float(z.group(2).replace(",",""))
            sk=norm(q[:z.start(2)]+" <N> "+q[z.end(2):])
            return direction,k,sk
    return None

def deadline_sig(m):
    q=m["q"]
    # "event occurs by/before D": earlier deadline => later deadline.
    # "state continues through/until D": later horizon => earlier horizon.
    has_duration=bool(re.search(r"\b(through|until)\b",q,re.I))
    has_occurrence=bool(re.search(r"\b(by|before)\b",q,re.I))
    if not (has_duration or has_occurrence):return None
    if has_duration and has_occurrence:
        return None  # ambiguous phrasing: fail closed
    semantic="DURATION" if has_duration else "OCCURRENCE"
    sk=q
    changed=False
    for p in DATE_PAT:
        sk,n=p.subn("<DATE>",sk);changed=changed or n>0
    return (semantic,norm(sk)) if changed else None

def book(t):
    x=get_json(f"{CLOB}/book?token_id={urllib.parse.quote(t)}")
    bids=sorted([(float(v["price"]),float(v["size"])) for v in x.get("bids",[])],reverse=True)
    asks=sorted([(float(v["price"]),float(v["size"])) for v in x.get("asks",[])])
    return bids,asks

def fee(m):
    try:
        x=get_json(f"{CLOB}/clob-markets/{urllib.parse.quote(m['cid'])}")
        fd=x.get("fd");return float(fd.get("r") or 0) if isinstance(fd,dict) else 0.0
    except:return 0.07 if m["fees"] else 0.0

def buy(asks,target,r):
    rem=target;cost=0.0
    for p,s in asks:
        f=1-r*(1-p);cap=s*f;n=min(rem,cap);cost+=(n/f)*p;rem-=n
        if rem<=1e-12:return cost
    return None

def main():
    ms=markets();print("REL_START",datetime.now(timezone.utc).isoformat(),"markets",len(ms),flush=True)
    byslug={m["slug"]:m for m in ms}
    relations=[]

    # Exact duplicate proposition candidates: require exact normalized question, end, source and description.
    g=defaultdict(list)
    for m in ms:g[(m["qn"],m["end"],m["source"],norm(m["desc"]))].append(m)
    for xs in g.values():
        if len(xs)>1:
            for i in range(len(xs)):
                for k in range(i+1,len(xs)):
                    a,b=xs[i],xs[k]
                    relations.append(("EQUIV",a,b,{"reason":"exact q/end/source/description"}))
                    relations.append(("EQUIV",b,a,{"reason":"exact q/end/source/description"}))

    # Numeric monotonic threshold relations.
    tg=defaultdict(list)
    for m in ms:
        z=threshold_sig(m)
        if z:
            d,k,sk=z;tg[(d,sk,m["end"],m["source"])].append((k,m))
    for (d,sk,end,src),xs in tg.items():
        xs.sort(key=lambda z:z[0])
        for i in range(len(xs)):
            for k in range(i+1,len(xs)):
                lo,ml=xs[i];hi,mh=xs[k]
                if d=="UP": # high => low
                    relations.append(("IMPL",mh,ml,{"kind":"threshold_up","from":hi,"to":lo}))
                else: # below low => below high
                    relations.append(("IMPL",ml,mh,{"kind":"threshold_down","from":lo,"to":hi}))

    # By-date monotonic candidates; same skeleton/source/description, order by endDate.
    dg=defaultdict(list)
    for m in ms:
        ds=deadline_sig(m)
        if ds:
            semantic,sk=ds
            dg[(semantic,sk,m["source"],norm(m["desc"]))].append(m)
    for (semantic,sk,src,desc),xs in dg.items():
        xs=sorted(xs,key=lambda m:m["end"])
        for i in range(len(xs)):
            for k in range(i+1,len(xs)):
                early,later=xs[i],xs[k]
                if not early["end"] or not later["end"] or early["end"]>=later["end"]:
                    continue
                if semantic=="OCCURRENCE":
                    # Occurred by earlier date => occurred by later date.
                    relations.append(("IMPL",early,later,{"kind":"deadline_occurrence","early":early["end"],"late":later["end"]}))
                else:
                    # Continued through later date => continued through earlier date.
                    relations.append(("IMPL",later,early,{"kind":"deadline_duration","early":early["end"],"late":later["end"]}))

    # Deduplicate relation directions.
    uniq={}
    for typ,a,b,meta in relations:
        if a["slug"]==b["slug"]:continue
        uniq[(typ,a["slug"],b["slug"])]=(typ,a,b,meta)
    relations=list(uniq.values())
    print("REL_CANDIDATES",len(relations),flush=True)

    involved={t for typ,a,b,meta in relations for t in (a["yes"],a["no"],b["yes"],b["no"])}
    books={}
    with cf.ThreadPoolExecutor(max_workers=40) as ex:
        fut={ex.submit(book,t):t for t in involved}
        for f in cf.as_completed(fut):
            try:books[fut[f]]=f.result()
            except:pass
    fm={}
    cids={m["cid"]:m for typ,a,b,meta in relations for m in (a,b)}
    with cf.ThreadPoolExecutor(max_workers=30) as ex:
        fut={ex.submit(fee,m):cid for cid,m in cids.items()}
        for f in cf.as_completed(fut):
            try:fm[fut[f]]=f.result()
            except:pass
    print("REL_BOOKS",len(books),"TOKENS",len(involved),"FEES",len(fm),flush=True)

    pos=[];near=[];hybrid=[]
    for typ,A,B,meta in relations:
        # A => B. Strict floor portfolio: NO(A) + YES(B) >= $1.
        if A["no"] not in books or B["yes"] not in books:continue
        noA_bids,noA_asks=books[A["no"]]
        yesB_bids,yesB_asks=books[B["yes"]]
        rA=fm.get(A["cid"],0.07 if A["fees"] else 0.0);rB=fm.get(B["cid"],0.07 if B["fees"] else 0.0)
        for s in SIZES:
            ca=buy(noA_asks,s,rA);cb=buy(yesB_asks,s,rB)
            if ca is not None and cb is not None:
                p=s-ca-cb;row={"type":typ,"A":A["q"],"A_slug":A["slug"],"B":B["q"],"B_slug":B["slug"],
                  "size":s,"cost":ca+cb,"profit_floor":p,"roi_floor":p/(ca+cb),"fees":[rA,rB],"meta":meta}
                if p>0:pos.append(row)
                elif s==1:near.append(row)

            # Conditional lock after ONE maker BUY is filled, then immediately buy the other leg as taker.
            # Maker fee/rebate is set to zero; maker rebate is deliberately excluded.
            # This is NOT unconditional arbitrage because maker fill and hedge-book persistence are uncertain.
            if s==1:
                if noA_bids and cb is not None:
                    mp=noA_bids[0][0]
                    hp=s-(s*mp+cb)
                    hybrid.append({"maker_leg":"NO(A)","maker_price":mp,"taker_leg":"YES(B)",
                      "conditional_profit_floor":hp,"A":A["q"],"A_slug":A["slug"],"B":B["q"],"B_slug":B["slug"],
                      "size":s,"taker_fee":rB,"meta":meta})
                if yesB_bids and ca is not None:
                    mp=yesB_bids[0][0]
                    hp=s-(ca+s*mp)
                    hybrid.append({"maker_leg":"YES(B)","maker_price":mp,"taker_leg":"NO(A)",
                      "conditional_profit_floor":hp,"A":A["q"],"A_slug":A["slug"],"B":B["q"],"B_slug":B["slug"],
                      "size":s,"taker_fee":rA,"meta":meta})
    pos.sort(key=lambda x:x["profit_floor"],reverse=True)
    near.sort(key=lambda x:x["profit_floor"],reverse=True)
    hybrid.sort(key=lambda x:x["conditional_profit_floor"],reverse=True)
    hpos=[x for x in hybrid if x["conditional_profit_floor"]>0]
    print("REL_POSITIVE",len(pos),flush=True)
    for x in pos[:50]:print("REL_ARB",json.dumps(x,separators=(",",":")),flush=True)
    print("REL_HYBRID_POSITIVE",len(hpos),flush=True)
    for x in hpos[:40]:print("REL_HYBRID",json.dumps(x,separators=(",",":")),flush=True)
    print("REL_NEAREST")
    for x in near[:20]:print("REL_NEAR",json.dumps(x,separators=(",",":")),flush=True)
    print("REL_SUMMARY",json.dumps({"utc":datetime.now(timezone.utc).isoformat(),
      "markets":len(ms),"relations":len(relations),"positive":len(pos),"best":pos[:20],
      "hybrid_positive":len(hpos),"best_hybrid":hpos[:20],"nearest":near[:20]},separators=(",",":")),flush=True)

if __name__=="__main__":main()
