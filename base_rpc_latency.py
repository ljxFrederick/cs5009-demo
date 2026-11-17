#!/usr/bin/env python3
import json,time,statistics,urllib.request
from datetime import datetime,timezone
ENDS={
 "sequencer":"https://mainnet-sequencer.base.org",
 "preconf":"https://mainnet-preconf.base.org",
 "alchemy_public":"https://base-mainnet.g.alchemy.com/public",
 "publicnode":"https://base-rpc.publicnode.com",
 "base_public":"https://mainnet.base.org",
}
body=json.dumps({"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}).encode()
out={}
for n,u in ENDS.items():
 xs=[];heads=[];errs=[]
 for i in range(12):
  t=time.perf_counter()
  try:
   req=urllib.request.Request(u,data=body,headers={"Content-Type":"application/json","User-Agent":"base-latency/1.0"},method="POST")
   with urllib.request.urlopen(req,timeout=5) as r:z=json.loads(r.read().decode())
   dt=(time.perf_counter()-t)*1000
   xs.append(dt)
   if z.get("result"):heads.append(int(z["result"],16))
   elif z.get("error"):errs.append(z["error"])
  except Exception as e:errs.append(repr(e))
  time.sleep(.08)
 out[n]={"n":len(xs),"median_ms":statistics.median(xs) if xs else None,
         "p90_ms":sorted(xs)[max(0,int(len(xs)*.9)-1)] if xs else None,
         "min_ms":min(xs) if xs else None,"max_ms":max(xs) if xs else None,
         "heads":heads,"max_head":max(heads) if heads else None,"errors":errs[:5]}
print("BASE_RPC_LATENCY",json.dumps({"utc":datetime.now(timezone.utc).isoformat(),"results":out},separators=(",",":")),flush=True)
