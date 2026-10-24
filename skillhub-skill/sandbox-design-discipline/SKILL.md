---
name: sandbox-design-discipline
slug: sandbox-design-discipline
displayName: 沙箱设计纪律（环境净化 + 进程围栏）
version: 0.1.0
summary: 5 条沙箱纪律 —— 等级量出来不声称、不静默降级、不叫"沙箱"除非真隔离、Windows 上封顶 PROCESS_JAIL，代码里直说"这不是沙箱"。
description: 面向后端工程师。提供沙箱设计的 5 条纪律：等级量出来（不是声称）、跨平台原语对照表、不静默降级、Windows 限制诚实标注、环境净化实测数字（202→21 变量 + 密钥泄漏 12→0）。
license: MIT
---

# 沙箱设计纪律（环境净化 + 进程围栏）

> **一句话**：Windows 上封顶 `PROCESS_JAIL`（不是沙箱）。代码里直说「这不是沙箱」。

## 触发词

- 「设计包运行时隔离」
- 「沙箱纪律」
- 「Windows 进程围栏」
- 「不让包读 SSH 密钥」
- 「进程级隔离」
- 「PROCESS_JAIL」

## 这是什么

一个**沙箱设计纪律 + 跨平台原语对照 + Windows 实测**的工程经验总结。

**核心思路**：
```
"沙箱"是个重度词 —— macOS sandbox-exec / Linux Landlock / Windows AppContainer 才配叫。
档 B（环境净化 + 工作目录）单独存在没有意义，绝不能对外叫"沙箱"。
```

**能解决**：
- 跨平台包运行时隔离设计没踩坑
- 不会因为「Windows 跑不了 Landlock」就伪装"我有沙箱"
- 拿到 5 类原语对照表，知道每个平台能拿什么

**不解决**：
- 真沙箱（macOS sandbox-exec 需要重新设计 — 不是这个 skill 的范围）

## 单次价格

本 skill 是**免费版**。可独立嵌入任何 Python 后端，无外部依赖、无网络调用。

## 依赖安装

```bash
# Python 3.11+ 标准库
python3 -c "import os, subprocess; print('ok')"

# Windows 特定能力（可选）
pip install pywin32    # 用于 Job Object 等 API
```

## 5 条纪律

### ① 等级量出来，不声称

```
等级不能写"sandbox: true" —— 必须来自实测。
```

```python
def canary_probe(env_var_name="VOLC_SECRETKEY") -> dict:
    """在隔离环境下真去探。**没有探过 = 不知道级别**，而不是"应该是沙箱"。"""
    facts = {
        "env_scrubbed": False,   # env 里看不到 env_var_name
        "secret_count": 0,        # env 里还有多少像密钥的串
        "process_killed_on_exit": False,  # 退出时孙进程被杀
        "file_read_blocked": None,        # 写家目录是否成功
        "net_blocked": None,              # 连外网是否成功
        "level": "UNKNOWN",
    }
    # 探测 env 净化（实测）
    env = os.environ.copy()
    if env_var_name not in env:
        facts["env_scrubbed"] = True
    # 探测进程围栏
    child = subprocess.Popen(["python", "-c", "import time; time.sleep(60)"])
    # ... 探死后 child 还在不在
    # 探测文件读
    try:
        Path.home().joinpath(".test-write").write_text("x")
        facts["file_read_blocked"] = False
    except: pass
    # 综合判定
    facts["level"] = derive_level(facts)
    return facts
```

**实测是唯一可信来源**。声称"sandbox: true" 而没探过 = 撒谎。

### ② 跨平台原语对照表（每个平台拿什么）

| 原语 | Windows | macOS | Linux | 我们拿到了吗 |
|---|---|---|---|---|
| **环境净化** | ✅ SetEnvironmentVariable | ✅ environ | ✅ environ | ✅ |
| **进程围栏（Job Object）** | ✅ Job Object + KILL_ON_JOB_CLOSE | n/a | ✅ prctl(PR_SET_PDEATHSIG) | ✅ Windows |
| **低完整性级别（IL）** | ⚠️ CreateProcessAsUser + 特权 | n/a | n/a | ❌ |
| **文件读白名单** | ⚠️ ACL（复杂） | ✅ sandbox-exec | ✅ Landlock | ❌ |
| **网络白名单** | ⚠️ WFP（要管理员） | ✅ sandbox network-rule | ✅ seccomp + nftables | ❌ |
| **AppContainer** | ✅ | n/a | n/a | ❌ |

**Windows 上的实际能力封顶**：`PROCESS_JAIL`（环境净化 + 进程围栏）。

**Linux 上能做到**：环境净化 + prctl + Landlock + seccomp（**才配叫"沙箱"**）。

**macOS 上能做到**：sandbox-exec（最完整的沙箱语义）。

### ③ 不静默降级

```
拿到 PROCESS_JAIL → 报错说"我给的不够" → 用户能选"我用 macOS 跑"还是"接受这个降级"。
不能：悄悄跑 PROCESS_JAIL 然后告诉用户"已有沙箱"。
```

