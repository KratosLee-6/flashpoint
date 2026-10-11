<div align="center">

<img src="docs/architecture.png" alt="AI 闪光点 · 碰撞台分层架构" width="70%" />

# ✨ AI 闪光点 · Skill 碰撞台

**选两个 Skill 包，碰撞出一个新能力。**

本地优先 · 逐字引文可核 · 能跑得动才算数

[![Version](https://img.shields.io/badge/version-V1.0.0-1658F5?style=flat-square)](VERSION.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-22c55e?style=flat-square)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-660%20passed-brightgreen?style=flat-square)](app/fusion/)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)]
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Web-9cf?style=flat-square)]

[功能](#-功能) · [怎么工作的](#-它是怎么工作的) · [实测数据](#-实测数据) · [快速开始](#-快速开始) · [架构](docs/architecture.html)

</div>

---

## 📖 这是什么

你手里有一堆 AI Skill 包——做教育的、做安全审计的、做数据可视化的。

你想过把它们**碰在一起**吗？不是拼接，不是堆砌——是把 A 的方法论嫁接到 B 的场景上，产出两个父本都做不到的新能力。

三周前我试了一次，碰撞结果像两份说明书合订本。这三周就干了一件事：让它真的能跑。

**AI 闪光点**是一个本地优先的桌面应用，把两个 AI Agent Skill 包放在一起，自动分析双方能力结构，发现跨域碰撞机会，生成包含可核验证据的新 Skill 方案——并最终物化为可运行的代码包。

---

## 📸 界面

| 快速碰撞 · 三步出方案 | 融合工作区 · 专家模式 |
|---|---|
| <img src="screenshots/实拍-02-快速碰撞面板.png" width="100%" /> | <img src="screenshots/实拍-01-融合工作区.png" width="100%" /> |
| 选两个包，勾一次确认，等结果 | 逐文件审查材料、逐条引文核对 |

| 物化并自测 · 能跑得动 | 模型设置 · 多协议支持 |
|---|---|
| <img src="screenshots/实拍-04-快速碰撞已填.png" width="100%" /> | <img src="screenshots/实拍-03-模型设置.png" width="100%" /> |

> 🎬 **视频演示**：*制作中——将展示从选包到物化自测的完整流程。*

---

## ✨ 功能

### ⚡ 快速碰撞

选两个 Skill 包（下拉选择或填路径）→ 勾一次确认 → 等几分钟出方案。

<details>
<summary>自动化了什么</summary>

- **上下文自动精简**：SKILL.md 必留，其余按体积升序装满预算，逐文件留痕
- **语义分段**：整份方案拆成自洽块（2 个能力画像 + 方向审查 + 3 个候选），每块独立生成
- **段级确定性校验**：形状、枚举、数组边界、引文逐字归位、认领自洽——每段一返回就判
- **定向重试**：只重试失败的段，把校验器的具体指控写进重试提示词
- **证据重锚定**：引文转写误差自动修回真实文件内容（0.95/0.90 双阈值，唯一命中才归位）
- **产物名推断**：文件名写到点就停的场景，按用途推断扩展名
</details>

### 🔬 专家模式

需要逐文件审查材料、逐条核对引文、检查完整请求内容？切到专家模式，一步一步都在。

<details>
<summary>专家模式包含</summary>

- 完整材料清点（含不可读文件的安全排除列表）
- 逐文件发送选择（勾选 + 分类 + 取舍理由）
- 能力结构与碰撞落点可视化
- 规则方向排序与审查
- 完整请求预览（含系统提示词、schema、源文件正文）
- 连接检查（计费确认分离）
- 碰撞结果逐候选审查（贡献来源、证据链、反证检查、评估计划）
</details>

### 📦 物化并自测

碰撞产出的是方案（纸上的东西）。「物化并自测」把它生成**真实的文件包**（脚本、模板、schema），再让包给自己出题——十几项检查逐条跑，**全过才算数**。

<details>
<summary>物化流程</summary>

1. **生成段**：按方案逐文件产出真实内容（每个文件一次调用，哨兵裸文本传输）
2. **本地校验**：路径与方案一致、Python 可编译、JSON 可解析、内容非空
3. **运行段**：自动生成 selftest.py（用合成数据调用包内真实代码），退出码判定
4. **修复回路**：selftest 失败时把真实 traceback 交给模型定点修，修完重考
</details>

### 📦 补足闭环

碰撞方案自己会列缺口（`needs_ai_fill` + `gaps_to_fill`）。对第 5 类候选，可以发起补足请求——模型带缺口清单再答一次，补完由同一个核对器复评。

---

## 🔬 它是怎么工作的

不依赖模型"理解"两个 Skill 的语义——而是用**确定性算法**做四件事：

### 1. 能力结构抽取

从每个 Skill 包中提取 8 个与领域无关的结构轴：

> 步骤 · 条件 · 约束 · 校验 · 交付物 · 证据引用 · 边界 · 受众

法律包和汽修包可以零共同词汇，但都有步骤和约束。

### 2. 碰撞落点计算

在结构轴的交集上找碰撞机会——**失效面优先**（A 在 B 的某个场景下没有可援引规则），退化为机制配对。每个碰撞候选都带**可锚定的抓手**（母包里的逐字原文），不是空洞的"你们可以合作"。

### 3. 分段生成 + 逐段校验

方案拆成自洽块（2 个能力画像 + 方向审查 + 3 个候选方案），每块独立生成、独立校验。引文重锚定修转写误差，产物名推断补扩展名，证据编造零容忍。

### 4. 物化为真实文件包

通过验收的方案生成真实文件包（脚本、模板、schema），再让包给自己出题——selftest 退出码判定「能不能跑得动」。

---

## 📊 实测数据

### A/B 对照（同材料同模型各 16 次）

| | 通过率 | 说明 |
|---|---|---|
| 一次性输出完整方案 | **0/32** | 写两万字必崩 |
| 分段 + 逐段校验 + 定向重试 | **23/25** | 错哪补哪，只重试坏的那块 |

> 差距不在模型变聪明——在工序。把"一次写对两万字"换成"每块考完再拼"。

### 跨域碰撞（4 个 SkillHub 包 × 本地包，全部 accepted）

| 包对 | 产出 | selftest |
|---|---|---|
| 🛡 防骗话术 × 证据采集 | 来源可信度验证器 | — |
| 🎨 润色去AI味 × 防刷计分 | 文本质量门禁 | ✅ |
| 👨‍👩‍👧 育儿顾问 × 设备密钥证明 | 建议签名 + 防重放 | ✅ 14 项 |
| 💻 编程助手 × 默认拒绝权限 | AI 操作权限扫描器 | ✅ 14 项 |

---

## 🏗 架构

📊 **可视化分层架构图**：[docs/architecture.html](docs/architecture.html)

```
app/
├── index.html           入口（CSP 严格，无外部依赖）
├── styles.css           三主题：paper / forest / night
├── core.js              数据模型 + 模型访问管理
├── spark-fx.js          签名特效（WebGL 极光 + 碰撞粒子）
├── fusion-ui.js         融合工作区 UI（快速 / 专家双模式）
├── app.js               应用主逻辑（资料库/设置/路由）
└── fusion/              Spark Core 引擎（Python）
    ├── capability.py        能力结构抽取（8 轴 + 桥接 + 失效面）
    ├── collide.py           本地碰撞候选生成
    ├── segments.py          语义分段 + 段级确定性校验
    ├── contract.py          65 项验收契约
    ├── evidence_reanchor.py 引文重锚定（0.95/0.90 双阈值）
    ├── engine.py            编排：prepare → 分段 → 验收链
    ├── materialize.py       方案 → 真实文件包 → selftest
    ├── server.py            HTTP 服务（loopback + 令牌）
    └── ...
```

---

## 🚀 快速开始

### 前端（零构建，打开即用）

```bash
cd app
python -m http.server 8080
# 打开 http://localhost:8080
```

### 融合服务（Python 后端）

```bash
pip install Pillow

python tools/serve-local.py --port 4178
# 打开 http://127.0.0.1:4178/app/
```

### 桌面安装包

```bash
cd packages/shell
npm install && npm run build && npm run sidecar
npx tauri build
# 安装包在 src-tauri/target/release/bundle/nsis/
```

### 模型配置

在「数据与偏好 → 调用模型」里配置接口地址、模型名和密钥。仅本次会话内存，不落盘。支持：

| 协议 | 服务商 |
|---|---|
| OpenAI | GPT / DeepSeek / MiniMax / 零一万物 / Kimi … |
| Anthropic | Claude |
| Gemini | Google Gemini |
| 本机模型 | Ollama / LM Studio（不需要密钥） |

---

## 🧪 测试

```bash
# Python 融合引擎（660+ 条）
python -m pytest app/fusion/ -q

# 前端 Node 测试（37 条）
node --test app/runtime.test.cjs app/app.test.cjs app/storage.test.cjs

# Playwright UI（设计规范 + 性能守卫 + 融合工作区）
cd app
npx playwright test tests/ui/design.spec.cjs
npx playwright test --config playwright.fusion.config.cjs
```

---

## 📦 测试用 Skill 包

以下 Skill 包用于碰撞测试（从 [SkillHub](https://skillhub.cn/skills?sortBy=score) 下载）：

| 包 | 领域 | 来源 | 大小 |
|---|---|---|---|
| anti-fraud | 防骗话术识别 | SkillHub | 185 KB |
| libai | 中文润色去 AI 味 | SkillHub | 286 KB |
| parenting-expert | 育儿顾问 | SkillHub | 657 KB |
| dev-expert | 编程专家 | SkillHub | 1 MB |

本地 curated 包：

| 包 | 领域 |
|---|---|
| anti-cheat-scoring | 防刷信号计分 |
| consent-default-deny | 默认拒绝授权 |
| device-key-attestation | 设备密钥证明 |
| evidence-collector | 证据采集 |
| outline-writer | 写作提纲 |
| sandbox-design-discipline | 沙箱权限设计 |
| package-permission-design | 包权限设计 |
| ast-static-scan-arch | AST 静态扫描 |

---

## 📋 诚实体边界

| ✅ 已验证 | ❌ 未验证 |
|---|---|
| 碰撞方案 65 项契约通过 | 比父本技能更好 |
| 证据逐字引文可核 | 新 Skill 长期运行稳定性 |
| 物化包 selftest 可运行 | 商业价值与市场规模 |
| 4 个跨域包对全部 accepted | 更大样本量的统计显著性 |

---

## 🗓 Roadmap

- [ ] 五基线对照实验（效果验证）
- [ ] SkillHub 在线搜索集成
- [ ] 更多模型适配（glm-4.7 / Claude / GPT）
- [ ] 桌面自动更新通道

---

## 🤝 贡献

欢迎 Issue 和 PR。提交前请跑：

```bash
python -m pytest app/fusion/ -q
node --test app/runtime.test.cjs app/app.test.cjs app/storage.test.cjs
```

---

## 📄 License

[MIT](LICENSE)
