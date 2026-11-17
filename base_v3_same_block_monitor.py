#!/usr/bin/env python3
import json,time,itertools
from datetime import datetime,timezone
from web3 import Web3

RPC="https://base-mainnet.g.alchemy.com/public"
w3=Web3(Web3.HTTPProvider(RPC,request_kwargs={"timeout":6}))
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
Q={"UNI":Web3.to_checksum_address("0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a"),
   "PAN":Web3.to_checksum_address("0xB048Bbc1Ee6b733FFfCFb9e9CeF7375518e25997")}
MABI=[{"inputs":[{"components":[{"type":"address","name":"target"},{"type":"bool","name":"allowFailure"},{"type":"bytes","name":"callData"}],"type":"tuple[]","name":"calls"}],"name":"aggregate3","outputs":[{"components":[{"type":"bool","name":"success"},{"type":"bytes","name":"returnData"}],"type":"tuple[]","name":"returnData"}],"stateMutability":"payable","type":"function"}]
PABI=[{"inputs":[],"name":"slot0","outputs":[{"type":"uint160"},{"type":"int24"},{"type":"uint16"},{"type":"uint16"},{"type":"uint16"},{"type":"uint8"},{"type":"bool"}],"stateMutability":"view","type":"function"}]
QABI=[{"inputs":[{"type":"bytes","name":"path"},{"type":"uint256","name":"amountIn"}],"name":"quoteExactInput","outputs":[{"type":"uint256"},{"type":"uint160[]"},{"type":"uint32[]"},{"type":"uint256"}],"stateMutability":"nonpayable","type":"function"}]
multi=w3.eth.contract(MULTI,abi=MABI)
pc={a:w3.eth.contract(a,abi=PABI) for _,_,a in POOLS}
qc={v:w3.eth.contract(a,abi=QABI) for v,a in Q.items()}

def spots_at(block):
    calls=[(a,True,pc[a].functions.slot0()._encode_transaction_data()) for _,_,a in POOLS]
    res=multi.functions.aggregate3(calls).call(block_identifier=block)
    out=[]
    for (v,f,a),(ok,b) in zip(POOLS,res):
        if not ok or len(b)<32:continue
        sqrt=int.from_bytes(bytes(b)[:32],"big")
        price=(sqrt/(2**96))**2*1e12
        out.append({"venue":v,"fee":f,"pool":a,"price":price})
    return out
def path(a,b,f):return bytes.fromhex(a[2:])+int(f).to_bytes(3,"big")+bytes.fromhex(b[2:])
def quote_at(a,b,usd,block):
    z1=qc[a["venue"]].functions.quoteExactInput(path(USDC,WETH,a["fee"]),int(usd*1e6)).call(block_identifier=block)
    z2=qc[b["venue"]].functions.quoteExactInput(path(WETH,USDC,b["fee"]),int(z1[0])).call(block_identifier=block)
    out=int(z2[0])/1e6
    return {"usd":usd,"out":out,"profit":out-usd,"bps":(out-usd)/usd*10000,
            "quote_gas":[int(z1[3]),int(z2[3])]}
start=time.time();dur=90
samples=0;unique_blocks=set();theory_pos=0;actual_pos=[];cand=[];best=None;errs=[]
while time.time()-start<dur:
    t0=time.time()
    try:
        block=w3.eth.block_number
        if block in unique_blocks:
            time.sleep(.05);continue
        unique_blocks.add(block)
        spots=spots_at(block);samples+=1
        for a,b in itertools.permutations(spots,2):
            factor=(b["price"]/a["price"])*(1-a["fee"]/1e6)*(1-b["fee"]/1e6)
            tbps=(factor-1)*10000
            base={"block":block,"buy":f'{a["venue"]}-{a["fee"]}',"sell":f'{b["venue"]}-{b["fee"]}',
                  "spot_buy":a["price"],"spot_sell":b["price"],"theory_bps":tbps}
            if best is None or tbps>best["theory_bps"]:best={**base,"utc":datetime.now(timezone.utc).isoformat()}
            if tbps>0:
                theory_pos+=1
                qs=[]
                for usd in (1,5,10,25,50,100):
                    try:qs.append(quote_at(a,b,usd,block))
                    except Exception as e:qs.append({"usd":usd,"error":repr(e)})
                rec={**base,"utc":datetime.now(timezone.utc).isoformat(),"quotes":qs}
                cand.append(rec)
                pp=[q for q in qs if "profit" in q and q["profit"]>0]
                if pp:
                    actual_pos.append(rec)
                    print("SAME_BLOCK_PROFIT",json.dumps(rec,separators=(",",":")),flush=True)
                else:
                    print("SAME_BLOCK_THEORY_ONLY",json.dumps(rec,separators=(",",":")),flush=True)
    except Exception as e:errs.append(repr(e))
    time.sleep(max(0,.18-(time.time()-t0)))
print("SAME_BLOCK_SUMMARY",json.dumps({
 "utc_end":datetime.now(timezone.utc).isoformat(),"seconds":time.time()-start,
 "samples":samples,"unique_blocks":len(unique_blocks),"theory_positive":theory_pos,
 "actual_positive_events":len(actual_pos),"actual_positive":actual_pos[:100],
 "candidates":cand[:100],"best":best,"errors":errs[:20]
},separators=(",",":")),flush=True)
