#!/usr/bin/env python3
import json,time,urllib.parse,urllib.request
from datetime import datetime,timezone

C="https://clob.polymarket.com";D="https://data-api.polymarket.com/v2"
CID="0x3e4d20cb627ab0bcfab0e7fb48753765b3436dd121999befd2596572a6d2dc36"
NO="647195517645297636552322833889363807010013971390938336792067770402830369875"
LEGS=[
 ("Rayo YES","71932326558053661020478366656945464222054464428704051104566748341121780719525",0.05),
 ("Draw YES","107428238696627085423612646047678309668925997011790108432733125831886878167315",0.05)
]
UA="athletic-unit-econ/1.0";PRICES=(0.41,0.42,0.43);SIZES=(1,5,10,20,25,35,50,75,100)

def get(url):
    req=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=20) as r:return json.loads(r.read().decode())
def bids(tok):
    x=get(C+"/book?token_id="+urllib.parse.quote(tok))
    return sorted([(float(v["price"]),float(v["size"])) for v in x.get("bids",[]) if float(v.get("size",0))>0],reverse=True)
def sell(b,qty,r):
    rem=qty;out=0.0
    for p,s in b:
        t=min(rem,s);out+=t*(p-r*p*(1-p));rem-=t
        if rem<=1e-12:return out
    return None

now=time.time()
rows=[];cursor=None
while len(rows)<5000:
    q={"condition":CID,"limit":1000}
    if cursor:q["cursor"]=cursor
    x=get(D+"/trades?"+urllib.parse.urlencode(q))
    batch=x.get("data") or []
    rows.extend(batch)
    cursor=(x.get("pagination") or {}).get("next_cursor")
    if not cursor or not batch:break
nr=[]
for z in rows:
    if str(z.get("token_id") or "")!=NO or str(z.get("side") or "").upper()!="SELL":continue
    try:ts=float(z.get("timestamp"));px=float(z.get("price"));sz=float(z.get("size") or 0)
    except:continue
    nr.append((ts,px,sz))

hist={}
for p in PRICES:
    hist[str(p)]={}
    for name,sec in [("1h",3600),("6h",21600),("24h",86400),("7d",604800),("30d",2592000)]:
        a=[z for z in nr if z[1]<=p+1e-12 and now-z[0]<=sec]
        hist[str(p)][name]={"events":len(a),"shares":sum(z[2] for z in a),
                            "notional":sum(z[1]*z[2] for z in a)}
    hist[str(p)]["recent"]=[{"age_s":now-z[0],"price":z[1],"size":z[2]} for z in sorted([z for z in nr if z[1]<=p+1e-12],reverse=True)[:20]]

legbooks=[(n,bids(t),r) for n,t,r in LEGS]
economics=[]
for p in PRICES:
    for q in SIZES:
        pro=[];total=0;ok=True
        for n,b,r in legbooks:
            z=sell(b,q,r)
            if z is None:ok=False;break
            pro.append({"leg":n,"proceeds":z});total+=z
        if ok:
            economics.append({"maker_price":p,"qty":q,"cost":p*q,"complement_net":total,
                              "profit":total-p*q,"roi":(total-p*q)/(p*q),"legs":pro})
print("ATHLETIC_UNIT_SUMMARY",json.dumps({
 "utc":datetime.now(timezone.utc).isoformat(),"trade_rows":len(rows),"sell_no_rows":len(nr),
 "history":hist,"current_leg_bids":{n:b[:8] for n,b,r in legbooks},
 "economics":economics
},separators=(",",":")),flush=True)
