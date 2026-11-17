#!/usr/bin/env python3
import json,time,urllib.request
from collections import defaultdict
from datetime import datetime,timezone
from web3 import Web3

RPC="https://base-rpc.publicnode.com"
w3=Web3(Web3.HTTPProvider(RPC,request_kwargs={"timeout":15}))
SWAP=Web3.keccak(text="Swap(address,address,int256,int256,uint160,uint128,int24)").hex()
PABI=[{"inputs":[],"name":"token0","outputs":[{"type":"address"}],"stateMutability":"view","type":"function"},
      {"inputs":[],"name":"token1","outputs":[{"type":"address"}],"stateMutability":"view","type":"function"},
      {"inputs":[],"name":"fee","outputs":[{"type":"uint24"}],"stateMutability":"view","type":"function"}]
EABI=[{"inputs":[],"name":"symbol","outputs":[{"type":"string"}],"stateMutability":"view","type":"function"},
      {"inputs":[],"name":"decimals","outputs":[{"type":"uint8"}],"stateMutability":"view","type":"function"}]

pools={}
for page in range(1,7):
 u=f"https://api.geckoterminal.com/api/v2/networks/base/pools?page={page}"
 try:
  req=urllib.request.Request(u,headers={"User-Agent":"realized-arb-known-pools/1.0","Accept":"application/json"})
  with urllib.request.urlopen(req,timeout=20) as r:x=json.loads(r.read().decode())
  for p in x.get("data") or []:
   a=p.get("attributes") or {};rel=p.get("relationships") or {}
   dex=((rel.get("dex") or {}).get("data") or {}).get("id","")
   if dex not in ("uniswap-v3-base","pancakeswap-v3-base"):continue
   addr=a.get("address")
   if addr:pools[Web3.to_checksum_address(addr)]={"dex":"UNI" if dex=="uniswap-v3-base" else "PAN","name":a.get("name")}
 except Exception as e:print("GECKO_ERR",page,repr(e),flush=True)
 time.sleep(.25)

# enrich known pools
for a,m in list(pools.items()):
 try:
  c=w3.eth.contract(a,abi=PABI)
  m.update({"t0":Web3.to_checksum_address(c.functions.token0().call()),
            "t1":Web3.to_checksum_address(c.functions.token1().call()),
            "fee":int(c.functions.fee().call())})
 except Exception:pools.pop(a,None)

head=w3.eth.block_number;start=head-2500
logs=[]
addrs=list(pools)
for lo in range(start,head+1,200):
 hi=min(head,lo+199)
 for j in range(0,len(addrs),5):
  batch=addrs[j:j+5]
  try:
   logs.extend(w3.eth.get_logs({"fromBlock":lo,"toBlock":hi,"address":batch,"topics":[SWAP]}))
  except Exception as e:print("LOG_ERR",lo,hi,j,repr(e),flush=True)

bytx=defaultdict(list)
for L in logs:bytx[L["transactionHash"].hex()].append(L)
multi={h:xs for h,xs in bytx.items() if len(xs)>=2}
tokaddrs={m[k] for m in pools.values() for k in ("t0","t1")}
tm={}
for a in tokaddrs:
 try:
  c=w3.eth.contract(a,abi=EABI);tm[a]={"s":c.functions.symbol().call(),"d":int(c.functions.decimals().call())}
 except:tm[a]={"s":a[:8],"d":18}

out=[]
for h,xs in multi.items():
 flows=defaultdict(int);sw=[]
 for L in xs:
  m=pools[Web3.to_checksum_address(L["address"])]
  z=w3.codec.decode(["int256","int256","uint160","uint128","int24"],bytes(L["data"]))
  a0,a1=int(z[0]),int(z[1]);flows[m["t0"]]-=a0;flows[m["t1"]]-=a1
  sw.append({"pool":L["address"],"dex":m["dex"],"name":m["name"],"fee":m["fee"],
             "a0":str(a0),"a1":str(a1),"t0":m["t0"],"t1":m["t1"],"log":L["logIndex"]})
 non={a:v for a,v in flows.items() if v}
 closed=bool(non) and all(v>=0 for v in non.values())
 pairset={tuple(sorted((pools[Web3.to_checksum_address(L["address"])]["t0"].lower(),
                       pools[Web3.to_checksum_address(L["address"])]["t1"].lower()))) for L in xs}
 if not closed and len(pairset)!=1:continue
 rec=w3.eth.get_transaction_receipt(h);tx=w3.eth.get_transaction(h)
 pretty={}
 for a,v in non.items():
  md=tm[a];pretty[md["s"]]={"raw":str(v),"amount":v/(10**md["d"])}
 out.append({"tx":h,"block":xs[0]["blockNumber"],"tx_index":xs[0]["transactionIndex"],
             "closed":closed,"same_pair":len(pairset)==1,"venues":sorted({pools[Web3.to_checksum_address(L["address"])]["dex"] for L in xs}),
             "net":pretty,"gasUsed":rec["gasUsed"],"egp_gwei":int(rec["effectiveGasPrice"])/1e9,
             "l1Fee":int(rec.get("l1Fee") or 0),"from":tx["from"],"to":tx["to"],"swaps":sw})
out.sort(key=lambda z:(z["closed"],z["block"],z["tx_index"]),reverse=True)
print("KNOWN_POOL_REALIZED_SUMMARY",json.dumps({"utc":datetime.now(timezone.utc).isoformat(),
 "start":start,"head":head,"known_pools":len(pools),"swap_logs":len(logs),"multi_txs":len(multi),
 "candidates":out[:100]},separators=(",",":")),flush=True)
