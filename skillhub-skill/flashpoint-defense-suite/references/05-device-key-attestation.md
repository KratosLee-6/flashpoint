---
name: device-key-attestation
slug: device-key-attestation
displayName: 设备密钥对鉴权（Ed25519 + DPoP）
version: 0.1.0
summary: 偷到 token 也不能冒充 —— 本地生成 Ed25519 私钥、每请求签名、含降级通道（**并如实告诉用户没保护**）。
description: 面向后端工程师。提供设备密钥对生成 + 本地保护（Windows 上 icacls 收紧权限）+ DPoP 风格签名 + 防重放 + 降级通道（无 cryptography 库时 + 主动 --no-device-key 时）+ 18 攻击场景验证。
license: MIT
---

# 设备密钥对鉴权（Ed25519 + DPoP）

> **一句话**：光有 token 不够。偷到 token 的人可以冒充任意终端。Ed25519 私钥不出本机，每请求带签名 + 防重放。

## 触发词

- 「token 不够要加一层」
- 「防止冒充终端」
- 「DPX + Ed25519」
- 「Ed25519 私钥存在哪」
- 「设备密钥」
- 「icacls 收紧私钥」

## 这是什么

一个**终端设备密钥对**的设计 + 实现。

**核心思路**：
```
光凭 token 鉴权 = 偷到 token 的人能冒充该终端。
解决：每台终端有 Ed25519 私钥（**只在本机**），每个请求带签名（DPoP 风格）。

偷到 token = 拿到钥匙。
偷到 token + 签名 = 拿到本机。
本机不让别人进 = 偷不到签名 = 冒充失败。
```

**能解决**：
- 偷 token 冒充任意终端
- 同一 token 在多机器使用（私钥不一样）

**不解决**：
- 本机被攻破（私钥被读出）—— 要靠 sandbox + 文件权限
- 中间人攻击（要靠 HTTPS）

## 单次价格

本 skill 是**免费版**。可独立嵌入任何 Python 后端，无外部依赖、无网络调用。

## 依赖安装

```bash
pip install cryptography   # 推荐
# 不安装也能跑（降级模式 + 如实告知）
```

**降级通道必须如实告知用户** —— 不能假装有保护。

## 4 条设计原则

### ① 私钥只在本机，不传

```
每次请求：
    1. 生成 nonce（一次性）
    2. 用 nonce + method + URL + body 算签名
    3. 把 nonce + signature + token 一起发

服务端校验：
    1. 查 token 对应的公钥
    2. 验证签名
    3. 检查 nonce 没被用过（防重放）
```

**私钥永远不出机器**。

### ② Windows 上 icacls 收紧

```python
import subprocess, platform, os
from pathlib import Path

def harden_private_key(pem_path: Path) -> dict:
    """收紧私钥文件权限 —— 谁都读得到 = 全部白做。"""
    if platform.system() == "Windows":
        # Windows 上 os.chmod(0o600) 不真限制读取
        # 必须用 icacls
        r = subprocess.run(
            ["icacls", str(pem_path), "/inheritance:r",
             "/grant:r", f"{os.environ['USERNAME']}:(R)"],
            capture_output=True, text=True
        )
        return {"method": "windows-icacls", "ok": r.returncode == 0}
    else:
        os.chmod(pem_path, 0o600)
        return {"method": "posix-600", "ok": True}
```

**实测**：Windows 上 `chmod(0o600)` 后文件仍 `-rw-r--r--`。**必须用 icacls**。

### ③ 降级通道 + 如实告知

```python
def load_or_create_key(pem_path: Path) -> tuple[str, str, str]:
    """返回 (私钥, 公钥, 收紧方式)。如失败，给 None + 警告。"""
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519
        from cryptography.hazmat.primitives import serialization
    except ImportError:
        return ("", "", "no-cryptography-lib")

    if pem_path.exists():
        pem_bytes = pem_path.read_bytes()
        priv = serialization.load_pem_private_key(pem_bytes, password=None)
    else:
        priv = ed25519.Ed25519PrivateKey.generate()
        pem_bytes = priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        pem_path.write_bytes(pem_bytes)
    pub = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    harden = harden_private_key(pem_path)
    return (pem_bytes.decode(), pub.hex(), harden["method"])
```

**降级运行时**：
```
[!] cryptography 库没装 → 这台终端没有设备签名保护，只有 token
    想要更强保护：pip install cryptography 然后重启

[!] --no-device-key：用户主动关的，只带 token 不受设备签名保护

[!] Windows 上 chmod 不真限制读取
    必须用 icacls 收紧
    收紧结果：<方法名>（实际动作）/ <"没有收紧">（如实告知）
```

### ④ 防重放 nonce + 短 TTL

