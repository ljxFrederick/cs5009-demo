#!/usr/bin/env python3
import json
from datetime import datetime,timezone
from web3 import Web3
RPC="https://base-mainnet.g.alchemy.com/public"
w3=Web3(Web3.HTTPProvider(RPC,request_kwargs={"timeout":10}))
USDC=Web3.to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")
ICNT=Web3.to_checksum_address("0xe0cd4cAcDdCBf4f36E845407cE53E87717b6601d")
UNI=Web3.to_checksum_address("0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a")
PAN=Web3.to_checksum_address("0xB048Bbc1Ee6b733FFfCFb9e9CeF7375518e25997")
UNI_POOL=Web3.to_checksum_address("0x9331f571f79d1e186a095c93756b5680a43932ac")
PAN_POOL=Web3.to_checksum_address("0x175aa9ab06d458ad2c928c7664f0bcbd8109d54d")
FEE=500
QABI=[{"inputs":[{"type":"bytes","name":"path"},{"type":"uint256","name":"amountIn"}],"name":"quoteExactInput","outputs":[{"type":"uint256"},{"type":"uint160[]"},{"type":"uint32[]"},{"type":"uint256"}],"stateMutability":"nonpayable","type":"function"}]
EABI=[{"inputs":[],"name":"decimals","outputs":[{"type":"uint8"}],"stateMutability":"view","type":"function"},
      {"inputs":[],"name":"symbol","outputs":[{"type":"string"}],"stateMutability":"view","type":"function"}]
PABI=[{"inputs":[],"name":"slot0","outputs":[{"type":"uint160"},{"type":"int24"},{"type":"uint16"},{"type":"uint16"},{"type":"uint16"},{"type":"uint8"},{"type":"bool"}],"stateMutability":"view","type":"function"},
      {"inputs":[],"name":"fee","outputs":[{"type":"uint24"}],"stateMutability":"view","type":"function"}]
uq=w3.eth.contract(UNI,abi=QABI);pq=w3.eth.contract(PAN,abi=QABI)
uc=w3.eth.contract(UNI_POOL,abi=PABI);pc=w3.eth.contract(PAN_POOL,abi=PABI)
tc=w3.eth.contract(ICNT,abi=EABI);d=int(tc.functions.decimals().call());sym=tc.functions.symbol().call()
b=w3.eth.block_number
def path(a,b):return bytes.fromhex(a[2:])+FEE.to_bytes(3,"big")+bytes.fromhex(b[2:])
def q(c,a,z,amt):
 r=c.functions.quoteExactInput(path(a,z),amt).call(block_identifier=b)
 return {"out":int(r[0]),"gas":int(r[3]),"ticks":list(map(int,r[2]))}
rows=[]
for usd in [1,2,5,10,20,25,50,75,100,150,200,300,500,750,1000,2000]:
 for name,c1,c2 in [("UNI_TO_PAN",uq,pq),("PAN_TO_UNI",pq,uq)]:
  try:
   z1=q(c1,USDC,ICNT,int(usd*1e6))
   z2=q(c2,ICNT,USDC,z1["out"])
   out=z2["out"]/1e6;p=out-usd
   rows.append({"route":name,"in_usdc":usd,"out_usdc":out,"profit":p,"bps":p/usd*10000,
                "leg1_out_icnt":z1["out"]/(10**d),"leg1_gas":z1["gas"],"leg2_gas":z2["gas"]})
  except Exception as e: rows.append({"route":name,"in_usdc":usd,"error":repr(e)})
valid=[x for x in rows if "profit" in x];valid.sort(key=lambda x:x["profit"],reverse=True)
us=uc.functions.slot0().call(block_identifier=b);ps=pc.functions.slot0().call(block_identifier=b)
print("ICNT_ARB_SUMMARY",json.dumps({"utc":datetime.now(timezone.utc).isoformat(),"block":b,
 "token":{"symbol":sym,"decimals":d},"fees":{"uni":uc.functions.fee().call(block_identifier=b),"pan":pc.functions.fee().call(block_identifier=b)},
 "ticks":{"uni":int(us[1]),"pan":int(ps[1])},"positive_count":sum(x["profit"]>0 for x in valid),
 "rows":valid+[x for x in rows if "error" in x]},separators=(",",":")),flush=True)
