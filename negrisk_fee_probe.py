#!/usr/bin/env python3
import json, urllib.request, urllib.parse
from Crypto.Hash import keccak

G="https://gamma-api.polymarket.com"
RPCS=["https://polygon-rpc.com","https://polygon.drpc.org"]
ADAPTER="0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
SLUGS=[
"lal-ray-bil-2026-10-10",
"fed-decision-in-january-20260729233815502",
"fed-decision-in-december-20260729232808632",
]
def req(url,body=None):
    h={"User-Agent":"nr-fee-probe/1.0","Accept":"application/json"}
    data=None
    if body is not None:
        data=json.dumps(body).encode();h["Content-Type"]="application/json"
    r=urllib.request.Request(url,data=data,headers=h,method="POST" if body is not None else "GET")
    with urllib.request.urlopen(r,timeout=20) as x:return json.loads(x.read().decode())
def selector(sig):
    k=keccak.new(digest_bits=256);k.update(sig.encode());return k.hexdigest()[:8]
SEL=selector("getFeeBips(bytes32)")
for slug in SLUGS:
    e=req(G+"/events/slug/"+urllib.parse.quote(slug))
    mids=[]
    for m in e.get("markets") or []:
        for key in ("negRiskMarketID","negRiskMarketId","negRiskMarketID"):
            v=m.get(key)
            if v and v not in mids:mids.append(v)
    for key in ("negRiskMarketID","negRiskMarketId"):
        v=e.get(key)
        if v and v not in mids:mids.append(v)
    print("NR_META",json.dumps({"slug":slug,"title":e.get("title"),"event_keys":{k:e.get(k) for k in e if "risk" in k.lower() or "marketid" in k.lower()},"market_ids":mids},separators=(",",":")),flush=True)
    for mid in mids:
        hx=str(mid).lower().removeprefix("0x").rjust(64,"0")
        data="0x"+SEL+hx
        got=None;err=None
        for rpc in RPCS:
            try:
                res=req(rpc,{"jsonrpc":"2.0","id":1,"method":"eth_call","params":[{"to":ADAPTER,"data":data},"latest"]})
                if "result" in res:
                    got=int(res["result"],16);break
                err=res
            except Exception as ex:err=repr(ex)
        print("NR_FEE",json.dumps({"slug":slug,"market_id":mid,"fee_bips":got,"fee_rate":None if got is None else got/10000,"error":err if got is None else None},separators=(",",":")),flush=True)
