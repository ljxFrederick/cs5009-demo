#!/usr/bin/env python3
import json
from collections import defaultdict
from datetime import datetime,timezone
from web3 import Web3

RPC="https://base-mainnet.g.alchemy.com/public"
w3=Web3(Web3.HTTPProvider(RPC,request_kwargs={"timeout":12}))
PAN=Web3.to_checksum_address("0x72AB388E2E2F6FaceF59E3C3FA2C4E29011c2D38")
UNI=Web3.to_checksum_address("0xb4CB800910B228ED3d0834cF79D697127BBB00e5")
SWAP=Web3.keccak(text="Swap(address,address,int256,int256,uint160,uint128,int24)").hex()
pools={PAN.lower():"PAN-100",UNI.lower():"UNI-100"}
logs=w3.eth.get_logs({"fromBlock":58497210,"toBlock":58497218,"address":[PAN,UNI],"topics":[SWAP]})
rows=[]
bytx=defaultdict(list)
for L in logs:
    vals=w3.codec.decode(["int256","int256","uint160","uint128","int24"],bytes(L["data"]))
    sender="0x"+L["topics"][1].hex()[-40:]
    recipient="0x"+L["topics"][2].hex()[-40:]
    r={"block":L["blockNumber"],"tx":L["transactionHash"].hex(),"tx_index":L["transactionIndex"],
       "log_index":L["logIndex"],"pool":pools[L["address"].lower()],
       "sender":Web3.to_checksum_address(sender),"recipient":Web3.to_checksum_address(recipient),
       "amount0_weth":vals[0]/1e18,"amount1_usdc":vals[1]/1e6,
       "sqrtPriceX96":int(vals[2]),"liquidity":int(vals[3]),"tick":int(vals[4])}
    rows.append(r);bytx[r["tx"]].append(r)

txs=[]
for h,xs in bytx.items():
    rec=w3.eth.get_transaction_receipt(h);tx=w3.eth.get_transaction(h)
    touched=sorted(set(z["pool"] for z in xs))
    net_weth=-sum(z["amount0_weth"] for z in xs)
    net_usdc=-sum(z["amount1_usdc"] for z in xs)
    txs.append({"tx":h,"block":rec["blockNumber"],"tx_index":tx["transactionIndex"],
      "from":tx["from"],"to":tx["to"],"selector":tx["input"].hex()[:10] if hasattr(tx["input"],"hex") else str(tx["input"])[:10],
      "gasUsed":rec["gasUsed"],"effectiveGasPrice_gwei":int(rec["effectiveGasPrice"])/1e9,
      "status":rec["status"],"pools":touched,"swaps":len(xs),
      "net_from_these_pools":{"weth":net_weth,"usdc":net_usdc},
      "receipt_extra":{k:(v.hex() if hasattr(v,"hex") else v) for k,v in rec.items() if k in ["l1Fee","l1GasUsed","operatorFeeScalar","operatorFeeConstant"]},
      "swap_rows":xs})
txs.sort(key=lambda z:(z["block"],z["tx_index"]))
print("ARB_BLOCK_FORENSICS",json.dumps({"utc":datetime.now(timezone.utc).isoformat(),
 "swap_logs":rows,"transactions":txs,
 "multi_pool":[x for x in txs if len(x["pools"])>1]},separators=(",",":")),flush=True)