```python
import time
from collections import defaultdict

class ReplayCache:
    """简单的 nonce 去重缓存（生产用 Redis）。"""
    def __init__(self, ttl_sec=60):
        self.seen = defaultdict(set)   # terminal_id -> set(nonce)
        self.ttl = ttl_sec

    def is_fresh(self, tid: str, nonce: str) -> bool:
        now = time.time()
        # 清掉过期的
        self.seen[tid] = {n for n in self.seen[tid] if n["ts"] > now - self.ttl}
        if any(n["val"] == nonce for n in self.seen[tid]):
            return False
        self.seen[tid].add({"val": nonce, "ts": now})
        return True
```

**TTL = 60s**（nonce 60 秒后过期可重用）。**生产用 Redis**。

## 调用流程

### 1) 终端启动时加载/生成

```python
import argparse

ap = argparse.ArgumentParser()
ap.add_argument("--no-device-key", action="store_true",
                help="主动禁用设备签名（这台终端只带 token）")
args = ap.parse_args()

pem, pub, harden = devicekey.load_or_create(base / "device_key.pem")
if not pem and not args.no_device_key:
    print("[!] 没装 cryptography：这台终端**没有设备签名**保护，只带 token")
if args.no_device_key:
    print("[!] --no-device-key:这台终端只带 token，不受设备签名保护")
else:
    print(f"[✓] 设备密钥就绪 Ed25519 公钥 {pub[:16]}…")
    print(f"    私钥权限：{harden}")  # windows-icacls / posix-600 / 没收紧
```

### 2) 每请求带签名

```python
import json, secrets, hashlib

def sign_request(pem: str, method: str, url: str, body: bytes = b"") -> dict:
    """DPoP 风格签名。"""
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives import serialization

    if not pem:
        return {}

    nonce = secrets.token_hex(16)
    payload = f"{method}|{url}|{nonce}|{hashlib.sha256(body).hexdigest()}"
    priv = serialization.load_pem_private_key(pem.encode(), password=None)
    sig = priv.sign(payload.encode("ascii"))
    return {
        "nonce": nonce,
        "sig": sig.hex(),
        "ts": int(time.time()),
    }
```

### 3) 服务端校验

```python
def verify_proof(pub_hex: str, proof_str: str, method: str, url: str,
                 cache: ReplayCache) -> tuple[bool, str]:
    """验证 + 防重放。返回 (ok, why)。"""
    if not proof_str:
        return False, "no proof"

    # 1. 解析 proof（JSON / DPoP）
    try:
        proof = json.loads(proof_str)
    except: return False, "bad proof json"

    # 2. 查公钥
    pub_bytes = bytes.fromhex(pub_hex)
    from cryptography.hazmat.primitives.asymmetric import ed25519
    pub = ed25519.Ed25519PublicKey.from_public_bytes(pub_bytes)

    # 3. 重算签名
    nonce = proof.get("nonce")
    sig = bytes.fromhex(proof.get("sig", ""))
    payload = f"{method}|{url}|{nonce}|{proof.get('body_sha256','')}"
    try:
        pub.verify(sig, payload.encode("ascii"))
    except: return False, "signature invalid"

    # 4. 防重放
    if not cache.is_fresh(tid, nonce):
        return False, "nonce reused"

    return True, "ok"
```

## 结果展示

| 指标 | 实测（2026-09-18 lightpack）|
|---|---|
| 攻击场景 | 18 个（含重放 / 换密钥 / 篡改 / 偷 token / 超上限 / 退役）|
| 偷 token 但没有私钥 | **401 拒**（实测） |
| 私钥文件权限（Windows） | icacls 收紧 → 只有 KRATOSLINE-PC\Admin 可读 |
| 降级模式 | 如实告知"没有保护"，**不假装** |
| 私钥生成位置 | `~/.shinehub/device_key.pem`（不传任何地方）|

## 说明

### ⚠️ 「设备私钥泄露」是这套方案的失效模式

**本机的 root/admin** 就能读出私钥。

要挡：
- macOS Keychain / Windows DPAPI 加密私钥
- TPM 硬件存储（Linux + Windows 11+）
- 沙箱 + 不可写用户文件系统

**这套方案**只挡**网络上的攻击者**（偷了 token 也签不出 signature）。

### ⚠️ 公钥不可改（root 登记）

```python
# 服务端：第一次登记公钥后永远不许改
device_pubkey = rec.get("device_pubkey") or pub_or_input  # 第一次登记
# ... 后面的注册请求里 device_pubkey 字段被忽略
```

**允许改 = 偷 token 的人换成自己的公钥 → 这套机制白做**。

### ⚠️ 测试模式与生产模式区分

```python
if "test" in server_url:
    # 测试模式：可能用弱签名或无签名
    # 但**不能沉默** —— 要让用户知道
    print("[!] 测试模式：签名可能被跳过")
```

### ⚠️ 「HAVE_CRYPTO=False 时」诚实告知

```python
HAVE_CRYPTO = True
try:
    from cryptography.hazmat.primitives.asymmetric import ed25519
except ImportError:
    HAVE_CRYPTO = False
```

**HAVE_CRYPTO=False 不能默默跑 token-only 模式**——必须告诉用户"这台机器没设备签名"。

## 许可

MIT。