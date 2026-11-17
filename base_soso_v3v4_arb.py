#!/usr/bin/env python3
import json
from datetime import datetime,timezone
from web3 import Web3

RPC="https://base-mainnet.g.alchemy.com/public"
w3=Web3(Web3.HTTPProvider(RPC,request_kwargs={"timeout":12}))
POOLMAN=Web3.to_checksum_address("0x498581ff718922c3f8e6a244956af099b2652b2b")
V4Q=Web3.to_checksum_address("0x0d5e0f971ed27fbff6c2837bf31316121532048d")
V3Q=Web3.to_checksum_address("0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a")
V3POOL=Web3.to_checksum_address("0x29183f918920a2aef0115a9c7374945589968aea")
POOLID="0x636da53e0251b3cb0b4d10dbaff488b294f97bd5212b7e2a7c1bc3fb57fd6e31"
SOSO=Web3.to_checksum_address("0x624e2e7fdc8903165f64891672267ab0fcb98831")
USDC=Web3.to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")

# PoolKey verified from current Uniswap routing metadata for this exact V4 pool.
c0=SOSO
c1=USDC
fee=1
tickSpacing=1
hooks=Web3.to_checksum_address("0x0000000000000000000000000000000000000000")

PABI=[{"inputs":[],"name":"fee","outputs":[{"type":"uint24"}],"stateMutability":"view","type":"function"}]
EABI=[{"inputs":[],"name":"decimals","outputs":[{"type":"uint8"}],"stateMutability":"view","type":"function"},
      {"inputs":[],"name":"symbol","outputs":[{"type":"string"}],"stateMutability":"view","type":"function"}]
V3ABI=[{"inputs":[{"type":"bytes","name":"path"},{"type":"uint256","name":"amountIn"}],"name":"quoteExactInput",
        "outputs":[{"type":"uint256"},{"type":"uint160[]"},{"type":"uint32[]"},{"type":"uint256"}],
        "stateMutability":"nonpayable","type":"function"}]
V4ABI=[{"inputs":[{"components":[{"components":[{"type":"address","name":"currency0"},{"type":"address","name":"currency1"},{"type":"uint24","name":"fee"},{"type":"int24","name":"tickSpacing"},{"type":"address","name":"hooks"}],"type":"tuple","name":"poolKey"},{"type":"bool","name":"zeroForOne"},{"type":"uint128","name":"exactAmount"},{"type":"bytes","name":"hookData"}],"type":"tuple","name":"params"}],
        "name":"quoteExactInputSingle","outputs":[{"type":"uint256","name":"amountOut"},{"type":"uint256","name":"gasEstimate"}],
        "stateMutability":"nonpayable","type":"function"}]
v3=w3.eth.contract(V3Q,abi=V3ABI);v4=w3.eth.contract(V4Q,abi=V4ABI)
p3=w3.eth.contract(V3POOL,abi=PABI);tc=w3.eth.contract(SOSO,abi=EABI)
v3fee=int(p3.functions.fee().call());dec=int(tc.functions.decimals().call());sym=tc.functions.symbol().call()
block=w3.eth.block_number

def path(a,b,f):return bytes.fromhex(a[2:])+int(f).to_bytes(3,"big")+bytes.fromhex(b[2:])
def q3(a,b,amt):
 z=v3.functions.quoteExactInput(path(a,b,v3fee),amt).call(block_identifier=block)
 return int(z[0]),int(z[3])
def q4(a,b,amt):
 zero=(a.lower()==c0.lower() and b.lower()==c1.lower())
 if not zero and not (a.lower()==c1.lower() and b.lower()==c0.lower()):raise ValueError("pair mismatch")
 key=(c0,c1,int(fee),int(tickSpacing),hooks)
 z=v4.functions.quoteExactInputSingle((key,zero,int(amt),b"")).call(block_identifier=block)
 return int(z[0]),int(z[1])

rows=[]
for usd in [1,2,5,10,20,25,50,75,100,150,200,300,500,750,1000]:
 for direction in ("V3_TO_V4","V4_TO_V3"):
  try:
   if direction=="V3_TO_V4":
    t,g1=q3(USDC,SOSO,int(usd*1e6)); out,g2=q4(SOSO,USDC,t)
   else:
    t,g1=q4(USDC,SOSO,int(usd*1e6)); out,g2=q3(SOSO,USDC,t)
   u=out/1e6;p=u-usd
   rows.append({"direction":direction,"in_usdc":usd,"out_usdc":u,"profit":p,"bps":p/usd*10000,
                "mid_soso":t/(10**dec),"gas1":g1,"gas2":g2})
  except Exception as e:rows.append({"direction":direction,"in_usdc":usd,"error":repr(e)})
valid=[x for x in rows if "profit" in x];valid.sort(key=lambda x:x["profit"],reverse=True)
print("SOSO_V3V4_SUMMARY",json.dumps({
 "utc":datetime.now(timezone.utc).isoformat(),"block":block,
 "token":{"symbol":sym,"decimals":dec},"v3":{"pool":V3POOL,"fee":v3fee},
 "v4":{"pool_id":POOLID,"currency0":c0,"currency1":c1,
       "fee":int(fee),"tickSpacing":int(tickSpacing),"hooks":hooks},
 "positive_count":sum(x["profit"]>0 for x in valid),"rows":valid+[x for x in rows if "error" in x]
},separators=(",",":")),flush=True)
