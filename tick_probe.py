#!/usr/bin/env python3
import json,urllib.parse,urllib.request
C="https://clob.polymarket.com"
T=[
("Athletic NO","647195517645297636552322833889363807010013971390938336792067770402830369875"),
("Fed Jan +25 NO","63396423986062034636625373404202970920080078602349826866595954726582462497358")
]
def get(url):
    req=urllib.request.Request(url,headers={"User-Agent":"tick-probe/1.0","Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=15) as r:return json.loads(r.read().decode())
for name,t in T:
    tick=get(C+"/tick-size?token_id="+urllib.parse.quote(t))
    book=get(C+"/book?token_id="+urllib.parse.quote(t))
    bids=sorted([(float(x["price"]),float(x["size"])) for x in book.get("bids",[])],reverse=True)
    asks=sorted([(float(x["price"]),float(x["size"])) for x in book.get("asks",[])])
    print("TICK_RESULT",json.dumps({"name":name,"tick":tick,"top_bids":bids[:8],"top_asks":asks[:8]},separators=(",",":")),flush=True)
