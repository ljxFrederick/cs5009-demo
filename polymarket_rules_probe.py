#!/usr/bin/env python3
import json, urllib.parse, urllib.request
G="https://gamma-api.polymarket.com"
UA="polymarket-rules-probe/1.0"
SLUGS=[
"metamask-fdv-above-3b-one-day-after-launch-363-663-664-569-222",
"metamask-fdv-above-2b-one-day-after-launch-222-955-573-228",
"metamask-fdv-above-1b-one-day-after-launch-978-851-628-634",
]
def get(url):
    req=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=15) as r:return json.loads(r.read().decode())
for slug in SLUGS:
    a=get(G+"/markets?slug="+urllib.parse.quote(slug))
    x=a[0] if a else {}
    keep={k:x.get(k) for k in [
      "slug","question","description","resolutionSource","endDate","eventStartTime",
      "conditionId","clobTokenIds","outcomes","feesEnabled","negRisk","negRiskMarketID","negRiskRequestID"
    ]}
    keep["events"]=[{k:e.get(k) for k in ["slug","title","description","negRisk","negRiskAugmented","resolutionSource","endDate"]} for e in (x.get("events") or [])]
    print("RULE_JSON",json.dumps(keep,separators=(",",":"),ensure_ascii=False),flush=True)
