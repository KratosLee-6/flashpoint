import json
from pathlib import Path
p = Path(r"E:\工作\【汐构科技】\客户跟进\AI闪光点\app\fusion\evaluations\fuse-skillhub-parenting-x-keyattest.json")
d = json.loads(p.read_text(encoding="utf-8"))
def walk(o, pre="", depth=0):
    if depth > 2: return
    if isinstance(o, dict):
        for k, v in o.items():
            if isinstance(v, (dict, list)):
                print(f"{pre}{k}: {type(v).__name__}[{len(v)}]")
                walk(v, pre + "  ", depth + 1)
            else:
                s = str(v)
                print(f"{pre}{k}: {s[:110]}")
    elif isinstance(o, list):
        for i, v in enumerate(o[:3]):
            if isinstance(v, (dict, list)): walk(v, pre + f"[{i}] ", depth+1)
            else: print(f"{pre}[{i}] {str(v)[:110]}")
print("=== TOP KEYS ===", list(d.keys()))
print("=== STRUCTURE ===")
walk(d)