```python
# ❌ 错
report = {"sandbox_level": "PROCESS_JAIL", "claimed": "sandbox"}  # 撒谎

# ✅ 对
report = {
    "sandbox_level": "PROCESS_JAIL",
    "sandbox_desc": ("环境净化 + 进程围栏。**这不是沙箱**："
                     "Windows 上文件读与网络白名单拿不到（要 ACL / WFP）。"
                     "沙箱能力需要 macOS sandbox-exec 或 Linux Landlock。"),
    "downgraded": False,
    "no_better_alternative_on_this_platform": True,
}
```

**降级要明示**。**不让用户以为"已有保护"**。

### ④ 取各步里最弱的环节上报

```
整个隔离的强度 = min(各步强度)。
所以：取最弱一环上报。 拿最强的上报 = 虚报。
```

```python
def report_run(steps: list[dict]) -> dict:
    levels = [step["level"] for step in steps]
    overall = min(levels, key=lambda x: LEVEL_ORDER[x])
    return {
        "overall_level": overall,
        "per_step": steps,
        "report_level": overall,  # 取最弱，不是最强
    }
```

### ⑤ Windows 上到不了沙箱 = 直说

```
代码注释里写："这不是沙箱"。用户手册里写："Windows 上封顶 PROCESS_JAIL"。
不是失败 —— 是诚实。**这是这个项目的纪律**。
```

**示例（README 段落）**：
```markdown
## Windows 上不是沙箱

`lightpack/sandbox.py` 拿到的实际能力：

| 原语 | 拿到了吗 | 实测 |
|---|---|---|
| 环境净化 | ✅ | 202 个变量 → 21 个；密钥泄漏 12 → 0 |
| 作业对象 | ✅ | 孙进程连带被杀 |
| 文件读白名单 | ❌ | Windows 没有等价 Landlock 的东西 |
| 网络白名单 | ❌ | 要 WFP，要管理员 |

等级封顶 `PROCESS_JAIL`，代码里输出直说：「这不是沙箱」。
调研纪律：档 B（环境净化 + 工作目录）单独存在没有意义，绝不能对外叫沙箱。
macOS sandbox-exec、Linux Landlock 才配叫沙箱。
```

## 调用流程

### 1) Windows 进程围栏

```python
import subprocess
import ctypes
from ctypes import wintypes

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JobObject = ctypes.windll.kernel32.CreateJobObjectW
SetInformationJobObject = ctypes.windll.kernel32.SetInformationJobObject

def run_in_job(cmd: list[str], env: dict | None = None) -> dict:
    job = JobObject(None, None)
    info = ctypes.c_byte(0x60)  # 演示用结构体
    # 实际要 JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))

    p = subprocess.Popen(cmd, env={**os.environ, **(env or {})})
    # 把 p 绑到 job 上
    AssignProcessToJobObject(job, p)
    # 父进程退出 → 子进程被杀（孙进程也跟着）
    return {"pid": p.pid, "in_job": True}
```

### 2) 环境净化（最实在的一道）

```python
def sanitize_env(allowed: list[str], drop_patterns: list[re.Pattern]) -> dict:
    """保留白名单 + 屏蔽含敏感词的变量。"""
    kept = {k: v for k, v in os.environ.items() if k in allowed}
    removed_count = 0
    for k in list(os.environ.keys()):
        for pat in drop_patterns:
            if pat.search(k):
                del os.environ[k]
                removed_count += 1
                break
    return {"kept_count": len(kept), "removed_count": removed_count}

# 实测：202 → 21 个变量；密钥泄漏 12 → 0
```

### 3) canary 探针（必须每次启动跑）

```python
def startup_probe() -> dict:
    """启动时跑一次 —— 等级会随系统更新悄悄失真。
    没有探过的等级不可信。
    """
    facts = canary_probe()
    return {
        "level": facts["level"],
        "facts": facts,
        "probed_at": time.time(),
        "warn": (facts["level"] == "UNKNOWN"),
    }
```

## 结果展示

| 指标 | 实测（2026-09-18 lightpack, Windows 10）|
|---|---|
| 环境净化前 | 202 个变量（其中 12 个密钥/类密钥）|
| 环境净化后 | 21 个变量 |
| 密钥泄漏 | 12 → 0 |
| 作业对象 | 孙进程连带被杀（KILL_ON_JOB_CLOSE）|
| 进程数限制 | 启用 |
| 等级 | `PROCESS_JAIL`（**不是沙箱**）|

## 说明

### ⚠️ 「不静默降级」是这条纪律的灵魂

你可以在代码里跑 `PROCESS_JAIL`，但**你必须在报告里说"这是 PROCESS_JAIL，不是沙箱"**。

代码注释、UI 文案、CLI 输出、README——**所有面向用户的地方都要说**。

### ⚠️ 进程围栏在 Windows 上要小心

挂 job 的时机：必须先挂 job 再启动子进程；子进程启动前的瞬间可能已经 fork 出孙进程。

**正确顺序**：
```
1. CreateProcessAsSuspended(...)
2. AssignProcessToJobObject(...)
3. ResumeThread(...)
```

**否则孙进程在挂上 job 之前就逃出去**。

### ⚠️ macOS sandbox-exec 是另一套体系

这个 skill 不包含 sandbox-exec 的实现 —— 那是另一个 skill 的范围。

如果你想"真沙箱"，让用户在 macOS 上跑，并在文档里说明。

### ⚠️ Linux Landlock 需要内核 ≥ 5.13

老 Linux 内核没有 Landlock，要走 seccomp + nftables。

**别假装你有**。

## 许可

MIT。