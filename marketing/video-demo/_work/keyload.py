"""Load a model endpoint + key from the local minimax config.yaml. Never writes the key to disk."""
import re
from pathlib import Path

CONFIG = Path(r"C:\Users\Admin\.minimax\config.yaml")

# (label, baseURL regex, apiKey regex scoped after baseURL)
CANDIDATES = [
    ("glm", r"baseURL:\s*(https://open\.bigmodel\.cn/api/coding/paas/v4)\s*\n\s*authMode:[^\n]*\n\s*apiKey:\s*(\S+)"),
    ("minimax", r"baseURL:\s*(https://agent\.minimax\.cn/mavis/api/v1/llm/v1)\s*\n\s*apiKey:\s*(\S+)"),
]


def load():
    text = CONFIG.read_text(encoding="utf-8")
    for label, pattern in CANDIDATES:
        m = re.search(pattern, text)
        if m:
            base, key = m.group(1), m.group(2).strip().strip('"').strip("'")
            if label == "glm":
                return {"label": label, "base": base, "key": key,
                        "endpoint": base + "/chat/completions",
                        "model": "glm-5.3", "thinking": False}
            return {"label": label, "base": base, "key": key,
                    "endpoint": base + "/chat/completions",
                    "model": "minimax/MiniMax-M3.1-Flash-Preview", "thinking": False}
    raise SystemExit("no provider endpoint found in config.yaml")


if __name__ == "__main__":
    cfg = load()
    print("label  :", cfg["label"])
    print("endpoint:", cfg["endpoint"])
    print("model  :", cfg["model"])
    print("key len:", len(cfg["key"]), "prefix:", cfg["key"][:6] + "***")