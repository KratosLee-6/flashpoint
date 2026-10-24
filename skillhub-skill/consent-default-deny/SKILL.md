---
name: consent-default-deny
slug: consent-default-deny
displayName: 安装确认 UX 与默认拒绝
version: 0.1.0
summary: 第一次装包时给人话清单 + 默认拒绝 + 越界自动拒 —— 不是「我同意以上条款」，是「我要装它读你的键盘日志了，同意吗」。
description: 面向独立开发者与产品设计师。提供安装确认 UX 设计、内容哈希校验、人话权限清单、默认拒绝、越界自动拒绝、无交互默认拒绝、负面断言必须由静态检测背书（不能由声明背书）。代码示例以 Python + JSON Schema 实现。
license: MIT
---

# 安装确认 UX 与默认拒绝

> **一句话**：装包不是法律文书对话。是「要不要让它读你的 SSH 密钥，yes/no」。

## 触发词

- 「设计包安装确认」
- 「让人话权限清单」
- 「默认拒绝安装」
- 「无交互默认拒绝」
- 「实现 consent flow」
- 「防止静默安装」

## 这是什么

一个**安装确认 UX** 的设计 + 实现。

**核心思路**：
```
❌ 错：I agree to the terms of service.
✅ 对：I want to install this.
       This package will:
         [✓] read your $HOME/.ssh/id_rsa
         [✓] connect to api.example.com
         [✗] NOT read your SSH keys (verified by static scan)

       yes / no
```

**能解决**：
- 用户装包时不知道「它到底要干啥」
- 「我同意所有条款」式的法律钩子
- 装完才发现自己电脑被开了后门

**不解决**：
- 包作者故意撒谎（要靠 package-permission-design + ast-static-scan-arch）
- 用户**不管装什么**都点 yes（教育问题，不是技术问题）

## 单次价格

本 skill 是**免费版**。可独立嵌入任何 Python 后端，无外部依赖、无网络调用。

## 依赖安装

```bash
# Python 3.11+ 标准库
python3 -c "import json, hashlib; print('ok')"
```

## 6 条核心设计原则

### ① 人话，不是法律

```
❌ I agree to the terms of service and privacy policy.
✅ I'll read 1 file and 1 directory. Specifically:
   - $HOME/.config/yourapp/** (your config files)
   - not your SSH keys
   - not your .env
```

### ② 正面 + 负面 + 静态检测背书

| 类型 | 来源 | 谁来保证 |
|---|---|---|
| **正面**（"我会读 X"）| manifest.json 声明 | 作者自己 |
| **负面**（"我不会读 X"）| **静态扫描结果** | 平台 |
| **数据流向**（"会连 X.com"）| manifest 声明 + 静态扫描 | 作者 + 平台 |

**核心**：**负面断言必须由静态检测背书**。

```
❌ 错：声明里写"不读 SSH 密钥" → 实际代码里 read_text(Path.home()/".ssh"/"id_rsa")
✅ 对：扫描器扫到 read_text(.ssh/id_rsa) → "没读"那个复选框**取消勾选**
```

**让骗子写自己的品行证明 = 自欺欺人**。

### ③ 默认拒绝（Default Deny）

```python
def on_install_request(pack_id: str, auto_yes: bool = False):
    # 1. 跑扫描
    scan = static_scan(pack_id)  # → 5 类越界
    # 2. 任何 critical 越界 = 拒绝（auto_yes 都不能绕过）
    if scan["critical"]:
        return {"status": "DENIED",
                "why": "scan found " + scan["critical"][0]["desc"],
                "no_override": True}
    # 3. 没有越界，等用户同意
    if not auto_yes:
        return {"status": "WAITING_USER",
                "prompt": render_consent(pack_id, scan)}
    # 4. 用户同意过（同意记录在 consents.json）
    if user_already_consented(pack_id, pack_hash()):
        return {"status": "OK"}
    return {"status": "WAITING_USER"}
```

**没有 `--force` 参数**。**有 `--yes` 参数**（自动通过所有未越界检查），**但有 critical 越界时一律拒，不接受绕过**。

### ④ 同意绑内容哈希，不绑包名

```
❌ 错：用户同意了 pack_id="your-app" → 装新版（带后门的"升级版"）→ 不再问
✅ 对：同意记录存的是用户同意时的 manifest 内容哈希 + 包名
       装新版时哈希变了 → 重新确认
```

```python
def user_already_consented(pack_id: str, current_hash: str) -> bool:
    record = load_consent(pack_id)
    if not record:
        return False
    return record["digest"] == current_hash
```

**否则就是「同意一次就永远信任」 → 攻击者发个 1.0.1 偷数据**。

