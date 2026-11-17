#!/usr/bin/env python3
import json,urllib.request
from Crypto.Hash import keccak

RPCS=["https://polygon-rpc.com","https://polygon.drpc.org"]
DEC=1_000_000
TXS=[
 ("Fed +25 NO 50@0.37","0x81eda253ae0132efa47b6ffb99d2442a7225f42f711a3b81820d91fc66451613","63396423986062034636625373404202970920080078602349826866595954726582462497358"),
 ("Fed +25 NO 24.44@0.37","0x9b506399b3c7b33fa7a70b09ae56f42a6d4567369043d23e3acef69f209d82e4","63396423986062034636625373404202970920080078602349826866595954726582462497358"),
 ("Fed +25 NO 25.56@0.37","0x6ef92afe38e1df25bed4e4f87ef3bd2da6dcc047f2c0f7d7cc9662b014e89877","63396423986062034636625373404202970920080078602349826866595954726582462497358"),
 ("Athletic NO 35.46@0.40","0xaddcbccf63109cc8d413470afd85ceed971ec77160c7ffb5398cbe9858380da3","647195517645297636552322833889363807010013971390938336792067770402830369875"),
]
def kt(sig):
    h=keccak.new(digest_bits=256);h.update(sig.encode());return "0x"+h.hexdigest()
V1=kt("OrdersMatched(bytes32,address,uint256,uint256,uint256,uint256)").lower()
V2=kt("OrdersMatched(bytes32,address,uint8,uint256,uint256,uint256)").lower()
def rpc(method,params):
    body=json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}).encode()
    last=None
    for u in RPCS:
        try:
            req=urllib.request.Request(u,data=body,headers={"Content-Type":"application/json","User-Agent":"receipt-classify/1.0"},method="POST")
            with urllib.request.urlopen(req,timeout=20) as r:x=json.loads(r.read().decode())
            if "result" in x:return x["result"]
            last=x
        except Exception as e:last=repr(e)
    raise RuntimeError(last)
def words(data):
    h=str(data).removeprefix("0x")
    return [int(h[i:i+64],16) for i in range(0,len(h),64) if len(h[i:i+64])==64]
for name,tx,target in TXS:
    rec=rpc("eth_getTransactionReceipt",[tx])
    rows=[]
    for lg in rec.get("logs") or []:
        tops=[str(x).lower() for x in lg.get("topics") or []]
        if not tops:continue
        w=words(lg.get("data","0x"))
        if tops[0]==V1 and len(w)>=4:
            ma,ta,mf,tf=w[:4]
            side="SELL" if ma!=0 and ta==0 else ("BUY" if ma==0 and ta!=0 else "OTHER")
            tok=ma if side=="SELL" else (ta if side=="BUY" else None)
            px=(tf/mf if side=="SELL" and mf else (mf/tf if side=="BUY" and tf else None))
            qty=(mf/DEC if side=="SELL" else tf/DEC if side=="BUY" else None)
            rows.append({"abi":"v1","side":side,"token":None if tok is None else str(tok),
                         "target":str(tok)==target if tok is not None else False,
                         "price":px,"qty":qty,"raw":[ma,ta,mf,tf],"address":lg.get("address")})
        elif tops[0]==V2 and len(w)>=4:
            si,tok,mf,tf=w[:4];side="SELL" if si==1 else "BUY"
            px=(tf/mf if side=="SELL" and mf else (mf/tf if side=="BUY" and tf else None))
            qty=(mf/DEC if side=="SELL" else tf/DEC if side=="BUY" else None)
            rows.append({"abi":"v2","side":side,"token":str(tok),"target":str(tok)==target,
                         "price":px,"qty":qty,"raw":[si,tok,mf,tf],"address":lg.get("address")})
    print("RECEIPT_CLASSIFY",json.dumps({"name":name,"tx":tx,"status":int(rec["status"],16),
      "gasUsed":int(rec["gasUsed"],16),"target_token":target,
      "matches":rows,"target_matches":[x for x in rows if x["target"]]},separators=(",",":")),flush=True)
