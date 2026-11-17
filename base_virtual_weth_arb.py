#!/usr/bin/env python3
import json
from datetime import datetime, timezone
from web3 import Web3

RPC="https://base-mainnet.g.alchemy.com/public"
w3=Web3(Web3.HTTPProvider(RPC,request_kwargs={"timeout":10}))

WETH=Web3.to_checksum_address("0x4200000000000000000000000000000000000006")
VIRTUAL=Web3.to_checksum_address("0x0b3e328455c4059eeb9e3f84b5543f74e24e7e1b")
AERO_POOL=Web3.to_checksum_address("0x792f465e0434fb1028ea3bfeb05240cb839c4e04")
UNI_POOL=Web3.to_checksum_address("0x9c087eb773291e50cf6c6a90ef0f4500e349b903")
AERO_MIXED_V3=Web3.to_checksum_address("0xCd2A7D98e82D6107eac1828ce8DeAA6acB65b555")
UNI_QUOTER=Web3.to_checksum_address("0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a")

OLD=Web3.to_checksum_address("0x5e7BB104d84c7CB9B682AaC2F3d509f5F406809A")
MID=Web3.to_checksum_address("0xaDe65c38CD4849aDBA595a4323a8C7DdfE89716a")
NEW=Web3.to_checksum_address("0xf8f2eB4940CFE7d13603DDDD87f123820Fc061Ef")

POOL_ABI=[
 {"inputs":[],"name":"factory","outputs":[{"type":"address"}],"stateMutability":"view","type":"function"},
 {"inputs":[],"name":"token0","outputs":[{"type":"address"}],"stateMutability":"view","type":"function"},
 {"inputs":[],"name":"token1","outputs":[{"type":"address"}],"stateMutability":"view","type":"function"},
 {"inputs":[],"name":"tickSpacing","outputs":[{"type":"int24"}],"stateMutability":"view","type":"function"},
 {"inputs":[],"name":"fee","outputs":[{"type":"uint24"}],"stateMutability":"view","type":"function"},
 {"inputs":[],"name":"slot0","outputs":[{"type":"uint160"},{"type":"int24"},{"type":"uint16"},{"type":"uint16"},{"type":"uint16"},{"type":"uint8"},{"type":"bool"}],"stateMutability":"view","type":"function"},
]
ERC20=[{"inputs":[],"name":"decimals","outputs":[{"type":"uint8"}],"stateMutability":"view","type":"function"},
       {"inputs":[],"name":"symbol","outputs":[{"type":"string"}],"stateMutability":"view","type":"function"}]
QABI=[{"inputs":[{"type":"bytes","name":"path"},{"type":"uint256","name":"amountIn"}],"name":"quoteExactInput",
       "outputs":[{"type":"uint256"},{"type":"uint160[]"},{"type":"uint32[]"},{"type":"uint256"}],
       "stateMutability":"nonpayable","type":"function"}]

ap=w3.eth.contract(AERO_POOL,abi=POOL_ABI)
up=w3.eth.contract(UNI_POOL,abi=POOL_ABI)
aq=w3.eth.contract(AERO_MIXED_V3,abi=QABI)
uq=w3.eth.contract(UNI_QUOTER,abi=QABI)
vc=w3.eth.contract(VIRTUAL,abi=ERC20)

block=w3.eth.block_number
factory=Web3.to_checksum_address(ap.functions.factory().call(block_identifier=block))
tick=int(ap.functions.tickSpacing().call(block_identifier=block))
try:aero_fee=int(ap.functions.fee().call(block_identifier=block))
except Exception as e:aero_fee=None
uni_fee=int(up.functions.fee().call(block_identifier=block))
vdec=int(vc.functions.decimals().call(block_identifier=block))
vsym=vc.functions.symbol().call(block_identifier=block)
a0=Web3.to_checksum_address(ap.functions.token0().call(block_identifier=block))
a1=Web3.to_checksum_address(ap.functions.token1().call(block_identifier=block))
u0=Web3.to_checksum_address(up.functions.token0().call(block_identifier=block))
u1=Web3.to_checksum_address(up.functions.token1().call(block_identifier=block))

if factory==NEW: encoded=tick | 0x80000
elif factory==OLD: encoded=tick | 0x100000
elif factory==MID: encoded=tick
else: encoded=tick

def p3(a,b,x):
    return bytes.fromhex(a[2:])+int(x).to_bytes(3,"big",signed=False)+bytes.fromhex(b[2:])

def quote_aero(token_in,token_out,amount):
    z=aq.functions.quoteExactInput(p3(token_in,token_out,encoded),amount).call(block_identifier=block)
    return {"out":int(z[0]),"gas":int(z[3]),"sqrt_after":int(z[1][0]) if z[1] else None,
            "ticks_crossed":int(z[2][0]) if z[2] else None}
def quote_uni(token_in,token_out,amount):
    z=uq.functions.quoteExactInput(p3(token_in,token_out,uni_fee),amount).call(block_identifier=block)
    return {"out":int(z[0]),"gas":int(z[3]),"sqrt_after":int(z[1][0]) if z[1] else None,
            "ticks_crossed":int(z[2][0]) if z[2] else None}

amounts=[0.0001,0.0005,0.001,0.002,0.005,0.01,0.02,0.05,0.1,0.25]
rows=[]
for eth in amounts:
    raw=int(eth*1e18)
    for direction in ("AERO_TO_UNI","UNI_TO_AERO"):
        try:
            if direction=="AERO_TO_UNI":
                z1=quote_aero(WETH,VIRTUAL,raw)
                z2=quote_uni(VIRTUAL,WETH,z1["out"])
            else:
                z1=quote_uni(WETH,VIRTUAL,raw)
                z2=quote_aero(VIRTUAL,WETH,z1["out"])
            out=z2["out"]/1e18
            profit=out-eth
            rows.append({"direction":direction,"in_weth":eth,"out_weth":out,"profit_weth":profit,
                         "gross_bps":profit/eth*10000,"leg1":z1,"leg2":z2})
        except Exception as e:
            rows.append({"direction":direction,"in_weth":eth,"error":repr(e)})

valid=[x for x in rows if "profit_weth" in x]
valid.sort(key=lambda z:z["profit_weth"],reverse=True)
print("VIRTUAL_ARB_SUMMARY",json.dumps({
 "utc":datetime.now(timezone.utc).isoformat(),"block":block,"gas_price_gwei":w3.eth.gas_price/1e9,
 "virtual":{"symbol":vsym,"decimals":vdec,"address":VIRTUAL},
 "aero":{"pool":AERO_POOL,"factory":factory,"tick_spacing":tick,"encoded_spacing":encoded,
         "fee_read":aero_fee,"token0":a0,"token1":a1,
         "mixed_quoter_v3":AERO_MIXED_V3},
 "uni":{"pool":UNI_POOL,"fee":uni_fee,"token0":u0,"token1":u1,
        "quoter":UNI_QUOTER},
 "positive_count":sum(x["profit_weth"]>0 for x in valid),
 "rows":valid+ [x for x in rows if "error" in x]
},separators=(",",":")),flush=True)
