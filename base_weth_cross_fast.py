#!/usr/bin/env python3
from web3 import Web3
import json
from datetime import datetime,timezone
RPC="https://mainnet.base.org"
w3=Web3(Web3.HTTPProvider(RPC,request_kwargs={"timeout":8}))
USDC=Web3.to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")
WETH=Web3.to_checksum_address("0x4200000000000000000000000000000000000006")
ZERO="0x0000000000000000000000000000000000000000"
FEES=[100,500,3000,10000]
FABI=[{"inputs":[{"type":"address","name":"tokenA"},{"type":"address","name":"tokenB"},{"type":"uint24","name":"fee"}],"name":"getPool","outputs":[{"type":"address"}],"stateMutability":"view","type":"function"}]
QABI=[{"inputs":[{"type":"bytes","name":"path"},{"type":"uint256","name":"amountIn"}],"name":"quoteExactInput","outputs":[{"type":"uint256"},{"type":"uint160[]"},{"type":"uint32[]"},{"type":"uint256"}],"stateMutability":"nonpayable","type":"function"}]
AABI=[{"inputs":[{"type":"uint256","name":"amountIn"},{"components":[{"type":"address","name":"from"},{"type":"address","name":"to"},{"type":"bool","name":"stable"},{"type":"address","name":"factory"}],"type":"tuple[]","name":"routes"}],"name":"getAmountsOut","outputs":[{"type":"uint256[]"}],"stateMutability":"view","type":"function"}]
PABI=[{"inputs":[],"name":"FLASHLOAN_PREMIUM_TOTAL","outputs":[{"type":"uint128"}],"stateMutability":"view","type":"function"}]
cfg={
"UNI":("0x33128a8fC17869897dcE68Ed026d694621f6FDfD","0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a"),
"PAN":("0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865","0xB048Bbc1Ee6b733FFfCFb9e9CeF7375518e25997")}
fs={k:w3.eth.contract(Web3.to_checksum_address(v[0]),abi=FABI) for k,v in cfg.items()}
qs={k:w3.eth.contract(Web3.to_checksum_address(v[1]),abi=QABI) for k,v in cfg.items()}
aero=w3.eth.contract(Web3.to_checksum_address("0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43"),abi=AABI)
pool=w3.eth.contract(Web3.to_checksum_address("0xA238Dd80C259a72e81d7e4664a9801593F98d1c5"),abi=PABI)
block=w3.eth.block_number
def path(a,b,f):return bytes.fromhex(a[2:])+int(f).to_bytes(3,"big")+bytes.fromhex(b[2:])
def v3(v,a,b,x):
 best=None
 for f in FEES:
  try:
   p=fs[v].functions.getPool(a,b,f).call(block_identifier=block)
   if p.lower()==ZERO.lower():continue
   z=qs[v].functions.quoteExactInput(path(a,b,f),x).call(block_identifier=block)
   r={"out":int(z[0]),"fee":f,"pool":p,"gas":int(z[3])}
   if best is None or r["out"]>best["out"]:best=r
  except:pass
 return best
def ar(a,b,x):
 best=None
 for stable in (False,True):
  try:
   z=aero.functions.getAmountsOut(x,[(a,b,stable,Web3.to_checksum_address(ZERO))]).call(block_identifier=block)
   r={"out":int(z[-1]),"stable":stable}
   if r["out"] and (best is None or r["out"]>best["out"]):best=r
  except:pass
 return best
def q(v,a,b,x):return v3(v,a,b,x) if v!="AERO" else ar(a,b,x)
rows=[]
for usd in (100,1000):
 for v1 in ("UNI","PAN","AERO"):
  z1=q(v1,USDC,WETH,int(usd*1e6))
  if not z1:continue
  for v2 in ("UNI","PAN","AERO"):
   if v2==v1:continue
   z2=q(v2,WETH,USDC,z1["out"])
   if not z2:continue
   out=z2["out"]/1e6
   rows.append({"usd":usd,"route":v1+"->"+v2,"out":out,"profit":out-usd,"bps":(out-usd)/usd*10000,"leg1":z1,"leg2":z2})
rows.sort(key=lambda x:x["profit"],reverse=True)
print("WETH_CROSS_SUMMARY",json.dumps({"utc":datetime.now(timezone.utc).isoformat(),"block":block,"gas_gwei":w3.eth.gas_price/1e9,"aave_flash_bps":int(pool.functions.FLASHLOAN_PREMIUM_TOTAL().call(block_identifier=block)),"rows":rows},separators=(",",":")),flush=True)
