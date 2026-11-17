#!/usr/bin/env python3
import json,urllib.parse,urllib.request
from datetime import datetime,timezone

D="https://data-api.polymarket.com/v2"
UA="athletic-historical-hedge/1.0"
TARGETS=[
 {"ts":1794861893.0,"no_price":0.40,"no_size":35.46},
 {"ts":1794859396.0,"no_price":0.4073855244,"no_size":6.77},
 {"ts":1794837215.0,"no_price":0.41,"no_size":15.87},
]
LEGS=[
 {"name":"Rayo YES","cid":"0xa0996a2e100ea502040335bf116a5ce635fa214d25b8ecd6e8eeb0d1541575de",
  "token":"71932326558053661020478366656945464222054464428704051104566748341121780719525","fee":0.05},
 {"name":"Draw YES","cid":"0x5a5587793833810ba3e06d42174c0adfacf00c63037a2a10a487096b8c206eaf",
  "token":"107428238696627085423612646047678309668925997011790108432733125831886878167315","fee":0.05}
]
def get(url):
    req=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=20) as r:return json.loads(r.read().decode())
def alltr(cid):
    rows=[];cur=None
    while len(rows)<5000:
        q={"condition":cid,"limit":1000}
        if cur:q["cursor"]=cur
        x=get(D+"/trades?"+urllib.parse.urlencode(q));b=x.get("data") or []
        rows.extend(b);cur=(x.get("pagination") or {}).get("next_cursor")
        if not cur or not b:break
    return rows
for leg in LEGS:
    rows=alltr(leg["cid"]); xs=[]
    for z in rows:
        if str(z.get("token_id") or "")!=leg["token"]:continue
        try:ts=float(z.get("timestamp"));px=float(z.get("price"));sz=float(z.get("size") or 0)
        except:continue
        xs.append({"ts":ts,"price":px,"size":sz,"side":str(z.get("side") or "").upper()})
    leg["trades"]=xs
    print("LEG_HISTORY",leg["name"],"rows",len(rows),"yes_trades",len(xs),flush=True)

out=[]
for t in TARGETS:
    rec={"target":t,"legs":[]}
    total_sell_proxy=0;proxy_ok=True
    for leg in LEGS:
        sells=[z for z in leg["trades"] if z["side"]=="SELL"]
        anyt=leg["trades"]
        near_sell=min(sells,key=lambda z:abs(z["ts"]-t["ts"])) if sells else None
        near_any=min(anyt,key=lambda z:abs(z["ts"]-t["ts"])) if anyt else None
        before=[z for z in sells if z["ts"]<=t["ts"]]
        after=[z for z in sells if z["ts"]>=t["ts"]]
        bef=max(before,key=lambda z:z["ts"]) if before else None
        aft=min(after,key=lambda z:z["ts"]) if after else None
        def pack(z):
            if not z:return None
            return {**z,"dt_s":z["ts"]-t["ts"],
                    "net_sell_per_share":z["price"]-leg["fee"]*z["price"]*(1-z["price"])}
        pnear=pack(near_sell); pany=pack(near_any); pb=pack(bef); pa=pack(aft)
        rec["legs"].append({"name":leg["name"],"nearest_sell":pnear,"nearest_any":pany,
                            "prev_sell":pb,"next_sell":pa})
        # Use nearest SELL only if within 10 minutes as a coarse historical bid proxy.
        if pnear and abs(pnear["dt_s"])<=600: total_sell_proxy+=pnear["net_sell_per_share"]
        else:proxy_ok=False
    rec["coarse_10m_proxy_sum"]=total_sell_proxy if proxy_ok else None
    rec["coarse_profit_vs_actual_fill"]=total_sell_proxy-t["no_price"] if proxy_ok else None
    out.append(rec)
print("HIST_HEDGE_SUMMARY",json.dumps({"utc":datetime.now(timezone.utc).isoformat(),"cases":out},separators=(",",":")),flush=True)
