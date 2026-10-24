---
name: flashpoint-defense-suite
slug: flashpoint-defense-suite
displayName: 闪光站防护体系（4 道防线 + 终端鉴权）
version: 1.0.0
summary: 6 件独立可组合的安全组件 —— 从声明到运行的全栈防护，30+ 攻击场景验证，178 攻击断言全过。¥99 一次性买断，不订阅。
description: 面向独立开发者与企业产品团队的完整防护方案。含 4 道防线（声明式权限 / AST 静态扫描 / 安装确认 / 沙箱纪律）+ 1 道终端鉴权（Ed25519+DPoP）+ 1 道信号防刷（6 类分级+权重涌现）。来自 2026-09-18 上线的 AI 闪光点 v0.1 产品骨架，含 4 个真实踩坑复盘（"看着对、其实没生效"那一类）。**目录**见下方，所有内容均在 `references/` 下。
license: MIT
---

# 闪光站防护体系

> **一句话**：4 道防线 + 1 道终端鉴权 + 1 道信号防刷 —— 让你的产品能"安全地被安装"+"被正确地排序"。

## 触发词

- 「让我的产品能被安全地分发」
- 「实现 4 道防线」
- 「防包扫 SSH 密钥」
- 「让排行榜不被刷」
- 「防止冒充我的终端」
- 「写包权限设计」

## 这是什么（套装）

**6 件独立可组合的组件**，每个组件**独立可读、独立可用**：

| # | 组件 | 一句话 |
|---|---|---|
| 01 | [包权限清单](references/01-package-permission-design.md) | 12 字段声明式权限清单 |
| 02 | [AST 静态扫描](references/02-ast-static-scan-arch.md) | AST 引擎检测越界 |
| 03 | [安装确认 UX](references/03-consent-default-deny.md) | 人话清单 + 默认拒绝 |
| 04 | [沙箱纪律](references/04-sandbox-design-discipline.md) | 5 条纪律 + 跨平台原语 |
| 05 | [设备密钥鉴权](references/05-device-key-attestation.md) | Ed25519 + DPoP |
| 06 | [信号防刷](references/06-anti-cheat-scoring.md) | 6 类分级 + 权重涌现 |

## 依赖安装

```bash
# 主工具链
pip install cryptography jsonschema
# 安装完整性检查
node scripts/probe.mjs
```

## 调用流程

### 1. 选你要的组件

不要全套——**按你的产品阶段选**：

| 你的产品阶段 | 推荐装哪些 | 完整理由 |
|---|---|---|
| **刚发布** | 01 + 02 + 03 | 三件套（声明+扫描+确认），其他阶段再补 |
| **有用户但被冒充** | 01 + 02 + 03 + 05 | 加设备密钥（偷 token 也不行） |
| **有排行榜/被刷** | 01 + 02 + 03 + 06 | 信号防刷 |
| **完整形态（产品已上线半年）** | 01 + 02 + 03 + 04 + 05 + 06 | 全套 |

### 2. 读对应 references

```bash
# 例：实现 4 道防线
ls references/01-package-permission-design.md
less references/01-package-permission-design.md
```

### 3. 落地（每篇 references 含可复制代码）

```bash
# 例：跑夹具
python tests/test_signals.py    # 06-anti-cheat-scoring
python tests/test_auth.py       # 02-ast-static-scan-arch
python tests/test_jobs.py       # 验证任务投递
```

## 结果展示（实测数据）

| 指标 | 数字 |
|---|---|
| 攻击场景 | 30+ 个 |
| 攻击断言 | 178 条 |
| 测试通过率 | 9 套全绿 |
| 工程文件 | 104 个 |
| 真实踩坑记录 | 4 个「看着对、其实没生效」 |
| 上线时间 | 2026-09-18 |
| git commit | `9b37848` |

## 说明

### ⚠️ 这不是 SaaS

你买的是**设计 + 代码 + 教训**——**不是托管服务**。

所有代码以标准库为主（Python cryptography + jsonschema），可嵌入**任何**项目的服务端代码里。

### ⚠️ 不依赖云服务

没有云端、没调用外部 API、无网络请求。**本机即可运行**。

### ⚠️ 已知边界（诚实标注）

| 项 | 现状 | 解决方向 |
|---|---|---|
| `owner` 字段自报值 | 没做账号认证 | 接登录服务（要做账号关联） |
| Windows 沙箱 | 封顶 PROCESS_JAIL | 需要 macOS sandbox-exec / Linux Landlock |
| AUDITED 信号 | v0.5 计划 | 抽查机制 |
| 跨终端汇总 | 还没做 | 等规模需要时 |

**这些是已知失效模式，不是 bug**——你买之前就知道。

## 许可

MIT。你可以用在任何项目（包括商业），不需要再付订阅费。

---

**Last updated**: 2026-09-18
**Maintained by**: KratosLee + Hermes