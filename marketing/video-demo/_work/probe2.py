import json, urllib.request
from keyload import load
cfg = load()
for name, extra in [("plain", {}), ("thinking-disabled", {"thinking": {"type": "disabled"}})]:
    body = {"model": cfg["model"], "messages": [{"role": "user", "content": "只回复两个字：就绪"}], "max_tokens": 2000, **extra}
    req = urllib.request.Request(cfg["endpoint"], data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + cfg["key"]})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            d = json.loads(r.read().decode("utf-8"))
        u = d.get("usage", {})
        print(f"[{name}] reply={d['choices'][0]['message']['content']!r}")
        print(f"        completion={u.get('completion_tokens')} reasoning={u.get('completion_tokens_details',{}).get('reasoning_tokens')}")
    except Exception as e:
        print(f"[{name}] FAILED {type(e).__name__}: {e}")
        if hasattr(e, "read"): print(e.read().decode("utf-8","replace")[:500])
