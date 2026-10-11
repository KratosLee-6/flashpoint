# AI 闪光点 · Skill 碰撞台

**选两个 Skill 包，碰撞出一个新能力。**

这是一个本地优先的桌面应用，把两个 AI Agent Skill 包放在一起，自动分析双方能力结构，发现跨域碰撞机会，生成包含可核验证据的新 Skill 方案——并最终物化为可运行的代码包。

## 它做什么

```
选两个 Skill 包
       ↓
自动提取能力结构（8 个与领域无关的结构轴）
       ↓
计算碰撞落点（失效面优先，锚点可核）
       ↓
语义分段 + 逐段校验 + 定向重试
       ↓
生成含逐字引文的新 Skill 方案
       ↓
物化为真实文件包 + 运行 selftest（能跑得动才算数）
```

## 核心特性

- **快速碰撞**：选两个包、一次确认，其余全自动
- **专家模式**：逐文件审查材料、逐条引文核对、完整请求预览
- **物化并自测**：方案 → 真实文件包 → selftest 退出码判定
- **可核验**：每条证据都是母包里的逐字引文，带文件、行号和哈希
- **诚实边界**：方案不装完整——哪里证据不足，它自己列出来
- **本地优先**：材料不出本机（模型调用除外，需单独确认）

## 快速开始

```bash
# 安装依赖
pip install pyinstaller==6.22.3
cd packages/shell && npm install && npm run build && npm run sidecar

# 或者直接用 Python 启动融合服务
python tools/serve-local.py --port 4178
# 然后浏览器打开 http://127.0.0.1:4178/app/
```

### Python 依赖

```
Pillow>=12.0
pyinstaller>=6.22（仅打包 sidecar 时需要）
```

前端无构建依赖——纯 HTML/CSS/JS，打开即用。

## 测试

```bash
# Python 融合引擎测试（660+ 条）
python -m pytest app/fusion/ -q

# 前端 Node 测试（37 条）
node --test app/runtime.test.cjs app/app.test.cjs app/storage.test.cjs

# Playwright UI 测试（设计规范 + 性能守卫）
cd app && npx playwright test tests/ui/design.spec.cjs
```

## 技术架构

| 层 | 技术 | 说明 |
|---|---|---|
| 前端 | 原生 HTML/CSS/JS | 零框架、零构建、单文件离线可用 |
| 融合引擎 | Python 3.10+ | 确定性算法 + 可选模型调用 |
| 桌面壳 | Tauri (Rust) | NSIS 安装包 + sidecar 自动拉起 |
| 模型接口 | OpenAI / Anthropic / Gemini 协议 | 用户自选端点与密钥 |

### 设计原则

- **确定性优先**：碰撞落点由本地算法算出，不靠模型猜
- **分段校验**：整份方案拆成自洽块，逐块本地校验，只重试失败的段
- **先修后判**：引文重锚定修转写误差→产物名推断补扩展名→然后才判
- **验收链不放松**：65 项契约条件逐一执行，编造引文零容忍
- **诚实体边界**：能跑得动 ≠ 比父本好；方案不装完整，缺口自己列

## 测试数据（真实模型）

| 包对 | 模型 | 结果 |
|---|---|---|
| 润色去AI味 × 防刷计分 | glm-4.5-air | accepted |
| 防骗话术 × 证据采集 | glm-4.5-air | accepted |
| 育儿顾问 × 设备密钥证明 | glm-4.5-air | accepted |
| 编程助手 × 默认拒绝权限 | glm-4.5-air | accepted 2/2 |
| A/B 对照（n=16 两臂） | glm-4.5-air | 整份 0/16 vs 分段 **14/16** |

物化的融合包 selftest 结果：**3 个包全部可运行**（含 11–14 项检查全过）。

## 许可

MIT
