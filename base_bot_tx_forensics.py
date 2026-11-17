#!/usr/bin/env python3
import json
from web3 import Web3
from datetime import datetime,timezone

RPC="https://base-mainnet.g.alchemy.com/public"
w3=Web3(Web3.HTTPProvider(RPC,request_kwargs={"timeout":12}))
TX="0xc0057b97a59225446b63475baeb99c78e8cf5b5bbbe820bcb85e8653f7beabb3"
EXEC="0x08FB33543aD633db94282df364AD80FEB8D9fc35"
WETH=Web3.to_checksum_address("0x4200000000000000000000000000000000000006")
USDC=Web3.to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")
SWAPV3=Web3.keccak(text="Swap(address,address,int256,int256,uint160,uint128,int24)").hex()
TRANSFER=Web3.keccak(text="Transfer(address,address,uint256)").hex()

MIN_POOL_ABI=[
 {"inputs":[],"name":"token0","outputs":[{"type":"address"}],"stateMutability":"view","type":"function"},
 {"inputs":[],"name":"token1","outputs":[{"type":"address"}],"stateMutability":"view","type":"function"},
 {"inputs":[],"name":"fee","outputs":[{"type":"uint24"}],"stateMutability":"view","type":"function"},
 {"inputs":[],"name":"factory","outputs":[{"type":"address"}],"stateMutability":"view","type":"function"},
 {"inputs":[],"name":"tickSpacing","outputs":[{"type":"int24"}],"stateMutability":"view","type":"function"},
]
r=w3.eth.get_transaction_receipt(TX);tx=w3.eth.get_transaction(TX);block=r["blockNumber"]
swaps=[];transfers=[];unknown=[]
for L in r["logs"]:
    if not L["topics"]:continue
    t0=L["topics"][0].hex()
    if t0==SWAPV3:
        vals=w3.codec.decode(["int256","int256","uint160","uint128","int24"],bytes(L["data"]))
        addr=Web3.to_checksum_address(L["address"])
        c=w3.eth.contract(addr,abi=MIN_POOL_ABI)
        meta={"address":addr}
        for fn in ["token0","token1","fee","factory","tickSpacing"]:
            try:meta[fn]=getattr(c.functions,fn)().call(block_identifier=block)
            except Exception as e:meta[fn]=None
        swaps.append({"meta":meta,"amount0":str(vals[0]),"amount1":str(vals[1]),
                      "sqrt":str(vals[2]),"liq":str(vals[3]),"tick":vals[4],"log_index":L["logIndex"]})
    elif t0==TRANSFER and L["address"].lower() in (WETH.lower(),USDC.lower()):
        frm="0x"+L["topics"][1].hex()[-40:];to="0x"+L["topics"][2].hex()[-40:]
        val=int.from_bytes(bytes(L["data"]),"big")
        token="WETH" if L["address"].lower()==WETH.lower() else "USDC"
        transfers.append({"token":token,"from":Web3.to_checksum_address(frm),"to":Web3.to_checksum_address(to),
                          "value_raw":str(val),"value":val/(1e18 if token=="WETH" else 1e6),"log_index":L["logIndex"]})
# net flows for executor and tx sender
nets={}
for who in [EXEC,tx["from"]]:
    x={"WETH":0.0,"USDC":0.0}
    for t in transfers:
        if t["to"].lower()==who.lower():x[t["token"]]+=t["value"]
        if t["from"].lower()==who.lower():x[t["token"]]-=t["value"]
    nets[who]=x
print("BOT_TX_FORENSICS",json.dumps({"utc":datetime.now(timezone.utc).isoformat(),
 "tx":{"hash":TX,"block":block,"index":tx["transactionIndex"],"from":tx["from"],"to":tx["to"],
       "gasUsed":r["gasUsed"],"effectiveGasPrice":int(r["effectiveGasPrice"]),"input":tx["input"].hex()},
 "swaps":swaps,"transfers":transfers,"net_flows":nets,
 "receipt_extra":{k:(v.hex() if hasattr(v,"hex") else v) for k,v in r.items() if k in ["l1Fee","l1GasUsed","operatorFeeScalar","operatorFeeConstant"]}},
 separators=(",",":")),flush=True)
