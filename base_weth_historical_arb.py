#!/usr/bin/env python3
import json
from collections import defaultdict
from datetime import datetime,timezone
from web3 import Web3

READ="https://base-rpc.publicnode.com"
CALL="https://base-mainnet.g.alchemy.com/public"
wr=Web3(Web3.HTTPProvider(READ,request_kwargs={"timeout":15}))
wc=Web3(Web3.HTTPProvider(CALL,request_kwargs={"timeout":12}))
PAN=Web3.to_checksum_address("0x72AB388E2E2F6FaceF59E3C3FA2C4E29011c2D38")
UNI=Web3.to_checksum_address("0xb4CB800910B228ED3d0834cF79D697127BBB00e5")
USDC=Web3.to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")
WETH=Web3.to_checksum_address("0x4200000000000000000000000000000000000006")
PANQ=Web3.to_checksum_address("0xB048Bbc1Ee6b733FFfCFb9e9CeF7375518e25997")
UNIQ=Web3.to_checksum_address("0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a")
SWAP=Web3.keccak(text="Swap(address,address,int256,int256,uint160,uint128,int24)").hex()
PABI=[{"inputs":[],"name":"slot0","outputs":[{"type":"uint160"},{"type":"int24"},{"type":"uint16"},{"type":"uint16"},{"type":"uint16"},{"type":"uint8"},{"type":"bool"}],"stateMutability":"view","type":"function"}]
QABI=[{"inputs":[{"type":"bytes","name":"path"},{"type":"uint256","name":"amountIn"}],"name":"quoteExactInput","outputs":[{"type":"uint256"},{"type":"uint160[]"},{"type":"uint32[]"},{"type":"uint256"}],"stateMutability":"nonpayable","type":"function"}]
pc={PAN:wc.eth.contract(PAN,abi=PABI),UNI:wc.eth.contract(UNI,abi=PABI)}
pq=wc.eth.contract(PANQ,abi=QABI);uq=wc.eth.contract(UNIQ,abi=QABI)

head=wr.eth.block_number
N=18000
start=head-N
logs=[]
for pool in (PAN,UNI):
 for lo in range(start,head+1,600):
  hi=min(head,lo+599)
  try:logs.extend(wr.eth.get_logs({"fromBlock":lo,"toBlock":hi,"address":pool,"topics":[SWAP]}))
  except Exception as e:print("LOG_ERR",pool,lo,hi,repr(e),flush=True)

# initial state before window
sqrt={PAN:int(pc[PAN].functions.slot0().call(block_identifier=start-1)[0]),
      UNI:int(pc[UNI].functions.slot0().call(block_identifier=start-1)[0])}
byblock=defaultdict(list)
for L in logs:byblock[int(L["blockNumber"],16) if isinstance(L["blockNumber"],str) else int(L["blockNumber"])].append(L)
def price(s):return (s/(2**96))**2*1e12
def factor(buy,sell):
 # both pools 1bp
 return (price(sqrt[sell])/price(sqrt[buy]))*(1-100/1e6)*(1-100/1e6)
def path(a,b):return bytes.fromhex(a[2:])+int(100).to_bytes(3,"big")+bytes.fromhex(b[2:])
def quote(direction,usd,b):
 q1,q2=(pq,uq) if direction=="PAN_TO_UNI" else (uq,pq)
 z1=q1.functions.quoteExactInput(path(USDC,WETH),int(usd*1e6)).call(block_identifier=b)
 z2=q2.functions.quoteExactInput(path(WETH,USDC),int(z1[0])).call(block_identifier=b)
 out=int(z2[0])/1e6
 return {"size":usd,"out":out,"profit":out-usd,"bps":(out-usd)/usd*10000,
         "quote_gas":int(z1[3])+int(z2[3])}

theory=[]
for b in sorted(byblock):
 # use end-of-block state: apply all logs ordered by tx/log index
 for L in sorted(byblock[b],key=lambda x:(int(x["transactionIndex"],16) if isinstance(x["transactionIndex"],str) else int(x["transactionIndex"]),
                                          int(x["logIndex"],16) if isinstance(x["logIndex"],str) else int(x["logIndex"]))):
  z=wr.codec.decode(["int256","int256","uint160","uint128","int24"],bytes(L["data"]))
  sqrt[Web3.to_checksum_address(L["address"])]=int(z[2])
 f1=factor(PAN,UNI);f2=factor(UNI,PAN)
 if max(f1,f2)>1:
  direction="PAN_TO_UNI" if f1>=f2 else "UNI_TO_PAN"
  theory.append({"block":b,"direction":direction,"theory_bps":(max(f1,f2)-1)*10000,
                 "pan_price":price(sqrt[PAN]),"uni_price":price(sqrt[UNI])})

# consolidate consecutive positive blocks into episodes
episodes=[]
for r in theory:
 if episodes and episodes[-1]["direction"]==r["direction"] and r["block"]<=episodes[-1]["end"]+1:
  e=episodes[-1];e["end"]=r["block"];e["blocks"]+=1
  if r["theory_bps"]>e["max_theory_bps"]:e["max_theory_bps"]=r["theory_bps"];e["peak_block"]=r["block"]
 else:
  episodes.append({"start":r["block"],"end":r["block"],"blocks":1,"direction":r["direction"],
                   "max_theory_bps":r["theory_bps"],"peak_block":r["block"]})

# exact quote each episode peak, sizes around useful range
for e in episodes:
 qs=[]
 for usd in (1,5,10,25,50,75,100,125,150,200,250,300,500,750,1000):
  try:qs.append(quote(e["direction"],usd,e["peak_block"]))
  except Exception as ex:qs.append({"size":usd,"error":repr(ex)})
 good=[x for x in qs if "profit" in x]
 e["quotes"]=qs
 e["best_quote"]=max(good,key=lambda x:x["profit"]) if good else None
 e["actual_positive"]=any(x["profit"]>0 for x in good)

pos=[e for e in episodes if e["actual_positive"]]
print("WETH_HIST_ARB_SUMMARY",json.dumps({
 "utc":datetime.now(timezone.utc).isoformat(),"start":start,"head":head,"blocks_scanned":N,
 "swap_logs":len(logs),"positive_theory_blocks":len(theory),"episodes":len(episodes),
 "actual_positive_episodes":len(pos),"positive":pos,
 "all_episodes":episodes[:100]
},separators=(",",":")),flush=True)
