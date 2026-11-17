#!/usr/bin/env python3
import json
from datetime import datetime,timezone
from web3 import Web3

RPC="https://base-mainnet.g.alchemy.com/public"
w3=Web3(Web3.HTTPProvider(RPC,request_kwargs={"timeout":12}))
B0=58497214
USDC=Web3.to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")
WETH=Web3.to_checksum_address("0x4200000000000000000000000000000000000006")
PAN=Web3.to_checksum_address("0xB048Bbc1Ee6b733FFfCFb9e9CeF7375518e25997")
UNI=Web3.to_checksum_address("0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a")
GPO=Web3.to_checksum_address("0x420000000000000000000000000000000000000F")
FEE=100
QABI=[{"inputs":[{"type":"bytes","name":"path"},{"type":"uint256","name":"amountIn"}],"name":"quoteExactInput","outputs":[{"type":"uint256"},{"type":"uint160[]"},{"type":"uint32[]"},{"type":"uint256"}],"stateMutability":"nonpayable","type":"function"}]
GABI=[
 {"inputs":[{"type":"uint256"}],"name":"getL1FeeUpperBound","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
 {"inputs":[{"type":"uint256"}],"name":"getOperatorFee","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
]
pan=w3.eth.contract(PAN,abi=QABI);uni=w3.eth.contract(UNI,abi=QABI);g=w3.eth.contract(GPO,abi=GABI)
def path(a,b):return bytes.fromhex(a[2:])+FEE.to_bytes(3,"big")+bytes.fromhex(b[2:])
def quote(usd,b):
    z1=pan.functions.quoteExactInput(path(USDC,WETH),int(usd*1e6)).call(block_identifier=b)
    z2=uni.functions.quoteExactInput(path(WETH,USDC),int(z1[0])).call(block_identifier=b)
    out=int(z2[0])/1e6
    return {"block":b,"in_usdc":usd,"out_usdc":out,"gross":out-usd,"bps":(out-usd)/usd*10000,
            "gas_quotes":[int(z1[3]),int(z2[3])],"sum_quote_gas":int(z1[3])+int(z2[3])}
# size curve at exact profitable block
sizes=[1,2,5,10,20,25,50,75,100,125,150,200,250,300,400,500,750,1000,1500,2000,3000,5000]
curve=[]
for x in sizes:
    try:curve.append(quote(x,B0))
    except Exception as e:curve.append({"in_usdc":x,"error":repr(e)})
# block duration around event at $100
timeline=[]
for b in range(B0-15,B0+16):
    try:
        q=quote(100,b)
        hdr=w3.eth.get_block(b)
        q["timestamp"]=int(hdr["timestamp"])
        q["base_fee_gwei"]=int(hdr["baseFeePerGas"])/1e9
        timeline.append(q)
    except Exception as e:timeline.append({"block":b,"error":repr(e)})
# fee upper bound at event block for various execution gas; estimate ETH/USD via the known route spot roughly from previous event
# use $/ETH inferred from event's PAN/UNI spot ~1826; sufficient only for fee conversion display
eth_usd=1826.0
fee_scen=[]
for gas in [180000,220000,260000,300000,350000,400000,500000,600000]:
    try:
        hdr=w3.eth.get_block(B0); gp=int(hdr["baseFeePerGas"])
        l1=int(g.functions.getL1FeeUpperBound(400).call(block_identifier=B0))
        op=int(g.functions.getOperatorFee(gas).call(block_identifier=B0))
        total=gas*gp+l1+op
        fee_scen.append({"gas":gas,"base_fee_gwei":gp/1e9,"l1_upper_wei":l1,"operator_wei":op,
                         "fee_usd_approx":total/1e18*eth_usd})
    except Exception as e:fee_scen.append({"gas":gas,"error":repr(e)})
valid=[x for x in curve if "gross" in x]
best=max(valid,key=lambda x:x["gross"]) if valid else None
print("EVENT_REPLAY_SUMMARY",json.dumps({"utc":datetime.now(timezone.utc).isoformat(),"event_block":B0,
 "best_gross":best,"curve":curve,"timeline":timeline,"fee_scenarios":fee_scen},separators=(",",":")),flush=True)
