#!/usr/bin/env python3
import itertools,json,time
from datetime import datetime,timezone
from web3 import Web3

RPCS=["https://mainnet.base.org","https://base-rpc.publicnode.com","https://1rpc.io/base"]
FACTORY=Web3.to_checksum_address("0x33128a8fC17869897dcE68Ed026d694621f6FDfD")
QUOTER=Web3.to_checksum_address("0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a")
ZERO="0x0000000000000000000000000000000000000000"
TOKENS={
 "USDC":"0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
 "WETH":"0x4200000000000000000000000000000000000006",
 "cbBTC":"0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf",
 "cbETH":"0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22",
 "DAI":"0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb",
 "USDbC":"0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA",
 "EURC":"0x60a3E35Cc302bFA44Cb288Bc5a4F316Fdb1adb42",
 "AERO":"0x940181a94A35A4569E4529A3CDfB74e38FD98631",
}
TOKENS={k:Web3.to_checksum_address(v) for k,v in TOKENS.items()}
FEES=[100,500,3000,10000]
AMOUNTS=[10,100,1000,10000]

FACTORY_ABI=[{"inputs":[{"internalType":"address","name":"tokenA","type":"address"},{"internalType":"address","name":"tokenB","type":"address"},{"internalType":"uint24","name":"fee","type":"uint24"}],"name":"getPool","outputs":[{"internalType":"address","name":"pool","type":"address"}],"stateMutability":"view","type":"function"}]
QUOTER_ABI=[{"inputs":[{"internalType":"bytes","name":"path","type":"bytes"},{"internalType":"uint256","name":"amountIn","type":"uint256"}],"name":"quoteExactInput","outputs":[{"internalType":"uint256","name":"amountOut","type":"uint256"},{"internalType":"uint160[]","name":"sqrtPriceX96AfterList","type":"uint160[]"},{"internalType":"uint32[]","name":"initializedTicksCrossedList","type":"uint32[]"},{"internalType":"uint256","name":"gasEstimate","type":"uint256"}],"stateMutability":"nonpayable","type":"function"}]
ERC20_ABI=[
 {"inputs":[],"name":"decimals","outputs":[{"type":"uint8"}],"stateMutability":"view","type":"function"},
 {"inputs":[],"name":"symbol","outputs":[{"type":"string"}],"stateMutability":"view","type":"function"}
]

w3=None
for rpc in RPCS:
 try:
  z=Web3(Web3.HTTPProvider(rpc,request_kwargs={"timeout":20}))
  if z.is_connected():
   w3=z;RPC=rpc;break
 except: pass
if not w3:raise SystemExit("No Base RPC")

fac=w3.eth.contract(address=FACTORY,abi=FACTORY_ABI)
quo=w3.eth.contract(address=QUOTER,abi=QUOTER_ABI)
dec={}
valid={}
for n,a in TOKENS.items():
 try:
  c=w3.eth.contract(address=a,abi=ERC20_ABI)
  dec[n]=int(c.functions.decimals().call())
  valid[n]={"address":a,"decimals":dec[n],"symbol":c.functions.symbol().call()}
 except Exception as e:
  valid[n]={"address":a,"error":repr(e)}

pools={}
names=[n for n in TOKENS if n in dec]
for i,a in enumerate(names):
 for b in names[i+1:]:
  for f in FEES:
   try:
    p=fac.functions.getPool(TOKENS[a],TOKENS[b],f).call()
    if p.lower()!=ZERO.lower():
     pools[(a,b,f)]=p;pools[(b,a,f)]=p
   except:pass

def pairfees(a,b):
 return [f for f in FEES if (a,b,f) in pools]
def path_bytes(seq,fees):
 out=bytes.fromhex(TOKENS[seq[0]][2:])
 for f,n in zip(fees,seq[1:]):
  out+=int(f).to_bytes(3,"big")+bytes.fromhex(TOKENS[n][2:])
 return out
def quote(seq,fees,usd):
 amt=int(usd*10**dec["USDC"])
 try:
  o=quo.functions.quoteExactInput(path_bytes(seq,fees),amt).call()
  return {"amount_out":o[0]/10**dec["USDC"],"gas_estimate":int(o[3])}
 except Exception as e:return None

routes=[]
# 2-hop same pair, deliberately different fee pools.
for x in names:
 if x=="USDC":continue
 f1=pairfees("USDC",x);f2=pairfees(x,"USDC")
 for a in f1:
  for b in f2:
   if a==b:continue
   routes.append((["USDC",x,"USDC"],[a,b]))
# 3-hop triangles.
other=[x for x in names if x!="USDC"]
for x,y in itertools.permutations(other,2):
 for a in pairfees("USDC",x):
  for b in pairfees(x,y):
   for c in pairfees(y,"USDC"):
    routes.append((["USDC",x,y,"USDC"],[a,b,c]))

# dedupe exact paths
seen=set();uniq=[]
for seq,fs in routes:
 k=(tuple(seq),tuple(fs))
 if k not in seen:seen.add(k);uniq.append((seq,fs))

rows=[]
block=w3.eth.block_number
gasprice=w3.eth.gas_price
for seq,fs in uniq:
 for usd in AMOUNTS:
  q=quote(seq,fs,usd)
  if q:
   profit=q["amount_out"]-usd
   rows.append({"path":"->".join(seq),"fees":fs,"amount_in_usdc":usd,
                "amount_out_usdc":q["amount_out"],"gross_profit_usdc":profit,
                "gross_bps":profit/usd*10000,"quoter_gas":q["gas_estimate"]})
rows.sort(key=lambda z:z["gross_profit_usdc"],reverse=True)
pos=[z for z in rows if z["gross_profit_usdc"]>1e-6]
print("BASE_UNI_ARB_SUMMARY",json.dumps({
 "utc":datetime.now(timezone.utc).isoformat(),"rpc":RPC,"block":block,
 "gas_price_gwei":gasprice/1e9,"tokens":valid,
 "unique_pools":len(set(pools.values())),"pool_edges":len(pools)//2,
 "route_count":len(uniq),"quote_count":len(rows),"positive_count":len(pos),
 "positive":pos[:100],"best":rows[:100],
 "pools":[{"a":a,"b":b,"fee":f,"pool":p} for (a,b,f),p in pools.items() if a<b]
},separators=(",",":")),flush=True)
