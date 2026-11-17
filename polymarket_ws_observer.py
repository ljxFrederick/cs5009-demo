#!/usr/bin/env python3
import json, math, threading, time, urllib.parse, urllib.request
from collections import defaultdict
from datetime import datetime, timezone

import websocket

GAMMA="https://gamma-api.polymarket.com"
CLOB="https://clob.polymarket.com"
WS="wss://ws-subscriptions-clob.polymarket.com/ws/market"
UA="Mozilla/5.0 polymarket-ws-arb-observer/1.0"

RUN_SECONDS=150
CONFIRM_COOLDOWN=0.10
MIN_EDGE=-0.002  # record near misses too
ASSETS=("btc","eth","sol")
DURS=(5,15)

def get_json(url, timeout=10):
    req=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=timeout) as r:
        return json.loads(r.read().decode())

def jj(x):
    if isinstance(x,(list,dict)): return x
    if isinstance(x,str):
        try:return json.loads(x)
        except:return []
    return []

def market_slug(asset,mins,now=None):
    now=int(now or time.time()); step=mins*60
    return f"{asset}-updown-{mins}m-{(now//step)*step}"

def fetch_market(slug):
    ev=get_json(f"{GAMMA}/events/slug/{urllib.parse.quote(slug)}")
    ms=ev.get("markets") or []
    if not ms:return None
    x=ms[0]
    outs=[str(v) for v in jj(x.get("outcomes"))]
    tids=[str(v) for v in jj(x.get("clobTokenIds"))]
    om={o.casefold():t for o,t in zip(outs,tids)}
    a=om.get("up") or om.get("yes")
    b=om.get("down") or om.get("no")
    if not a or not b:return None
    cid=str(x.get("conditionId") or "")
    info=get_json(f"{CLOB}/clob-markets/{urllib.parse.quote(cid)}")
    fd=info.get("fd")
    rate=float(fd.get("r") or 0.0) if isinstance(fd,dict) else 0.0
    return {
      "slug":slug,"question":x.get("question"),"condition_id":cid,
      "a":a,"b":b,"rate":rate,"end":x.get("endDate")
    }

def parse_book(raw):
    bids=[(float(z["price"]),float(z["size"])) for z in raw.get("bids",[]) if float(z.get("size",0))>0]
    asks=[(float(z["price"]),float(z["size"])) for z in raw.get("asks",[]) if float(z.get("size",0))>0]
    bids.sort(reverse=True); asks.sort()
    return bids,asks

def fetch_book(tid):
    return parse_book(get_json(f"{CLOB}/book?token_id={urllib.parse.quote(tid)}"))

def buy_cost_usdc(asks,target,r):
    rem=target; cost=0.0
    for p,sz in asks:
        t=min(rem,sz); cost += t*(p+r*p*(1-p)); rem-=t
        if rem<=1e-12:return cost
    return None

def buy_cost_share(asks,target,r):
    rem=target; cost=0.0
    for p,sz in asks:
        f=1-r*(1-p)
        cap=sz*f
        tn=min(rem,cap); cost += (tn/f)*p; rem-=tn
        if rem<=1e-12:return cost
    return None

def buy_cost(asks,target,r):
    a=buy_cost_usdc(asks,target,r); b=buy_cost_share(asks,target,r)
    if a is None or b is None:return None
    return max(a,b)

def sell_proceeds(bids,target,r):
    rem=target; out=0.0
    for p,sz in bids:
        t=min(rem,sz); out += t*(p-r*p*(1-p)); rem-=t
        if rem<=1e-12:return out
    return None

def evaluate_books(m,ba,bb):
    abid,aask=ba; bbid,bask=bb
    rows=[]
    for s in (1,5,10,25,50,100,250):
        ca=buy_cost(aask,s,m["rate"]); cb=buy_cost(bask,s,m["rate"])
        if ca is not None and cb is not None:
            rows.append(("BUY",s,s-ca-cb))
        pa=sell_proceeds(abid,s,m["rate"]); pb=sell_proceeds(bbid,s,m["rate"])
        if pa is not None and pb is not None:
            rows.append(("SELL",s,pa+pb-s))
    rows.sort(key=lambda x:x[2],reverse=True)
    return rows

