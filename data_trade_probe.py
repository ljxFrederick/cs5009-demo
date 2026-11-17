#!/usr/bin/env python3
import json,urllib.parse,urllib.request,time
D="https://data-api.polymarket.com"
UA="data-trade-probe/1.0"
QS=[
("Rayo-Athletic Athletic NO","0x3e4d20cb627ab0bcfab0e7fb48753765b3436dd121999befd2596572a6d2dc36","647195517645297636552322833889363807010013971390938336792067770402830369875"),
("Fed Jan +25 NO","0x50642b28be893e8752649e9ae6d687558fc5dcfeeecd10713cf59ef6ea0edce4","63396423986062034636625373404202970920080078602349826866595954726582462497358"),
("Fed Jan no-change NO","0xf6e7c89ec4b5d3c887ed9b70dfbcac9392e287367783ce475d32e8c16ed9f61c","36342864926284644003283595741257595655709757799681943153551982143131732331338")
]
def get(url):
    req=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=20) as r:return json.loads(r.read().decode())
now=time.time()
for name,cid,no_token in QS:
    for suffix in ("","&side=SELL"):
        url=D+"/trades?market="+urllib.parse.quote(cid)+"&limit=500"+suffix
        try:x=get(url)
        except Exception as e:
            print("TRADE_ERR",name,suffix,repr(e),flush=True);continue
        arr=x if isinstance(x,list) else (x.get("data") or x.get("trades") or [])
        rel=[]
        for t in arr:
            tok=str(t.get("asset") or t.get("asset_id") or t.get("assetId") or "")
            if tok!=no_token:continue
            ts=t.get("timestamp") or t.get("createdAt") or t.get("created_at")
            try:
                ts=float(ts)
                if ts>1e12:ts/=1000
            except:ts=None
            rel.append({"ts":ts,"age_s":None if ts is None else now-ts,"side":t.get("side"),
                        "price":t.get("price"),"size":t.get("size"),"outcome":t.get("outcome"),
                        "tx":t.get("transactionHash") or t.get("transaction_hash")})
        rel.sort(key=lambda z:z["ts"] or 0,reverse=True)
        print("TRADE_PROBE",json.dumps({"name":name,"query_suffix":suffix,"api_count":len(arr),
          "target_no_count":len(rel),"latest_target_no":rel[:20]},separators=(",",":")),flush=True)
