#!/usr/bin/env python3
import json
from web3 import Web3
from datetime import datetime,timezone
RPC="https://base-mainnet.g.alchemy.com/public"
w3=Web3(Web3.HTTPProvider(RPC,request_kwargs={"timeout":10}))
GPO=Web3.to_checksum_address("0x420000000000000000000000000000000000000F")
WETH_USDC_POOL=Web3.to_checksum_address("0xb4CB800910B228ED3d0834cF79D697127BBB00e5")
ABI=[
 {"inputs":[{"type":"uint256","name":"_unsignedTxSize"}],"name":"getL1FeeUpperBound","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
 {"inputs":[{"type":"uint256","name":"_gasUsed"}],"name":"getOperatorFee","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
 {"inputs":[],"name":"isJovian","outputs":[{"type":"bool"}],"stateMutability":"view","type":"function"},
 {"inputs":[],"name":"isIsthmus","outputs":[{"type":"bool"}],"stateMutability":"view","type":"function"},
]
PABI=[{"inputs":[],"name":"slot0","outputs":[{"type":"uint160"},{"type":"int24"},{"type":"uint16"},{"type":"uint16"},{"type":"uint16"},{"type":"uint8"},{"type":"bool"}],"stateMutability":"view","type":"function"}]
g=w3.eth.contract(GPO,abi=ABI);p=w3.eth.contract(WETH_USDC_POOL,abi=PABI)
b=w3.eth.block_number; slot=p.functions.slot0().call(block_identifier=b);sqrt=int(slot[0])
# pool token0=WETH token1=USDC: raw token1/token0 * 1e12
eth_usd=(sqrt/(2**96))**2*1e12
sizes=[120,160,200,240,300,400,600,800,1200]
gas=[150000,175000,200000,225000,250000,300000,400000]
l1={str(s):int(g.functions.getL1FeeUpperBound(s).call(block_identifier=b)) for s in sizes}
op={str(x):int(g.functions.getOperatorFee(x).call(block_identifier=b)) for x in gas}
gp=w3.eth.gas_price
rows=[]
for gu in gas:
  exec_wei=gu*gp
  for ts in (200,300,400,600):
    total=exec_wei+l1[str(ts)]+op[str(gu)]
    rows.append({"gas":gu,"tx_size":ts,"exec_usd":exec_wei/1e18*eth_usd,
                 "l1_usd":l1[str(ts)]/1e18*eth_usd,"operator_usd":op[str(gu)]/1e18*eth_usd,
                 "total_usd":total/1e18*eth_usd})
print("BASE_FEE_BOUND_SUMMARY",json.dumps({"utc":datetime.now(timezone.utc).isoformat(),"block":b,
 "gas_price_gwei":gp/1e9,"eth_usd":eth_usd,
 "is_jovian":g.functions.isJovian().call(block_identifier=b),
 "is_isthmus":g.functions.isIsthmus().call(block_identifier=b),
 "l1_fee_upper_wei":l1,"operator_fee_wei":op,"scenarios":rows},separators=(",",":")),flush=True)
