"""Cheap benchmark: which (endpoint, model, thinking) combination can actually finish a
segment-sized structured generation fast enough to complete a full collision."""
import json
import time
import urllib.error
import urllib.request

PROMPT = (
    "请严格按照给定 schema 生成一个 JSON 对象。\n"
    "schema: {\"title\": string, \"problem\": string, \"mechanism\": string, "
    "\"workflow\": string[6], \"artifacts\": {path:string, purpose:string}[5]}\n"
    "主题：把「儿童设备密钥可信证明」的能力接到「家长育儿建议流程」上。"
    "每个字段都要写满实质内容，workflow 六步都要具体可执行。"
)


def call(name, endpoint, key, body_extra, max_tokens=8192):
    body = {"messages": [{"role": "user", "content": PROMPT}],
            "max_tokens": max_tokens, "temperature": 0.3, **body_extra}
    req = urllib.request.Request(endpoint, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + key})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            d = json.loads(r.read().decode("utf-8"))
        el = time.time() - t0
        ch = d["choices"][0]
        u = d.get("usage", {})
        txt = ch["message"].get("content") or ""
        ok = False
        try:
            json.loads(txt.strip().strip("`").removeprefix("json").strip())
            ok = True
        except Exception:
            pass
        print(f"[{name}] {el:6.1f}s finish={ch.get('finish_reason')} "
              f"completion={u.get('completion_tokens')} reasoning={u.get('completion_tokens_details',{}).get('reasoning_tokens')} "
              f"chars={len(txt)} json_ok={ok}")
        return el
    except urllib.error.HTTPError as e:
        print(f"[{name}] HTTP {e.code}: {e.read().decode('utf-8','replace')[:200]}")
    except Exception as e:
        print(f"[{name}] FAILED after {time.time()-t0:.1f}s: {type(e).__name__} {e}")
    return None


import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from keyload import load  # noqa: E402

cfg = load()
GLM = cfg["endpoint"]

print("--- GLM coding endpoint / glm-5.3 ---")
call("glm5.3 no-thinking-field", GLM, cfg["key"], {})
call("glm5.3 effort=low", GLM, cfg["key"], {"thinking": {"type": "enabled", "effort": "low"}})