#!/usr/bin/env python3
import json
from collections import defaultdict
from datetime import datetime,timezone
from web3 import Web3

R="https://base-rpc.publicnode.com"; C="https://base-mainnet.g.alchemy.com/public"
wr=Web3(Web3.HTTPProvider(R,request_kwargs={"timeout":25}))
wc=Web3(Web3.HTTPProvider(C,request_kwargs={"timeout":12}))
PAN=Web3.to_checksum_address("0x72AB388E2E2F6FaceF59E3C3FA2C4E29011c2D38")
UNI=Web3.to_checksum_address("0xb4CB800910B228ED3d0834cF79D697127BBB00e5")
USDC=Web3.to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")
WETH=Web3.to_checksum_address("0x4200000000000000000000000000000000000006")
PANQ=Web3.to_checksum_address("0xB048Bbc1Ee6b733FFfCFb9e9CeF7375518e25997")
UNIQ=Web3.to_checksum_address("0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a")
SWAP=Web3.keccak(text="Swap(address,address,int256,int256,uint160,uint128,int24)").hex()
QABI=[{"inputs":[{"type":"bytes","name":"path"},{"type":"uint256","name":"amountIn"}],"name":"quoteExactInput","outputs":[{"type":"uint256"},{"type":"uint160[]"},{"type":"uint32[]"},{"type":"uint256"}],"stateMutability":"nonpayable","type":"function"}]
pq=wc.eth.contract(PANQ,abi=QABI);uq=wc.eth.contract(UNIQ,abi=QABI)
def num(x): return int(x,16) if isinstance(x,str) and x.startswith("0x") else int(x)
def rawsqrt(pool,b):
 raw=wc.eth.call({"to":pool,"data":Web3.keccak(text="slot0()")[:4]},block_identifier=b)
 return int.from_bytes(bytes(raw)[:32],"big")
def price(s): return (s/(2**96))**2*1e12
def path(a,b): return bytes.fromhex(a[2:])+int(100).to_bytes(3,"big")+bytes.fromhex(b[2:])
def quote(d,usd,b):
 q1,q2=(pq,uq) if d=="PAN_TO_UNI" else (uq,pq)
 z1=q1.functions.quoteExactInput(path(USDC,WETH),int(usd*1e6)).call(block_identifier=b)
 z2=q2.functions.quoteExactInput(path(WETH,USDC),int(z1[0])).call(block_identifier=b)
 out=int(z2[0])/1e6
 return {"size":usd,"out":out,"profit":out-usd,"bps":(out-usd)/usd*10000}
head=wr.eth.block_number; start=head-6000
logs=[]
for pool in (PAN,UNI):
 try:
  logs.extend(wr.eth.get_logs({"fromBlock":start,"toBlock":head,"address":pool,"topics":[SWAP]}))
 except Exception as e:
  print("ONE_SHOT_ERR",pool,repr(e),flush=True)
  for lo in range(start,head+1,1000):
   hi=min(head,lo+999)
   try: logs.extend(wr.eth.get_logs({"fromBlock":lo,"toBlock":hi,"address":pool,"topics":[SWAP]}))
   except Exception as ee: print("CHUNK_ERR",pool,lo,hi,repr(ee),flush=True)
sqrt={PAN:rawsqrt(PAN,start-1),UNI:rawsqrt(UNI,start-1)}
by=defaultdict(list)
for L in logs: by[num(L["blockNumber"])].append(L)
theory=[]
for b in sorted(by):
 for L in sorted(by[b],key=lambda x:(num(x["transactionIndex"]),num(x["logIndex"]))):
  z=wr.codec.decode(["int256","int256","uint160","uint128","int24"],bytes(L["data"]))
  sqrt[Web3.to_checksum_address(L["address"])]=int(z[2])
 f1=price(sqrt[UNI])/price(sqrt[PAN])*(.9999**2)
 f2=price(sqrt[PAN])/price(sqrt[UNI])*(.9999**2)
 if max(f1,f2)>1:
  theory.append({"block":b,"direction":"PAN_TO_UNI" if f1>=f2 else "UNI_TO_PAN","tbps":(max(f1,f2)-1)*10000})
episodes=[]
for x in theory:
 if episodes and episodes[-1]["direction"]==x["direction"] and x["block"]<=episodes[-1]["end"]+1:
  e=episodes[-1];e["end"]=x["block"];e["blocks"]+=1
  if x["tbps"]>e["peak_bps"]: e["peak_bps"]=x["tbps"];e["peak_block"]=x["block"]
 else: episodes.append({"start":x["block"],"end":x["block"],"blocks":1,"direction":x["direction"],"peak_bps":x["tbps"],"peak_block":x["block"]})
for e in episodes:
 qs=[]
 for usd in (10,25,50,75,100,125,150,200,250,300):
  try:qs.append(quote(e["direction"],usd,e["peak_block"]))
  except Exception as ex:qs.append({"size":usd,"error":repr(ex)})
 good=[q for q in qs if "profit" in q]
 e["best"]=max(good,key=lambda q:q["profit"]) if good else None
 e["actual_positive"]=any(q["profit"]>0 for q in good)
pos=[e for e in episodes if e["actual_positive"]]
print("FAST_HIST_SUMMARY",json.dumps({"utc":datetime.now(timezone.utc).isoformat(),"start":start,"head":head,
 "swap_logs":len(logs),"theory_blocks":len(theory),"episodes":len(episodes),"actual_positive":len(pos),
 "positive":pos,"all":episodes},separators=(",",":")),flush=True)
