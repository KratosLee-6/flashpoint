---
name: package-permission-design
slug: package-permission-design
displayName: 包权限声明式清单设计
version: 0.1.0
summary: 12 字段声明式权限清单 —— 让你的包说清楚「我要读啥、写啥、连啥」，机器可读、可校验、可审计。
description: 面向独立开发者与产品架构师。提供 12 字段声明式权限清单（permissions + capabilities + 路径白名单 + 网络出站白名单 + 环境变量白名单 + exec 白名单）的设计与校验。包上站台前**必须声明**，上万台前机器扫描，**声明之外的代码动作 = critical 越界**。代码示例以 JSON Schema + Python 实现。
license: MIT
---

# 包权限声明式清单设计

> **一句话**：让每个包说清楚"我要读啥、写啥、连啥、起啥进程、读啥环境变量"。没声明的 = 越界。

## 触发词

- 「设计包权限系统」
- 「声明式权限清单」
- 「让 Python 包说清楚它要干啥」
- 「防止包读 SSH 密钥」
- 「实现 manifest.json permissions」
- 「capabilities 声明」

## 这是什么

一个**12 字段声明式权限清单**的设计 + Schema + 校验。

**核心思路**：
```
第一道防线 = 声明。
包的作者必须写："我要读 $HOME/.ssh/，要连 api.x.com，要起 python 子进程"。
没说 = 越界 = 拦下。
```

**能解决**：
- 装了一个「画图」包，结果它去读 ~/.ssh/id_rsa
- 「工具」包静默上传用户文件
- 出事后无法审计"包到底做了什么"

**不解决**：
- 包作者**故意撒谎**（说要读图片，实际读 SSH）—— 这要靠第②道防线「静态检测」+ 第④道「运行时拦截」
- 已经在执行中的恶意包（要靠沙箱）

## 单次价格

本 skill 是**免费版**。可独立嵌入任何 Python 后端，无外部依赖、无网络调用。

## 依赖安装

```bash
# 仅 jsonschema（如要严格 schema 校验）+ 标准库
pip install jsonschema
```

## 12 字段权限清单

| 字段 | 含义 | 示例 |
|---|---|---|
| `fs:read` | 允许读取的文件/目录 glob | `["$HOME/.config/myapp/**"]` |
| `fs:write` | 允许写入的文件/目录 glob | `["$INSTALL_DIR/**"]` |
| `net:out` | 允许的网络出站域名/IP | `["api.example.com", "*.cdn.com"]` |
| `exec` | 允许起的可执行文件名（**只留子进程继承的边界**）| `["python", "ffmpeg"]` |
| `env:read` | 允许读的环境变量名 | `["MYAPP_API_KEY"]` |
| `env:write` | 允许写的环境变量名（极少）| `[]` |
| `cred:read` | 允许读凭据文件（**极敏感**）| `[]`（默认全空） |
| `cred:write` | 允许写凭据（**几乎不该有**）| `[]` |
| `paths` | 安装后可被列入 PATH 的目录 | `["$INSTALL_DIR/bin"]` |
| `signals` | 允许上报哪些信号类别 | `["install", "verify"]` |
| `capabilities` | 声明需要哪些 AI 能力 | `["vision"]` |
| `ui:show` | 允许弹哪些 UI（极少）| `[]` |

**加在一起 12 字段**。为什么是 12：够细不冗余 / 单一职责 / 跨项目复用。

## 完整 manifest.json 模板

```json
{
  "pack_id": "your-package-name",
  "name": "Your Package",
  "version": "1.0.0",
  "summary": "一句话能力描述",
  "entry": {"install": "install.py", "verify": "verify.py", "run": "run.py"},
  "permissions": {
    "fs:read":   ["$HOME/.config/yourapp/**"],
    "fs:write":  ["$INSTALL_DIR/data/**"],
    "net:out":   ["api.yourapp.com"],
    "exec":      ["python"],
    "env:read":  ["YOURAPP_API_KEY"],
    "env:write": [],
    "cred:read": [],
    "cred:write":[],
    "paths":     ["$INSTALL_DIR/bin"],
    "signals":   ["install", "verify"],
    "capabilities": [],
    "ui:show":   []
  },
  "capabilities": ["vision"],
  "author": "your-name",
  "license": "MIT"
}
```

### 变量占位符（自动展开）

- `$HOME` — 用户主目录
- `$INSTALL_DIR` — 安装根目录
- `$TMP` — 系统临时目录

**禁止在声明里写绝对路径** —— 一台机器上的 `/home/alice` 不等于另一台的 `/Users/bob`。