### ⑤ 无交互默认拒绝

```
agent 在 CI / 定时任务 / 后台运行时，没有 stdin → 默认拒绝。
```

```python
if not sys.stdin.isatty() and not auto_yes:
    return {"status": "DENIED", "why": "no TTY, default deny"}
```

**没有用户在场 = 没有同意 = 不装**。

### ⑥ 负面断言不能由声明背书

```
✅ 正确流程：
   1. manifest 声明 "I claim: not read .ssh"
   2. 静态扫描代码：没找到 Path.home()/".ssh" 访问
   3. "没读"复选框**自动勾选**
   4. 用户看到「没读 .ssh 已被静态扫描确认」

❌ 错：
   1. manifest 声明 "I claim: not read .ssh"
   2. 没扫描
   3. "没读"复选框按声明勾上
   4. 用户以为「没读」是真，被骗
```

## 调用流程

### 1) 渲染人话权限清单

```python
def render_consent(pack_id: str, scan: dict) -> str:
    man = load_manifest(pack_id)
    out = [f"I want to install {man['name']} {man['version']}.\n"]
    out.append("This package will:\n")

    # 正面（声明 → 人话）
    for k, v in man["permissions"].items():
        if not v: continue
        out.append(f"  [✓] {perm_to_human(k)}: {v}")

    # 负面（声明里写"不读" → 静态扫描验证）
    for claim in man.get("claims_neg", []):
        if scan["verified_neg"].get(claim):
            out.append(f"  [✗] NOT {claim_to_human(claim)} (verified by static scan)")
        else:
            out.append(f"  [!] NOT {claim_to_human(claim)} (UNVERIFIED — your call)")

    return "\n".join(out)
```

### 2) 记录同意

```python
import hashlib, json
from pathlib import Path

def record_consent(pack_id: str, manifest_dict: dict):
    digest = hashlib.sha256(
        json.dumps(manifest_dict, sort_keys=True).encode()
    ).hexdigest()[:32]
    rec = {"digest": digest, "at": now()}
    save_consent(pack_id, rec)
```

### 3) 校验同意记录

```python
def check_consent(pack_id: str, current_manifest: dict) -> dict:
    record = load_consent(pack_id)
    if not record:
        return {"ok": False, "why": "never consented"}
    current_digest = hashlib.sha256(
        json.dumps(current_manifest, sort_keys=True).encode()
    ).hexdigest()[:32]
    return {"ok": record["digest"] == current_digest,
            "stored": record["digest"],
            "current": current_digest}
```

### 4) 越界自动拒（自动 yes 也不绕过）

```python
def can_bypass(scan_result, auto_yes):
    if scan_result.get("critical"):
        return False  # 有 critical = 必拒
    if scan_result.get("high") and not auto_yes:
        return False
    return auto_yes
```

**测试**（`test_consent.py`）：
```python
def test_no_force_can_bypass_critical():
    result = on_install_request("evil-pack", auto_yes=True)
    assert result["status"] == "DENIED"
    assert "no_override" in result
```

## 结果展示

| 指标 | 实测（2026-09-18 lightpack）|
|---|---|
| 同意绑字段 | 内容哈希 + 包名 |
| 默认拒绝触发条件 | 无 TTY / 有 critical 越界 |
| 负面断言来源 | **仅静态扫描**，不来自声明 |
| 测试场景 | 29 个（critical 自动拒 / 无交互拒 / 哈希变更重问 / 负面断言撒谎检测）|
| `--force` 参数 | **不存在**（`--yes` 可加速但不绕过 critical）|

## 说明

### ⚠️ 「不要让骗子写自己的品行证明」

**声明不能背书负面断言**。

只有**静态扫描 + 沙箱**这种独立证据源能背书「没做 X」。

### ⚠️ 「I agree to terms」式对话框是反 UX

用户**根本不会读**。

人话 + 正面/负面分栏 + 静态检测背书 = 用户**能 5 秒读完 + 做出有意义的决策**。

### ⚠️ 同意记录必须校验

不要相信**「用户曾经同意过 X」** —— 用户同意的是 X 当时的内容哈希。

X 改了 → 重新同意。**这才能挡住"装一个干净包然后发新版加后门"**。

### ⚠️ 「不假装的」范围

本 skill 假设：

- 用户**愿意看 5 行人话**（不会同意一个 50 行的法律文本）
- 包作者**至少**会声明大致权限（不详细声明 = 越界扫描器挡）

如果用户是**企业合规**，要走单独的 `commercial-consent-audit` skill，不在本 skill 范围。

## 许可

MIT。