#!/usr/bin/env python3
import json,time,itertools
from datetime import datetime,timezone
from web3 import Web3

RPC="https://base-mainnet.g.alchemy.com/public"
w3=Web3(Web3.HTTPProvider(RPC,request_kwargs={"timeout":8}))
USDC=Web3.to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")
WETH=Web3.to_checksum_address("0x4200000000000000000000000000000000000006")
MULTI=Web3.to_checksum_address("0xcA11bde05977b3631167028862bE2a173976CA11")
POOLS=[
 ("UNI",100,"0xb4CB800910B228ED3d0834cF79D697127BBB00e5"),
 ("UNI",500,"0xd0b53D9277642d899DF5C87A3966A349A798F224"),
 ("UNI",3000,"0x6c561B446416E1A00E8E93E221854d6eA4171372"),
 ("UNI",10000,"0x0b1C2DCbBfA744ebD3fC17fF1A96A1E1Eb4B2d69"),
 ("PAN",100,"0x72AB388E2E2F6FaceF59E3C3FA2C4E29011c2D38"),
 ("PAN",500,"0xB775272E537cc670C65DC852908aD47015244EaF"),
 ("PAN",10000,"0x118EFEF7DB0712F03E067122b8F89E7a4B79262f"),
]
POOLS=[(v,f,Web3.to_checksum_address(a)) for v,f,a in POOLS]
Q={
 "UNI":Web3.to_checksum_address("0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a"),
 "PAN":Web3.to_checksum_address("0xB048Bbc1Ee6b733FFfCFb9e9CeF7375518e25997"),
}
MABI=[{"inputs":[{"components":[{"type":"address","name":"target"},{"type":"bool","name":"allowFailure"},{"type":"bytes","name":"callData"}],"type":"tuple[]","name":"calls"}],"name":"aggregate3","outputs":[{"components":[{"type":"bool","name":"success"},{"type":"bytes","name":"returnData"}],"type":"tuple[]","name":"returnData"}],"stateMutability":"payable","type":"function"}]
PABI=[{"inputs":[],"name":"slot0","outputs":[{"type":"uint160"},{"type":"int24"},{"type":"uint16"},{"type":"uint16"},{"type":"uint16"},{"type":"uint8"},{"type":"bool"}],"stateMutability":"view","type":"function"}]
QABI=[{"inputs":[{"type":"bytes","name":"path"},{"type":"uint256","name":"amountIn"}],"name":"quoteExactInput","outputs":[{"type":"uint256"},{"type":"uint160[]"},{"type":"uint32[]"},{"type":"uint256"}],"stateMutability":"nonpayable","type":"function"}]
multi=w3.eth.contract(MULTI,abi=MABI)
pc={a:w3.eth.contract(a,abi=PABI) for _,_,a in POOLS}
qc={v:w3.eth.contract(a,abi=QABI) for v,a in Q.items()}

def get_spots():
    calls=[(a,True,pc[a].functions.slot0()._encode_transaction_data()) for _,_,a in POOLS]
    res=multi.functions.aggregate3(calls).call()
    out=[]
    for (v,f,a),(ok,b) in zip(POOLS,res):
        if not ok or len(b)<32:continue
        sqrt=int.from_bytes(bytes(b)[:32],"big")
        raw=(sqrt/(2**96))**2
        price=raw*1e12  # token1=USDC raw / token0=WETH raw -> USDC/WETH
        out.append({"venue":v,"fee":f,"pool":a,"price":price})
    return out

def path(a,b,f):
    return bytes.fromhex(a[2:])+int(f).to_bytes(3,"big")+bytes.fromhex(b[2:])

def quote_cycle(a,b,usd=100):
    # a: buy WETH with USDC; b: sell WETH for USDC
    try:
        z1=qc[a["venue"]].functions.quoteExactInput(path(USDC,WETH,a["fee"]),int(usd*1e6)).call()
        z2=qc[b["venue"]].functions.quoteExactInput(path(WETH,USDC,b["fee"]),int(z1[0])).call()
        out=int(z2[0])/1e6
        return {"usd":usd,"out":out,"profit":out-usd,"bps":(out-usd)/usd*10000,
                "gas_quotes":[int(z1[3]),int(z2[3])]}
    except Exception as e:
        return {"error":repr(e)}

start=time.time();dur=100
samples=0;theory_positive=0;actual=[];near=[]
best=None
last_block=None
while time.time()-start<dur:
    t0=time.time()
    try:
        spots=get_spots();samples+=1
        for a,b in itertools.permutations(spots,2):
            if a["pool"]==b["pool"]:continue
            factor=(b["price"]/a["price"])*(1-a["fee"]/1e6)*(1-b["fee"]/1e6)
            bps=(factor-1)*10000
            rec={"buy":f'{a["venue"]}-{a["fee"]}',"sell":f'{b["venue"]}-{b["fee"]}',
                 "spot_buy":a["price"],"spot_sell":b["price"],"theory_bps":bps}
            if best is None or bps>best["theory_bps"]:best={**rec,"utc":datetime.now(timezone.utc).isoformat()}
            if bps>-3:
                near.append({**rec,"utc":datetime.now(timezone.utc).isoformat()})
                near=sorted(near,key=lambda x:x["theory_bps"],reverse=True)[:30]
            if bps>0:
                theory_positive+=1
                q=quote_cycle(a,b,100)
                rr={**rec,"utc":datetime.now(timezone.utc).isoformat(),"quote":q}
                actual.append(rr)
                print("SPOT_CANDIDATE",json.dumps(rr,separators=(",",":")),flush=True)
    except Exception as e:
        print("POLL_ERROR",repr(e),flush=True)
    elapsed=time.time()-t0
    time.sleep(max(0,0.8-elapsed))
print("SPOT_MONITOR_SUMMARY",json.dumps({
 "utc_end":datetime.now(timezone.utc).isoformat(),"seconds":time.time()-start,
 "samples":samples,"theory_positive_events":theory_positive,
 "actual_quote_candidates":actual[:100],"best_theoretical":best,
 "top_near":near[:20]
},separators=(",",":")),flush=True)