## 校验规则（5 条）

### ① 默认拒绝（Default Deny）

```python
ALLOWED_KEYS = {"fs:read","fs:write","net:out","exec","env:read","env:write",
                "cred:read","cred:write","paths","signals","capabilities","ui:show"}

def validate(perms: dict) -> list[str]:
    errs = []
    for k in perms:
        if k not in ALLOWED_KEYS:
            errs.append(f"未知字段：{k}")
    return errs
```

**任何不在白名单的字段都拒**。**`net:out: ["*"]` 也拒**（不允许通配）。

### ② glob 必须能展开（不能写「.」这种空 glob）

```python
def validate_globs(globs):
    for g in globs:
        if not g or g in [".", "..", "/"]:
            yield f"非法 glob：{g!r}"
        if "**" in g and not g.endswith("/**") and not g.endswith("/**/*"):
            yield f"双星必须配 / 目录边界：{g}"
```

### ③ exec 必须是已知程序（防止「exec: ["./anything"]」逃逸）

```python
KNOWN_EXECS = {"python", "python3", "node", "ffmpeg", "convert",
               "git", "curl"}  # 名单按需扩

def validate_exec(execs):
    for e in execs:
        if e.startswith("./") or "/" in e or "\\" in e:
            yield f"exec 必须是已知程序名，不允许路径：{e}"
        if e not in KNOWN_EXECS:
            yield f"未知程序：{e}（需先在 KNOWN_EXECS 注册）"
```

### ④ 凭据字段默认空（不是可选，是必须空）

```python
if perms.get("cred:read") or perms.get("cred:write"):
    yield ("warn", "cred:read/write 应默认全空，"
           "有值说明这个包要高权限，必须走审计")
```

**默认全空**。**有值 = 极高风险**。

### ⑤ capabilities 必须从已知清单选

```python
KNOWN_CAPS = {"vision", "audio", "long_context", "tools",
              "function_call", "code_exec", "web_search"}
```

## 调用流程

### 1) 包作者写 manifest.json

放在包根目录，提交前过校验：

```bash
python3 -c "
import json
from validate import validate, validate_globs, validate_exec
d = json.loads(open('manifest.json', encoding='utf-8').read())
errs = validate(d['permissions'])
errs += list(validate_globs(d['permissions']['fs:read'] + d['permissions']['fs:write']))
errs += list(validate_exec(d['permissions']['exec']))
print('✅ OK' if not errs else '❌ ' + '\n'.join(errs))
"
```

### 2) 平台扫描（消费 manifest）

```python
from manifest_loader import load_manifest
from validator import validate_manifest

man = load_manifest("your-package/manifest.json")
errs = validate_manifest(man)
if errs:
    print("❌ 拦截：", errs)
    return 403
```

### 3) 跨包审计（看哪些包用了哪些权限）

```python
# 平台后台扫描所有上架包
import os, json
audit = []
for pkg in os.listdir("packs/"):
    with open(f"packs/{pkg}/manifest.json", encoding="utf-8") as f:
        m = json.load(f)
    audit.append({
        "pack_id": pkg,
        "exec": m["permissions"]["exec"],
        "cred_read": m["permissions"]["cred:read"],
        "cred_write": m["permissions"]["cred:write"],
    })
# 找出任何 cred:write 非空的包 = 必须复审
```

## 结果展示

| 指标 | 实测（2026-09-18 lightpack） |
|---|---|
| 声明字段数 | 12 |
| 字段类型 | 5 类（路径 / 网络 / 进程 / 环境变量 / 能力）|
| 校验规则 | 5 条（默认拒 / glob / exec 白名单 / 凭据默认空 / 能力清单）|
| 包作者体验 | JSON 写一遍，提交前 1 个命令校验 |

## 说明

### ⚠️ 「声明 vs 实际」的鸿沟

**声明只能约束"好作者"**。

恶意包会**故意撒谎**：说要读图片，实际读 SSH。这道防线挡不住。

要挡，要靠：
- 第②道「静态检测」（AST 引擎看代码动作）
- 第④道「运行时拦截」（沙箱检查真实动作）

### ⚠️ 「exec 必须是已知程序」是硬约束

不能写 `"./mybinary"`。**包必须用 `python` / `ffmpeg` 这种已知程序**。

要起自定义二进制，应该走**单独的发布流程**（带数字签名），不在 `exec` 里。

### ⚠️ 「capabilities」是给上游看的

它告诉平台"这个包需要 AI 视觉"，不是"这个包能用视觉"。

后者是**运行时校验**，不是声明校验。

## 许可

MIT。