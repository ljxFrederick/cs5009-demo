#!/usr/bin/env python3
import json,time,urllib.parse,urllib.request
from datetime import datetime,timezone

D="https://data-api.polymarket.com/v2"
UA="pm-trade-history/1.0"

TARGETS=[
 {"name":"Athletic Club NO","condition":"0x3e4d20cb627ab0bcfab0e7fb48753765b3436dd121999befd2596572a6d2dc36",
  "no_token":"647195517645297636552322833889363807010013971390938336792067770402830369875","maker":0.42},
 {"name":"Fed Jan +25 NO","condition":"0x50642b28be893e8752649e9ae6d687558fc5dcfeeecd10713cf59ef6ea0edce4",
  "no_token":"63396423986062034636625373404202970920080078602349826866595954726582462497358","maker":0.37},
]

def get(url):
    req=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=20) as r:return json.loads(r.read().decode())

def fetch_all(cond,limit_total=10000):
    rows=[];cursor=None
    while len(rows)<limit_total:
        q={"condition":cond,"limit":min(1000,limit_total-len(rows))}
        if cursor:q["cursor"]=cursor
        x=get(D+"/trades?"+urllib.parse.urlencode(q))
        data=x.get("data") or []
        rows.extend(data)
        cursor=(x.get("pagination") or {}).get("next_cursor")
        if not cursor or not data:break
    return rows

def parse_ts(x):
    for k in ("timestamp","created_at","createdAt","match_time","matchTime"):
        v=x.get(k)
        if v is None:continue
        try:
            z=float(v)
            if z>1e12:z/=1000
            return z
        except:
            try:return datetime.fromisoformat(str(v).replace("Z","+00:00")).timestamp()
            except:pass
    return None

now=time.time()
for t in TARGETS:
    try:rows=fetch_all(t["condition"])
    except Exception as e:
        print("TARGET_ERROR",t["name"],repr(e),flush=True);continue
    print("SCHEMA",t["name"],json.dumps(rows[:3],separators=(",",":"))[:12000],flush=True)
    parsed=[]
    for x in rows:
        ts=parse_ts(x)
        asset=str(x.get("asset") or x.get("asset_id") or x.get("token_id") or "")
        side=str(x.get("side") or "").upper()
        outcome=str(x.get("outcome") or "")
        try:px=float(x.get("price"))
        except:continue
        try:sz=float(x.get("size") or x.get("amount") or 0)
        except:sz=0
        parsed.append({"ts":ts,"asset":asset,"side":side,"outcome":outcome,"price":px,"size":sz,"raw":x})
    # Determine rows representing NO; support both token and outcome label.
    nr=[z for z in parsed if z["asset"]==t["no_token"] or z["outcome"].casefold()=="no"]
    # A bid at maker fills when an incoming sell trades at maker or below.
    fills=[z for z in nr if z["side"]=="SELL" and z["price"]<=t["maker"]+1e-12]
    horizons={}
    for hname,secs in [("1h",3600),("6h",21600),("24h",86400),("7d",604800),("30d",2592000)]:
        hs=[z for z in fills if z["ts"] is not None and now-z["ts"]<=secs]
        horizons[hname]={"events":len(hs),"shares":sum(z["size"] for z in hs),
                         "last_age_s":min([now-z["ts"] for z in hs],default=None)}
    all_recent=sorted(fills,key=lambda z:z["ts"] or 0,reverse=True)[:20]
    print("TRADE_HISTORY_SUMMARY",json.dumps({
      "utc":datetime.now(timezone.utc).isoformat(),"target":t["name"],"maker":t["maker"],
      "rows":len(rows),"parsed":len(parsed),"no_rows":len(nr),"potential_fill_rows":len(fills),
      "horizons":horizons,
      "recent_potential_fills":[{k:z[k] for k in ("ts","side","outcome","price","size","asset")} for z in all_recent]
    },separators=(",",":")),flush=True)
