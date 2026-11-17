#!/usr/bin/env python3
import asyncio,json,time
from datetime import datetime,timezone
import websockets

WS="wss://ws-live-data.polymarket.com/"
RUN=20

async def main():
    start=time.monotonic(); rows=[]; frames=0
    async with websockets.connect(WS,ping_interval=None,max_size=8_000_000) as ws:
        sub={"action":"subscribe","subscriptions":[
            {"topic":"activity","type":"trades","filters":""},
            {"topic":"activity","type":"orders_matched","filters":""}
        ]}
        await ws.send(json.dumps(sub))
        next_ping=time.monotonic()+4
        print("RTDS_SAMPLE_START",datetime.now(timezone.utc).isoformat(),flush=True)
        while time.monotonic()-start<RUN:
            now=time.monotonic()
            if now>=next_ping:
                await ws.send("PING");next_ping=now+4
            try:
                p=await asyncio.wait_for(ws.recv(),timeout=.5)
            except asyncio.TimeoutError:
                continue
            if p in ("PONG","PING"):continue
            frames+=1
            try:v=json.loads(p)
            except Exception:
                print("RTDS_RAW",repr(p[:500]),flush=True);continue
            arr=v if isinstance(v,list) else [v]
            for m in arr:
                if not isinstance(m,dict):continue
                topic=m.get("topic");typ=m.get("type")
                if topic!="activity":continue
                rec={"topic":topic,"type":typ,"timestamp":m.get("timestamp"),"payload":m.get("payload")}
                rows.append(rec)
                if len(rows)<=30:
                    print("RTDS_EVENT",json.dumps(rec,separators=(",",":"),ensure_ascii=False),flush=True)
    types={}
    for r in rows:types[r["type"]]=types.get(r["type"],0)+1
    print("RTDS_SAMPLE_SUMMARY",json.dumps({
      "utc_end":datetime.now(timezone.utc).isoformat(),"frames":frames,"events":len(rows),
      "types":types,"sample":rows[:10]},separators=(",",":"),ensure_ascii=False),flush=True)

if __name__=="__main__":asyncio.run(main())
