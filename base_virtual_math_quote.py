#!/usr/bin/env python3
import json, math
from datetime import datetime, timezone
from web3 import Web3

RPC="https://base-mainnet.g.alchemy.com/public"
w3=Web3(Web3.HTTPProvider(RPC,request_kwargs={"timeout":10}))
Q96=1<<96
FEE_DEN=1_000_000

WETH=Web3.to_checksum_address("0x4200000000000000000000000000000000000006")
VIRTUAL=Web3.to_checksum_address("0x0b3e328455c4059eeb9e3f84b5543f74e24e7e1b")
AERO_POOL=Web3.to_checksum_address("0x792f465e0434fb1028ea3bfeb05240cb839c4e04")
UNI_POOL=Web3.to_checksum_address("0x9c087eb773291e50cf6c6a90ef0f4500e349b903")
UNI_QUOTER=Web3.to_checksum_address("0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a")

AABI=[
 {"inputs":[],"name":"token0","outputs":[{"type":"address"}],"stateMutability":"view","type":"function"},
 {"inputs":[],"name":"token1","outputs":[{"type":"address"}],"stateMutability":"view","type":"function"},
 {"inputs":[],"name":"tickSpacing","outputs":[{"type":"int24"}],"stateMutability":"view","type":"function"},
 {"inputs":[],"name":"fee","outputs":[{"type":"uint24"}],"stateMutability":"view","type":"function"},
 {"inputs":[],"name":"liquidity","outputs":[{"type":"uint128"}],"stateMutability":"view","type":"function"},
 {"inputs":[],"name":"slot0","outputs":[{"type":"uint160"},{"type":"int24"},{"type":"uint16"},{"type":"uint16"},{"type":"uint16"},{"type":"bool"}],"stateMutability":"view","type":"function"},
]
UABI=[
 {"inputs":[],"name":"fee","outputs":[{"type":"uint24"}],"stateMutability":"view","type":"function"},
 {"inputs":[],"name":"slot0","outputs":[{"type":"uint160"},{"type":"int24"},{"type":"uint16"},{"type":"uint16"},{"type":"uint16"},{"type":"uint8"},{"type":"bool"}],"stateMutability":"view","type":"function"},
]
QABI=[{"inputs":[{"type":"bytes","name":"path"},{"type":"uint256","name":"amountIn"}],"name":"quoteExactInput",
       "outputs":[{"type":"uint256"},{"type":"uint160[]"},{"type":"uint32[]"},{"type":"uint256"}],
       "stateMutability":"nonpayable","type":"function"}]

ap=w3.eth.contract(AERO_POOL,abi=AABI)
up=w3.eth.contract(UNI_POOL,abi=UABI)
uq=w3.eth.contract(UNI_QUOTER,abi=QABI)
block=w3.eth.block_number
sqrtP,tick,*_=ap.functions.slot0().call(block_identifier=block)
sqrtP=int(sqrtP);tick=int(tick)
L=int(ap.functions.liquidity().call(block_identifier=block))
fee=int(ap.functions.fee().call(block_identifier=block))
spacing=int(ap.functions.tickSpacing().call(block_identifier=block))
uni_fee=int(up.functions.fee().call(block_identifier=block))
uni_sqrt=int(up.functions.slot0().call(block_identifier=block)[0])

