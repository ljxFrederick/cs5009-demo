#!/usr/bin/env python3
import json, concurrent.futures as cf
from collections import defaultdict
from datetime import datetime,timezone
from web3 import Web3

RPC="https://base-mainnet.g.alchemy.com/public"
w3=Web3(Web3.HTTPProvider(RPC,request_kwargs={"timeout":15}))
UNI_F=Web3.to_checksum_address("0x33128a8fC17869897dcE68Ed026d694621f6FDfD")
PAN_F=Web3.to_checksum_address("0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865")
SWAP=Web3.keccak(text="Swap(address,address,int256,int256,uint160,uint128,int24)").hex()
PABI=[
 {"inputs":[],"name":"factory","outputs":[{"type":"address"}],"stateMutability":"view","type":"function"},
 {"inputs":[],"name":"token0","outputs":[{"type":"address"}],"stateMutability":"view","type":"function"},
 {"inputs":[],"name":"token1","outputs":[{"type":"address"}],"stateMutability":"view","type":"function"},
 {"inputs":[],"name":"fee","outputs":[{"type":"uint24"}],"stateMutability":"view","type":"function"}]
EABI=[
 {"inputs":[],"name":"symbol","outputs":[{"type":"string"}],"stateMutability":"view","type":"function"},
 {"inputs":[],"name":"decimals","outputs":[{"type":"uint8"}],"stateMutability":"view","type":"function"}]

head=w3.eth.block_number; start=head-1600
logs=[]
for lo in range(start,head+1,200):
 hi=min(head,lo+199)
 try: logs.extend(w3.eth.get_logs({"fromBlock":lo,"toBlock":hi,"topics":[SWAP]}))
 except Exception as e: print("LOG_ERR",lo,hi,repr(e),flush=True)

bytx=defaultdict(list)
for L in logs: bytx[L["transactionHash"].hex()].append(L)
multi={h:xs for h,xs in bytx.items() if len(xs)>=2}
pooladdrs=sorted({Web3.to_checksum_address(L["address"]) for xs in multi.values() for L in xs})
meta={}
def pmeta(a):
 try:
  c=w3.eth.contract(a,abi=PABI)
  f=Web3.to_checksum_address(c.functions.factory().call())
  if f not in (UNI_F,PAN_F):return a,None
  return a,{"factory":"UNI" if f==UNI_F else "PAN","token0":Web3.to_checksum_address(c.functions.token0().call()),
            "token1":Web3.to_checksum_address(c.functions.token1().call()),"fee":int(c.functions.fee().call())}
 except:return a,None
with cf.ThreadPoolExecutor(max_workers=30) as ex:
 for a,m in ex.map(pmeta,pooladdrs): 
  if m:meta[a.lower()]=m

tokens=sorted({m[k] for m in meta.values() for k in ("token0","token1")})
tmeta={}
def tm(a):
 try:
  c=w3.eth.contract(a,abi=EABI);return a,{"symbol":c.functions.symbol().call(),"decimals":int(c.functions.decimals().call())}
 except:return a,{"symbol":a[:8],"decimals":18}
with cf.ThreadPoolExecutor(max_workers=30) as ex:
 for a,m in ex.map(tm,tokens):tmeta[a.lower()]=m

cands=[]
for h,xs in multi.items():
 known=[L for L in xs if L["address"].lower() in meta]
 if len(known)<2:continue
 venues=set(meta[L["address"].lower()]["factory"] for L in known)
 flows=defaultdict(int); swaps=[]
 for L in known:
  m=meta[L["address"].lower()]
  vals=w3.codec.decode(["int256","int256","uint160","uint128","int24"],bytes(L["data"]))
  a0,a1=int(vals[0]),int(vals[1])
  # pool delta -> trader-side net = -delta
  flows[m["token0"]]-=a0;flows[m["token1"]]-=a1
  swaps.append({"pool":Web3.to_checksum_address(L["address"]),"venue":m["factory"],"fee":m["fee"],
                "t0":m["token0"],"t1":m["token1"],"a0":str(a0),"a1":str(a1),"log":L["logIndex"]})
 nonzero={a:v for a,v in flows.items() if v}
 # closed cycle among known pools: all net flows nonnegative, some positive.
 closed=bool(nonzero) and all(v>=0 for v in nonzero.values())
 # same pair opposite-pool transactions are especially clean
 pairs={tuple(sorted((meta[L["address"].lower()]["token0"].lower(),meta[L["address"].lower()]["token1"].lower()))) for L in known}
 if closed or (len(known)>=2 and len(pairs)==1):
  try:
   rec=w3.eth.get_transaction_receipt(h);tx=w3.eth.get_transaction(h)
   gas=int(rec["gasUsed"])*int(rec["effectiveGasPrice"])
   l1=int(rec.get("l1Fee") or 0)
  except Exception: rec=None;tx=None;gas=l1=0
  pretty={}
  for a,v in nonzero.items():
   md=tmeta[a.lower()];pretty[md["symbol"]]={"raw":str(v),"amount":v/(10**md["decimals"])}
  cands.append({"tx":h,"block":known[0]["blockNumber"],"tx_index":known[0]["transactionIndex"],
                "venues":sorted(venues),"known_swaps":len(known),"all_v3_swap_logs":len(xs),
                "closed_nonnegative":closed,"pair_count":len(pairs),"net_known_flows":pretty,
                "gasUsed":None if rec is None else rec["gasUsed"],
                "effectiveGasPrice_gwei":None if rec is None else int(rec["effectiveGasPrice"])/1e9,
                "l1Fee_wei":l1,"from":None if tx is None else tx["from"],"to":None if tx is None else tx["to"],
                "swaps":swaps})
cands.sort(key=lambda x:(x["closed_nonnegative"],x["block"],x["tx_index"]),reverse=True)
print("RECENT_V3_ARB_SUMMARY",json.dumps({"utc":datetime.now(timezone.utc).isoformat(),
 "start_block":start,"head":head,"swap_logs":len(logs),"multi_tx":len(multi),
 "known_pools":len(meta),"candidate_count":len(cands),"candidates":cands[:100]},separators=(",",":")),flush=True)
