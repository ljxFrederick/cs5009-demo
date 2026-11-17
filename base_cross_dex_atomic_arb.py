#!/usr/bin/env python3
import json,time
from datetime import datetime,timezone
from web3 import Web3

RPCS=["https://base-rpc.publicnode.com","https://mainnet.base.org","https://1rpc.io/base"]
TOKENS={
 "USDC":"0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
 "WETH":"0x4200000000000000000000000000000000000006",
 "cbBTC":"0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf",
 "AERO":"0x940181a94A35A4569E4529A3CDfB74e38FD98631",
}
UNI_FACTORY="0x33128a8fC17869897dcE68Ed026d694621f6FDfD"
UNI_QUOTER="0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a"
PAN_FACTORY="0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865"
PAN_QUOTER="0xB048Bbc1Ee6b733FFfCFb9e9CeF7375518e25997"
AERO_ROUTER="0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43"
AAVE_POOL="0xA238Dd80C259a72e81d7e4664a9801593F98d1c5"
ZERO="0x0000000000000000000000000000000000000000"
FEES=[100,500,3000,10000]
AMOUNTS=[10,100,1000,10000]

FACTORY_ABI=[{"inputs":[{"type":"address","name":"tokenA"},{"type":"address","name":"tokenB"},{"type":"uint24","name":"fee"}],"name":"getPool","outputs":[{"type":"address","name":"pool"}],"stateMutability":"view","type":"function"}]
QUOTER_ABI=[{"inputs":[{"type":"bytes","name":"path"},{"type":"uint256","name":"amountIn"}],"name":"quoteExactInput","outputs":[{"type":"uint256","name":"amountOut"},{"type":"uint160[]","name":"sqrtPriceX96AfterList"},{"type":"uint32[]","name":"initializedTicksCrossedList"},{"type":"uint256","name":"gasEstimate"}],"stateMutability":"nonpayable","type":"function"}]
AERO_ABI=[{"inputs":[{"type":"uint256","name":"amountIn"},{"components":[{"type":"address","name":"from"},{"type":"address","name":"to"},{"type":"bool","name":"stable"},{"type":"address","name":"factory"}],"type":"tuple[]","name":"routes"}],"name":"getAmountsOut","outputs":[{"type":"uint256[]","name":"amounts"}],"stateMutability":"view","type":"function"}]
ERC20_ABI=[{"inputs":[],"name":"decimals","outputs":[{"type":"uint8"}],"stateMutability":"view","type":"function"}]
AAVE_ABI=[{"inputs":[],"name":"FLASHLOAN_PREMIUM_TOTAL","outputs":[{"type":"uint128"}],"stateMutability":"view","type":"function"}]

w3=None
for rpc in RPCS:
    try:
        z=Web3(Web3.HTTPProvider(rpc,request_kwargs={"timeout":12}))
        if z.is_connected():
            w3=z;RPC=rpc;break
    except Exception: pass
if not w3: raise SystemExit("no rpc")

T={k:Web3.to_checksum_address(v) for k,v in TOKENS.items()}
dec={}
for k,a in T.items():
    dec[k]=int(w3.eth.contract(a,abi=ERC20_ABI).functions.decimals().call())
block=w3.eth.block_number
gas_price=w3.eth.gas_price

factories={
 "UNI":w3.eth.contract(Web3.to_checksum_address(UNI_FACTORY),abi=FACTORY_ABI),
 "PAN":w3.eth.contract(Web3.to_checksum_address(PAN_FACTORY),abi=FACTORY_ABI)
}
quoters={
 "UNI":w3.eth.contract(Web3.to_checksum_address(UNI_QUOTER),abi=QUOTER_ABI),
 "PAN":w3.eth.contract(Web3.to_checksum_address(PAN_QUOTER),abi=QUOTER_ABI)
}
aero=w3.eth.contract(Web3.to_checksum_address(AERO_ROUTER),abi=AERO_ABI)
aave=w3.eth.contract(Web3.to_checksum_address(AAVE_POOL),abi=AAVE_ABI)
try: flash_bps=int(aave.functions.FLASHLOAN_PREMIUM_TOTAL().call(block_identifier=block))
except Exception as e: flash_bps=None

poolmap={}
for venue,f in factories.items():
    for x in T:
        if x=="USDC": continue
        vals=[]
        for fee in FEES:
            try:
                p=f.functions.getPool(T["USDC"],T[x],fee).call(block_identifier=block)
                if p.lower()!=ZERO.lower(): vals.append((fee,p))
            except Exception: pass
        poolmap[(venue,x)]=vals

def v3_path(token_in,token_out,fee):
    return bytes.fromhex(token_in[2:])+int(fee).to_bytes(3,"big")+bytes.fromhex(token_out[2:])

def q_v3(venue,ain,aout,raw_in):
    # choose best direct fee tier; discover for this pair directly
    best=None
    f=factories[venue];q=quoters[venue]
    for fee in FEES:
        try:
            p=f.functions.getPool(ain,aout,fee).call(block_identifier=block)
            if p.lower()==ZERO.lower(): continue
            res=q.functions.quoteExactInput(v3_path(ain,aout,fee),raw_in).call(block_identifier=block)
            out=int(res[0]); gas=int(res[3])
            if out>0 and (best is None or out>best["out"]):
                best={"out":out,"fee":fee,"quote_gas":gas,"pool":p}
        except Exception: pass
    return best

def q_aero(ain,aout,raw_in):
    best=None
    for stable in (False,True):
        try:
            route=[(ain,aout,stable,Web3.to_checksum_address(ZERO))]
            amounts=aero.functions.getAmountsOut(raw_in,route).call(block_identifier=block)
            out=int(amounts[-1])
            if out>0 and (best is None or out>best["out"]):
                best={"out":out,"stable":stable}
        except Exception: pass
    return best

def quote(venue,ain,aout,raw):
    if venue in ("UNI","PAN"): return q_v3(venue,ain,aout,raw)
    return q_aero(ain,aout,raw)

venues=("UNI","PAN","AERO")
rows=[]
for x in ("WETH","cbBTC","AERO"):
    for usd in AMOUNTS:
        raw_in=int(usd*(10**dec["USDC"]))
        for v1 in venues:
            leg1=quote(v1,T["USDC"],T[x],raw_in)
            if not leg1: continue
            for v2 in venues:
                if v2==v1: continue
                leg2=quote(v2,T[x],T["USDC"],leg1["out"])
                if not leg2: continue
                out_usdc=leg2["out"]/(10**dec["USDC"])
                gross=out_usdc-usd
                rows.append({
                  "asset":x,"amount_in_usdc":usd,"route":f"{v1}->{v2}",
                  "amount_out_usdc":out_usdc,"gross_profit_usdc":gross,
                  "gross_bps":gross/usd*10000,
                  "leg1":leg1,"leg2":leg2
                })
rows.sort(key=lambda z:z["gross_profit_usdc"],reverse=True)
pos=[z for z in rows if z["gross_profit_usdc"]>1e-6]
print("BASE_CROSS_DEX_SUMMARY",json.dumps({
 "utc":datetime.now(timezone.utc).isoformat(),"rpc":RPC,"block":block,
 "gas_price_gwei":gas_price/1e9,"flashloan_premium_raw_bps":flash_bps,
 "flashloan_pct":None if flash_bps is None else flash_bps/100,
 "token_decimals":dec,"poolmap":{str(k):v for k,v in poolmap.items()},
 "quote_count":len(rows),"positive_count":len(pos),
 "positive":pos[:100],"best":rows[:100]
},separators=(",",":")),flush=True)
