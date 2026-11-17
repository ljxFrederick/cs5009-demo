#!/usr/bin/env python3
import json,urllib.request,time,statistics
from Crypto.Hash import keccak
RPCS=["https://polygon-rpc.com","https://polygon.drpc.org"]
ADAPTER="0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
UA="nr-gas-probe/1.0"

def rpc(method,params):
    body=json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}).encode()
    last=None
    for url in RPCS:
        try:
            req=urllib.request.Request(url,data=body,headers={"User-Agent":UA,"Content-Type":"application/json"},method="POST")
            with urllib.request.urlopen(req,timeout=20) as r:
                x=json.loads(r.read().decode())
            if "result" in x:return x["result"]
            last=x
        except Exception as e:last=repr(e)
    raise RuntimeError(last)

k=keccak.new(digest_bits=256);k.update(b"PositionsConverted(address,bytes32,uint256,uint256)")
TOPIC="0x"+k.hexdigest()
head=int(rpc("eth_blockNumber",[]),16)
print("GAS_HEAD",head,"TOPIC",TOPIC,flush=True)

logs=[]
# Search backwards in 2000-block chunks, cap 120k blocks.
for hi in range(head, max(-1,head-120000), -2000):
    lo=max(0,hi-1999)
    try:
        xs=rpc("eth_getLogs",[{"address":ADAPTER,"fromBlock":hex(lo),"toBlock":hex(hi),"topics":[TOPIC]}])
    except Exception as e:
        print("LOG_CHUNK_ERR",lo,hi,repr(e),flush=True);continue
    if xs:
        logs.extend(xs)
        print("LOG_CHUNK_HIT",lo,hi,len(xs),flush=True)
    if len(logs)>=20:break

rows=[]
seen=set()
for x in sorted(logs,key=lambda z:int(z["blockNumber"],16),reverse=True):
    h=x["transactionHash"]
    if h in seen:continue
    seen.add(h)
    try:
        rec=rpc("eth_getTransactionReceipt",[h])
        tx=rpc("eth_getTransactionByHash",[h])
        gas=int(rec["gasUsed"],16)
        egp=int(rec.get("effectiveGasPrice") or tx.get("gasPrice") or "0x0",16)
        cost_native=gas*egp/1e18
        rows.append({"tx":h,"block":int(rec["blockNumber"],16),"gasUsed":gas,
                     "effectiveGasPrice_gwei":egp/1e9,"cost_POL":cost_native,
                     "status":int(rec["status"],16)})
        if len(rows)>=12:break
    except Exception as e:
        print("RECEIPT_ERR",h,repr(e),flush=True)

for z in rows: print("GAS_TX",json.dumps(z,separators=(",",":")),flush=True)
if rows:
    print("GAS_SUMMARY",json.dumps({
      "n":len(rows),
      "gasUsed_median":statistics.median(z["gasUsed"] for z in rows),
      "gasUsed_min":min(z["gasUsed"] for z in rows),
      "gasUsed_max":max(z["gasUsed"] for z in rows),
      "cost_POL_median":statistics.median(z["cost_POL"] for z in rows),
      "cost_POL_min":min(z["cost_POL"] for z in rows),
      "cost_POL_max":max(z["cost_POL"] for z in rows)
    },separators=(",",":")),flush=True)
else: print("GAS_SUMMARY",json.dumps({"n":0}),flush=True)
