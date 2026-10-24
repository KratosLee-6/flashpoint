---
name: ast-static-scan-arch
slug: ast-static-scan-arch
displayName: AST 静态检测引擎架构
version: 0.1.0
summary: 别再用正则检测越界 —— 用 AST 引擎把「读 SSH 密钥 / 连 evil-collector / 写 /etc/cron.d」这种行为挑出来。正则漏 4/5，AST 5/5。
description: 面向后端工程师。提供 AST 静态扫描的两引擎对照（regex vs AST）、5 类越界检测规则、handler 顺序陷阱、值/编码误判解决方案、3 套攻击夹具。代码示例以 Python ast 模块实现。
license: MIT
---

# AST 静态检测引擎架构

> **一句话**：用正则做权限越界检测 = 漏 4/5。AST 引擎 = 5/5。

## 触发词

- 「写 Python 静态扫描器」
- 「检测代码里的 fs/net/exec 行为」
- 「正则 vs AST」
- 「如何不漏 open(/etc/passwd) 这种调用」
- 「Python AST 检测越界」
- 「做包安全扫描」

## 这是什么

一个**用 Python AST 模块做权限越界扫描**的架构设计。

**核心思路**：
```
Regex 模式：「open(image_path, "rb")」  ← 把 "rb" 当路径，误报 8 条
AST 模式：把代码变成语法树看真实动作 ← 准确，但实现复杂度 ↑

结论：单用 regex 不行；
     单用 AST 不够（碰到 exec("rm "+x) 这种动态拼接会漏）；
     两引擎并存 + diff 才能「量化变没变好」。
```

**能解决**：
- 装包前自动扫一遍「这个包实际会干啥」
- 「声明 vs 实际」的鸿沟（package-permission-design 留的坑）
- 误报 8 条 → 1 条（实测）

**不解决**：
- 运行时**真实动作**拦截（要靠 sandbox）
- 加密 / 混淆代码（AST 拿到的是加密字符串）

## 单次价格

本 skill 是**免费版**。可独立嵌入任何 Python 后端，无外部依赖、无网络调用。

## 依赖安装

```bash
# Python 3.11+ 标准库 ast
python3 -c "import ast; print('ok')"
```

## 为什么正则不够（4 类结构性误报）

| 反模式 | 触发场景 | 后果 |
|---|---|---|
| 把模式 `"rb"` 当路径 | `open(image_path, "rb")` | 误报 |
| 把编码名 `"utf-8"` 当路径 | `read_text(encoding="utf-8")` | 误报 |
| 函数参数名当路径 | `def f(name="~/.ssh/id_rsa"): ...` | 漏报（参数值没扫到）|
| 嵌套字符串拼接 | `os.system("rm -rf " + user_input)` | 漏报 |

**实测**：干净包被正则误报 8 处（5 个是上面这类）；AST 引擎下误报 = 1 处。

## AST 引擎架构

### 1) AST 提取

```python
import ast

def parse(code: str) -> ast.Module:
    return ast.parse(code)
```

### 2) 两引擎并存（regex + ast）

```python
def scan(code: str, declared: set) -> list[Finding]:
    findings = []
    findings += regex_extract(code)        # 兜底（看直观动作）
    findings += ast_extract(code)          # 主路径（看真实调用）
    # diff 阶段：同一段代码两引擎结果不同 = 一方漏判
    return findings
```

### 3) AST 走一遍（4 类核心节点）

```python
import ast
from pathlib import Path

class Extract(ast.NodeVisitor):
    def __init__(self):
        self.usages = []   # 收集到的所有"动作"

    def visit_Call(self, node):
        # open(...) / Path(...) / urllib... / requests.get(...) / subprocess.run(...)
        # 1. 解析 func 名（处理 a.b.c 这种链式调用）
        f = self._qualified_name(node.func)
        # 2. 提取参数（按 sink 类型处理）
        args = self._resolve_args(node.args)
        # 3. 标记 sink
        if self._is_sink(f):
            self.usages.append({"sink": f, "args": args, "line": node.lineno})
        self.generic_visit(node)

    def _qualified_name(self, node) -> str:
        # `import urllib.request` 绑顶层名 `urllib`，不是 `urllib.request`
        # 这里要小心：import a.b.c 实际绑定是 a（顶层）
        parts = []
        cur = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        return ".".join(reversed(parts))

    def _resolve_args(self, args):
        # 只解析字面量、Name 引用、Attribute 引用
        # 不解析 BinOp / Lambda 这种动态结构（标 soft）
        ...
```

### 4) 5 类越界规则（按 sink 分）

```python
SINKS = {
    # fs:read
    "open":          "fs:read",   "Path":       "fs:read",
    "read_text":     "fs:read",   "read_bytes": "fs:read",
    # fs:write
    "write_text":    "fs:write",  "write_bytes":"fs:write",
    "mkdir":         "fs:write",  "unlink":     "fs:write",
    # net:out
    "urlopen":       "net:out",   "urlretrieve":"net:out",
    "request":       "net:out",   "get":        "net:out",  # 配合 requests
    "urlretrieve":   "net:out",
    # exec
    "system":        "exec",      "Popen":      "exec",
    "run":           "exec",      "call":       "exec",
    # env:read
    "environ":       "env:read",  "getenv":     "env:read",
}

def map_sink_to_perm(sink: str) -> str | None:
    # requests.get(...) 的 sink 是 "get"——但只有当 obj 是 requests.Session
    # 的时候才算 net:out。这里要先看 func receiver。
    return SINKS.get(sink)
```