def ceildiv(a,b): return (a+b-1)//b
def amount0_delta(sa,sb,liq,round_up):
    if sa>sb: sa,sb=sb,sa
    n1=liq<<96
    n2=sb-sa
    if round_up:
        return ceildiv(ceildiv(n1*n2,sb),sa)
    return (n1*n2//sb)//sa
def amount1_delta(sa,sb,liq,round_up):
    if sa>sb:sa,sb=sb,sa
    n=liq*(sb-sa)
    return ceildiv(n,Q96) if round_up else n//Q96
def next_from_amount1(sp,liq,amt,add):
    if add:
        return sp + ((amt<<96)//liq)
    q=ceildiv(amt<<96,liq)
    if sp<=q: raise ValueError("sqrt underflow")
    return sp-q
def next_from_amount0(sp,liq,amt,add):
    n1=liq<<96
    if add:
        product=amt*sp
        return ceildiv(n1*sp,n1+product)
    product=amt*sp
    if product>=n1: raise ValueError("denom")
    return ceildiv(n1*sp,n1-product)

def aero_exact_in(token_in,amount):
    net=amount*(FEE_DEN-fee)//FEE_DEN
    if token_in==WETH:
        # token1 in, token0(VIRTUAL) out, sqrt increases
        sq=next_from_amount1(sqrtP,L,net,True)
        out=amount0_delta(sqrtP,sq,L,False)
        direction="oneForZero"
    elif token_in==VIRTUAL:
        # token0 in, token1(WETH) out, sqrt decreases
        sq=next_from_amount0(sqrtP,L,net,True)
        out=amount1_delta(sq,sqrtP,L,False)
        direction="zeroForOne"
    else: raise ValueError("token")
    return out,sq,direction

def tick_sqrt_approx(t):
    return int((1.0001**(t/2.0))*Q96)
def safe_no_tick_cross(sq,direction):
    if direction=="oneForZero":
        boundary=(math.floor(tick/spacing)+1)*spacing
        return sq < tick_sqrt_approx(boundary),boundary
    else:
        boundary=(math.ceil(tick/spacing)-1)*spacing
        return sq > tick_sqrt_approx(boundary),boundary
def path(a,b,f):
    return bytes.fromhex(a[2:])+int(f).to_bytes(3,"big")+bytes.fromhex(b[2:])
def uni_quote(a,b,amt):
    z=uq.functions.quoteExactInput(path(a,b,uni_fee),amt).call(block_identifier=block)
    return int(z[0]),int(z[3])

aero_spot=(sqrtP/Q96)**2
uni_spot=(uni_sqrt/Q96)**2
rows=[]
for eth in [0.00005,0.0001,0.00025,0.0005,0.001,0.002,0.005,0.01,0.02,0.05,0.1,0.25]:
    raw=int(eth*1e18)
    try:
        virt,sq,d=aero_exact_in(WETH,raw)
        safe,bound=safe_no_tick_cross(sq,d)
        weth_out,ugas=uni_quote(VIRTUAL,WETH,virt)
        out=weth_out/1e18;profit=out-eth
        rows.append({"direction":"AERO_TO_UNI","in_weth":eth,"out_weth":out,"profit_weth":profit,
                     "gross_bps":profit/eth*10000,"aero_out_virtual":virt/1e18,
                     "no_tick_cross":safe,"boundary_tick":bound,"sqrt_after":sq,"uni_quote_gas":ugas})
    except Exception as e:
        rows.append({"direction":"AERO_TO_UNI","in_weth":eth,"error":repr(e)})
    try:
        virt,ugas=uni_quote(WETH,VIRTUAL,raw)
        weth_out,sq,d=aero_exact_in(VIRTUAL,virt)
        safe,bound=safe_no_tick_cross(sq,d)
        out=weth_out/1e18;profit=out-eth
        rows.append({"direction":"UNI_TO_AERO","in_weth":eth,"out_weth":out,"profit_weth":profit,
                     "gross_bps":profit/eth*10000,"uni_out_virtual":virt/1e18,
                     "no_tick_cross":safe,"boundary_tick":bound,"sqrt_after":sq,"uni_quote_gas":ugas})
    except Exception as e:
        rows.append({"direction":"UNI_TO_AERO","in_weth":eth,"error":repr(e)})

valid=[x for x in rows if "profit_weth" in x]
valid.sort(key=lambda z:z["profit_weth"],reverse=True)
print("VIRTUAL_MATH_SUMMARY",json.dumps({
 "utc":datetime.now(timezone.utc).isoformat(),"block":block,"gas_price_gwei":w3.eth.gas_price/1e9,
 "aero":{"sqrtP":sqrtP,"tick":tick,"liquidity":L,"fee_pips":fee,"fee_bps":fee/100,
         "tick_spacing":spacing,"spot_weth_per_virtual":aero_spot},
 "uni":{"sqrtP":uni_sqrt,"fee_pips":uni_fee,"fee_bps":uni_fee/100,
        "spot_weth_per_virtual":uni_spot},
 "spot_spread_bps":(uni_spot/aero_spot-1)*10000,
 "positive_count":sum(x["profit_weth"]>0 and x["no_tick_cross"] for x in valid),
 "rows":valid+[x for x in rows if "error" in x]
},separators=(",",":")),flush=True)