class Observer:
    def __init__(self,markets):
        self.markets=markets
        self.by_token={}
        for m in markets:
            self.by_token[m["a"]]=(m,"a")
            self.by_token[m["b"]]=(m,"b")
        self.books={}
        self.lock=threading.Lock()
        self.start=time.time()
        self.events=0
        self.book_events=0
        self.price_events=0
        self.raw_positive=0
        self.confirmed_positive=[]
        self.near_misses=[]
        self.last_confirm=defaultdict(float)
        self.signal_open={}
        self.signal_durations=[]
        self.errors=[]

    def update_full(self,tid,raw):
        self.books[tid]=parse_book(raw)

    def update_delta(self,tid,side,price,size):
        if tid not in self.books:return
        bids,asks=self.books[tid]
        target=bids if str(side).upper()=="BUY" else asks
        d={p:s for p,s in target}
        p=float(price); sz=float(size)
        if sz<=0:d.pop(p,None)
        else:d[p]=sz
        arr=list(d.items())
        arr.sort(key=lambda z:z[0],reverse=(str(side).upper()=="BUY"))
        if str(side).upper()=="BUY":self.books[tid]=(arr,asks)
        else:self.books[tid]=(bids,arr)

    def check_market(self,m,source):
        a,b=m["a"],m["b"]
        if a not in self.books or b not in self.books:return
        rows=evaluate_books(m,self.books[a],self.books[b])
        if not rows:return
        kind,size,edge=rows[0]
        key=(m["slug"],kind,size)
        now=time.time()
        if edge>0:
            self.raw_positive+=1
            if key not in self.signal_open:self.signal_open[key]=now
            # confirm promptly but throttle duplicate confirmations
            if now-self.last_confirm[m["slug"]] >= CONFIRM_COOLDOWN:
                self.last_confirm[m["slug"]]=now
                try:
                    ba=fetch_book(a); bb=fetch_book(b)
                    cr=evaluate_books(m,ba,bb)
                    best=cr[0] if cr else None
                    rec={
                      "t":datetime.now(timezone.utc).isoformat(),
                      "elapsed":now-self.start,"slug":m["slug"],"source":source,
                      "ws_best":{"kind":kind,"size":size,"profit":edge},
                      "rest_best":None
                    }
                    if best:
                        rec["rest_best"]={"kind":best[0],"size":best[1],"profit":best[2]}
                        if best[2]>0:
                            self.confirmed_positive.append(rec)
                            print("CONFIRMED_ARB",json.dumps(rec,separators=(",",":")),flush=True)
                        elif best[2]>=MIN_EDGE:
                            self.near_misses.append(rec)
                            print("NEAR_MISS",json.dumps(rec,separators=(",",":")),flush=True)
                    else:
                        self.near_misses.append(rec)
                except Exception as e:
                    self.errors.append("confirm:"+repr(e))
        else:
            # close any open raw signals for this slug
            for k,t0 in list(self.signal_open.items()):
                if k[0]==m["slug"]:
                    self.signal_durations.append(now-t0); del self.signal_open[k]
            if edge>=MIN_EDGE:
                self.near_misses.append({
                  "t":datetime.now(timezone.utc).isoformat(),"elapsed":now-self.start,
                  "slug":m["slug"],"source":source,
                  "ws_best":{"kind":kind,"size":size,"profit":edge},"rest_best":None
                })

    def on_open(self,ws):
        msg={"type":"market","markets":[],"assets_ids":list(self.by_token.keys()),"initial_dump":True}
        ws.send(json.dumps(msg))
        print("WS_OPEN subscribed_tokens",len(self.by_token),flush=True)
        def ping():
            while time.time()-self.start<RUN_SECONDS+10:
                time.sleep(20)
                try:ws.send("PING")
                except:return
        threading.Thread(target=ping,daemon=True).start()

    def on_message(self,ws,msg):
        if msg=="PONG":return
        self.events+=1
        try:data=json.loads(msg)
        except:return
        items=data if isinstance(data,list) else [data]
        for ev in items:
            if not isinstance(ev,dict):continue
            et=ev.get("event_type") or ev.get("type")
            if et=="book":
                self.book_events+=1
                tid=str(ev.get("asset_id") or "")
                if tid in self.by_token:
                    self.update_full(tid,ev)
                    self.check_market(self.by_token[tid][0],"book")
            elif et=="price_change":
                self.price_events+=1
                changes=ev.get("price_changes") or ev.get("changes") or []
                for ch in changes:
                    tid=str(ch.get("asset_id") or ev.get("asset_id") or "")
                    if tid not in self.by_token:continue
                    if ch.get("price") is not None and ch.get("size") is not None and ch.get("side") is not None:
                        self.update_delta(tid,ch["side"],ch["price"],ch["size"])
                    self.check_market(self.by_token[tid][0],"price_change")

    def on_error(self,ws,e):
        self.errors.append("ws:"+repr(e)); print("WS_ERROR",repr(e),flush=True)

    def on_close(self,ws,code,reason):
        print("WS_CLOSE",code,reason,flush=True)

    def run(self):
        ws=websocket.WebSocketApp(WS,on_open=self.on_open,on_message=self.on_message,
                                  on_error=self.on_error,on_close=self.on_close)
        th=threading.Thread(target=lambda:ws.run_forever(ping_interval=0),daemon=True)
        th.start()
        deadline=self.start+RUN_SECONDS
        while time.time()<deadline:
            time.sleep(5)
            print("HEARTBEAT",json.dumps({
              "elapsed":round(time.time()-self.start,1),"events":self.events,
              "books":self.book_events,"price_changes":self.price_events,
              "raw_positive":self.raw_positive,"confirmed":len(self.confirmed_positive)
            },separators=(",",":")),flush=True)
        try:ws.close()
        except:pass
        now=time.time()
        for k,t0 in list(self.signal_open.items()):
            self.signal_durations.append(now-t0)
        # dedupe near misses: summarize closest 20 by profit
        nm=sorted(self.near_misses,key=lambda x:(x.get("rest_best") or x["ws_best"])["profit"],reverse=True)[:20]
        summary={
          "started_utc":datetime.fromtimestamp(self.start,timezone.utc).isoformat(),
          "ended_utc":datetime.now(timezone.utc).isoformat(),
          "run_seconds":RUN_SECONDS,"markets":[{"slug":m["slug"],"rate":m["rate"]} for m in self.markets],
          "events":self.events,"book_events":self.book_events,"price_events":self.price_events,
          "raw_positive_events":self.raw_positive,
          "confirmed_positive_count":len(self.confirmed_positive),
          "confirmed":self.confirmed_positive[:50],
          "raw_signal_duration_count":len(self.signal_durations),
          "raw_signal_duration_max":max(self.signal_durations) if self.signal_durations else 0,
          "raw_signal_duration_median":sorted(self.signal_durations)[len(self.signal_durations)//2] if self.signal_durations else 0,
          "closest_near_misses":nm,"errors":self.errors[:20]
        }
        print("WS_SUMMARY",json.dumps(summary,separators=(",",":")),flush=True)

def main():
    now=int(time.time())
    markets=[]
    for a in ASSETS:
        for d in DURS:
            try:
                m=fetch_market(market_slug(a,d,now))
                if m:markets.append(m)
            except Exception as e:
                print("MARKET_ERROR",a,d,repr(e),flush=True)
    print("MARKETS",json.dumps([{"slug":m["slug"],"rate":m["rate"]} for m in markets],separators=(",",":")),flush=True)
    if not markets:raise SystemExit("no markets")
    Observer(markets).run()

if __name__=="__main__":main()