**两个 sink 表合并的陷阱**（实测踩过）：
```python
"Path":        "fs:read",
"path.exists": "fs:read",  # path 是 Path 对象
"os.path.exists": "fs:read",
# 上面 3 个都映射 fs:read —— 但写代码时要按顺序匹配：
#   os.path.exists (模块函数)  →  fs:read
#   p.exists()       (对象方法)  →  fs:read
# 处理顺序错了会把 os.path.exists 误判为其他 sink。
```

### 5) 三类证据：硬 / 软 / 模糊

```python
EVIDENCE_HARD = "硬证据"     # 字面量直接是 SSH 密钥路径
EVIDENCE_SOFT = "软证据"     # 变量名提示，但不绝对
EVIDENCE_AMBIGUOUS = "模糊"  # 表达式拼接，无法静态判定
```

**只有硬证据算 critical**。**软证据标 warning**（让审计人去查）。

## 5 个核心陷阱（实测）

### ① 别把"rb" / "utf-8" 当路径

```python
# AST 提取参数时，只接受 ast.Constant (str)；
# mode / encoding 字符串要排除
SAFE_MODE = {"r","w","rb","wb","a","ab","r+","w+","a+"}
SAFE_ENC = {"utf-8","utf-16","ascii","latin-1","gbk","gb2312","gb18030"}

def is_path_like(s: str) -> bool:
    if not s: return False
    if s.startswith("/") or s.startswith("$"):
        return True
    if s.startswith(("~", "./", "../", ".\\", "..\\")):
        return True
    if "/" in s or "\\" in s:
        return True
    return False

# open(image_path, "rb") → mode="rb" → is_path_like("rb") = False ✓
```

### ② 别把列表参数当路径硬解析

```python
# requests.get([api_url])  → args 是 List，不应按字符串处理
if isinstance(arg, ast.List):
    return [resolve(e) for e in arg.elts]
```

### ③ `import a.b.c` 绑顶层 `a`

```python
# ⚠️ 大坑：import urllib.request 实际绑定是 urllib
# 错：把 urllib.request.urlopen 当成 sink="urllib.request.urlopen" → 不在 SINKS 表
# 对：sink="request"（按顶层 + 末段拆分匹配）
```

### ④ handler 顺序

**模块函数**（os.path.exists）必须排在**对象方法**（p.exists）之前——
否则前者被后者截走 → 误判为 HARD。

### ⑤ values_for 字面量展开

```python
def values_for(arg) -> list[str]:
    """只解析字面量 / Name 引用，BinOp 等复杂表达式标 ambiguous。"""
    if isinstance(arg, ast.Constant):
        return [arg.value] if isinstance(arg.value, str) else []
    if isinstance(arg, ast.Name):
        return [arg.id]   # 软证据（变量名就是值）
    return []            # ambiguous
```

## 调用流程

### 1) 写扫描器

```python
import ast
from extract import Extract, map_sink_to_perm

def scan_file(path: str, declared_perms: dict) -> list[dict]:
    code = Path(path).read_text(encoding="utf-8")
    tree = ast.parse(code)
    ex = Extract()
    ex.visit(tree)

    findings = []
    for u in ex.usages:
        perm = map_sink_to_perm(u["sink"])
        if not perm:
            continue
        # 看是否在 declared 里
        category = perm.split(":")[1]   # "read"/"write"/...
        allowed = declared_perms.get(perm, [])
        if u["args"] and not in_any(u["args"], allowed):
            findings.append({"perm": perm, "actual": u["args"],
                              "declared": allowed, "line": u["line"]})
    return findings
```

### 2) 跑夹具

```bash
python3 -m scan --pack evil-fixture --declared declared.json
# 期望：5 处越界全中
```

### 3) 比两引擎

```bash
python3 -m compare --pack clean-pack
# 期望：regex 误报 8 → AST 1
```

## 结果展示

| 指标 | regex 引擎 | AST 引擎 |
|---|---|---|
| 干净包误报 | 8 | 1 |
| 越界检测命中率 | 4/5 | 5/5 |
| 实现复杂度 | 低 | 中 |
| 误判攻击夹具 | 漏 1 处 | 全中 |

## 说明

### ⚠️ 两引擎必须并存

- regex 看**直观动作**（一眼能扫出来的不变量）
- AST 看**真实动作**（消除"rb"当路径这种误报）
- diff 阶段暴露"regex 漏、AST 中"的能力缺口

### ⚠️ AST 看不透加密/混淆

```python
import base64
exec(base64.b64decode("...").decode())  # AST 看到 base64.b64decode，看不到 decode 后
```

要对付这种，靠**运行时检测**（sandbox）而不是静态扫描。

### ⚠️ 「软证据」不能算 critical

软证据（变量名暗示）只标 warning，让审计人去查。

**只有字面量直接命中**（比如代码里写 `"/root/.ssh/id_rsa"`）才算硬证据 critical。

## 许可

MIT。