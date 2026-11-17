#!/usr/bin/env python3
import json,urllib.request
u="https://api.geckoterminal.com/api/v2/networks/base/pools?page=1"
req=urllib.request.Request(u,headers={"User-Agent":"base-gecko-candidate/1.0","Accept":"application/json"})
with urllib.request.urlopen(req,timeout=20) as r:x=json.loads(r.read().decode())
print("GECKO_SCHEMA",json.dumps({"keys":list(x.keys()),"first":(x.get("data") or [])[:2],"included":(x.get("included") or [])[:4]},separators=(",",":"))[:30000],flush=True)
