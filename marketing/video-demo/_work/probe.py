import json, urllib.request, sys
from keyload import load
cfg = load()
body = {"model": cfg["model"], "messages": [{"role": "user", "content": "只回复两个字：就绪"}], "max_tokens": 32}
req = urllib.request.Request(cfg["endpoint"], data=json.dumps(body).encode("utf-8"),
    headers={"Content-Type": "application/json", "Authorization": "Bearer " + cfg["key"]})
try:
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.loads(r.read().decode("utf-8"))
    print("HTTP OK")
    print("reply:", d["choices"][0]["message"]["content"][:200])
    print("usage:", d.get("usage"))
except Exception as e:
    print("FAILED:", type(e).__name__, e)
    if hasattr(e, "read"):
        print(e.read().decode("utf-8", "replace")[:800])
