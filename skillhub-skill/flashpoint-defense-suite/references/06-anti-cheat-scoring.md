---
name: signal-anti-cheat-scoring
slug: signal-anti-cheat-scoring
displayName: 信号防刷与权重涌现
version: 0.1.0
summary: 6 类信号分级 + 权重从数据涌现 —— 让「真装到机器上跑起来」这一个行为，胜过 1000 个赞。
description: 面向独立开发者的信号防刷 skill。提供 6 类信号分级算法、SELF_TEST / SAME_OWNER / REPEAT 三条零成本规则、权重涌现机制（哪种社交行为最能在事后预测"真的有人装"，谁就获得权重）、30+ 攻击场景验证。代码示例以 Python 实现，可直接嵌入任何产品后台。
license: MIT
---

# 信号防刷与权重涌现

> **一句话**：排序的"地面真值"= 真装到机器上跑起来。我们不定义"什么是好"，只定义"什么行为算数"。

## 触发词

- 「给排行榜做防刷」
- 「社交信号怎么不被刷子淹没」
- 「怎么让真装的人权重比点赞高」
- 「排序权重怎么从数据里算出来」
- 「实现信号分级：SELF_TEST / SAME_OWNER / REPEAT / UNVERIFIED / VERIFIED / AUDITED」
- 「权重从数据涌现」

## 这是什么

一个**6 类信号分级 + 权重涌现**的设计与代码实现。

**核心思路**：
```
地面真值：「安装并跑通」          ← 最难伪造
候选预测因子：点赞/收藏/评论/转发    ← 容易被刷

我们不预设哪种社交行为重要。
让数据自己回答：哪种行为最能在事后预测"真的有人装"？
谁能预测，谁就获得权重。
```

**能解决**：
- 刷点赞 / 刷收藏 / 刷转发淹没法真实的排行榜
- 「新模型一定更好」这个假设被当成事实写进报告
- 排行榜变成"谁机器多谁赢"，而不是"谁产品好谁赢"

**不解决**：
- 沙箱 / 鉴权（其他 skill）
- 推荐系统冷启动（用真实流量，不靠"补几个点赞"）

## 单次价格

本 skill 是**免费版**。可独立嵌入任何 Python 后端，无外部依赖、无网络调用。

## 依赖安装（导入这个 skill）

```bash
# 无外部依赖，标准库 + Python 3.11+
python3 -c "import json; from pathlib import Path; print('ok')"
```

代码示例以纯算法 + 标准库形式提供，可直接复制到任何产品的服务端代码里。

## 核心概念（30 秒读完能上手）

### 六类信号分级

| 级别 | 标签 | 含义 | 能不能影响繁殖权 |
|---|---|---|---|
| `SELF_TEST` | 🧪 | 同一台机器自己跑通 | ❌（不能奖励"自己跑自己"）|
| `SAME_OWNER` | 👤 | 同一个人多台机器跑通 | ❌（防止换机器刷数）|
| `REPEAT` | 🔁 | 这台机器已经装过 | ❌（防止反复装）|
| `UNVERIFIED` | ⚠️ | 跑了但**没经过真验证** | ❌（不能换钱）|
| `VERIFIED` | ✅ | 真装到机器 + verify 通过 + 哈希对上 | ✅ |
| `AUDITED` | 🏆 | VERIFIED + 抽查通过 | ✅ |

### 权重涌现（让数据说话）

```python
import math

def _sat(n):  # 边际递减（对数）
    """刷 1000 个赞只会得到 ~7 分 —— 真装的人保持线性。"""
    return math.log1p(max(0.0, n))

def score(verified, like, save, comment, share, w):
    return (
        verified * w["verified"]      # 真装：线性
        + _sat(like) * w["like"]       # 点赞：对数
        + _sat(save) * w["save"]       # 收藏：对数
        + _sat(comment) * w["comment"]
        + _sat(share) * w["share"]
    )
```

**为什么不直接用线性计数**：
```
1000 个赞 × 权重 0.1 = 100  >  10 个真装 × 1.0 = 10    ← 赞赢
log1p(1000) ≈ 6.9 × 0.67 = 4.6   <   10 个真装 = 10    ← 修好
```

**只封"权重"封不住"影响力"** —— 系数再小，乘个无界的数照样压过去。**必须叠加"边际递减"。**

### 三条零成本规则（防自己人刷）

这三条都是**结构上不可能漏判**的：

| 规则 | 触发条件 | 一票否决 |
|---|---|---|
| **规则①** 本终端自己跑自己 | `terminal_id == owner == 这次执行` | 不算信号（权重 0）|
| **规则②** 同 owner 多台机器 | 同一 owner 已有 VERIFIED | 不算"新独立用户" |
| **规则③** 这台机器已经装过 | `installed[]` 里有这个包 | 不算信号 |

**判定与落盘必须拆开**—— 否则重启后会重复算分（2026-09-18 真踩过）。

## 调用流程

### 1) 集成到你的产品

```python
from signal_book import SignalBook  # 见 references/

book = SignalBook("./data/signals.json")
book.record_install(
    pack_id="your-package",
    terminal_id="term_abc123",
    owner="u_xyz",
    is_self_test=(terminal_id == owner),
    is_same_owner_existed=("u_xyz" in known_owners),
    is_repeat=(pack_id in installed_for_term),
)

# 30 个攻击场景验证见 tests/test_signals.py
```

### 2) 算出排序权重

```python
import weight_emergence as we

rows = [
    {"pack_id": "A", "verified": 10, "like": 5,  "save": 30},
    {"pack_id": "B", "verified": 8,  "like": 200,"save": 2},
    {"pack_id": "C", "verified": 5,  "like": 0,  "save": 40},
]

w, source, meta = we.derive_weights(rows)
# w = {"verified":1.0, "save":0.42, "like":0.0, "comment":0.0, "share":0.0}
# source = "derived" | "default"（样本不够就标 default，不硬凑）
```

### 3) 排序 + 解释（每个包拆开能看到分项）

```python
ranked = we.rank(rows, w)
for r in ranked:
    print(f"#{r['rank']} {r['pack_id']} 得分 {r['score']}")
    print(f"  {we.explain(r, w)}")  # "10 真装×1.0 + 30 save×0.42 + ..."
```

## 结果展示

| 指标 | 实测（来自 2026-09-18 lightpack 项目）|
|---|---|
| 测试场景 | 30 个攻击场景 |
| 防刷层断言 | 30/30 全过 |
| 防"刷赞压过真装" | 1000 赞（0.69）< 10 真装（10.00）|
| 防"换 owner 刷独立用户" | 同 owner 多台机器判定为 0 分 |
| 防"重启后再刷一次" | 判定与落盘拆开，**结构上不可能忘** |

## 说明

### ⚠️ 不假装"变好"

**`新模型一定更好` 这个假设通常对，但它是个假设**。

如果包没在 verify 里输出 `SCORE: x` 这样的分数，平台**只能说"变了"**——不能说"变好了"。

包自报 `SCORE: 4.2` 就能换来"变强/变弱"的判定；不输出就只说变了。

### ⚠️ "上限"必须问一句它封的是什么量

只封"权重"封不住"影响力"——系数再小，乘个无界的数照样压过去。

**"上限"必须追问：它封的到底是什么量。封权重 ≠ 封影响力。**

### ⚠️ 已知残留

- 包**自报** `SCORE:` 不可信——审核可以造高分。
- `AUDITED` 需要抽查机制（v0.5 计划）
- `owner` 自报值未做账号认证（这是大坑，靠修账号登录修）

## 许可

MIT。