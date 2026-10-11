# 44 · Spark 核心黑盒与快速碰撞（2026-10-11）

> 机主两项指示：① 碰撞流程太复杂，要有**更直接的简化版本**；② 把碰撞算法做成
> **核心黑盒**，推广与商业变现吃红利，迭代更强后再开源。
> 上级：28 号文（合取结构改造，已 A/B 验证）、41 号文（可核验主张）、39 号文 §7（算法是核心价值）。

## 一、快速碰撞（已落地，本文件 §三）

**简单模式 = 选两个包 → 一次确认 → 等结果。** 其余全部自动：

| 原专家流程（保留） | 快速模式自动完成 |
|---|---|
| 填 A/B 路径 + 资源映射 + 已知缺口 | 下拉选包（本地 curated / SkillHub 下载区）；缺口留空＝如实未知 |
| 02 层逐文件勾选与理由 | `context.auto_selection`：SKILL.md 必留 + 体积升序装满 30k 预算，**逐文件 reason 照样留痕** |
| 手填端点/模型/密钥 | 「数据与偏好 → 调用模型」一次配置，长期取用 |
| 连接检查 + 双同意 | 单次确认（发送材料 + 可能费用）合一 |
| 请求字符预算 | 自动精简，不超预算 |

边界与纪律：**校验一道未放宽**——auto_selection 走正式 `select_context` 重签
（verify 必过、capability/collision 随之重算），每个文件的去留有逐条 reason；
专家模式完整保留（02 层逐文件审查是它的全部价值，不删）。

落地：`context.auto_selection` → server `inventory/prepare` 的 `auto_select` 参数
（选择定稿后 packet 直接留给 run）→ GUI 快速面板（默认视图，专家模式一键切回）。
测试：`test_quick_mode.py` 3 条 + `AutoSelectionTests` 2 条。

## 二、Spark 核心黑盒（边界先行，打包现状如实）

### 2.1 核心边界（今后按此说话）

**Spark Core = `app/fusion/` 全部**（除测试与留档）：

```
capability.py      能力结构层（8 轴抽取、桥接、失效面）——跨域发现的根
collide.py         本地碰撞候选（失效面优先，锚点可核）
directions.py      规则方向层（离线降级路径）
segments.py        语义分段与段级校验（合取结构改造的落地）
engine.py          编排：prepare → 模型 → 验收链
contract.py        65 项验收契约（可核验的执行者）
skeleton.py        骨架（确定性形状收回）
evidence_reanchor.py / json_repair.py   证据与语法修复（先修后判）
materialize.py     方案 → 可运行包体 → selftest
context.py         上下文选择（含 auto_selection）
```

调用方只有三处：`server.py`（HTTP 壳）、`fusion-ui.js`（界面，只经 HTTP）、
`tools/*`（实验与验收工具）。**核心没有反向依赖**——这就是可抽取性的定义。

### 2.2 商业黑盒现状（如实）

- **现在就能卖**：安装包里的 `flashpoint-fusion.exe` 是 PyInstaller 打包的
  sidecar——**算法全部在这个二进制里，分发的已经是黑盒**；前端只是界面。
- **诚实的保护边界**：PyInstaller 可被解包、.pyc 可被反编译——它挡住的是
  「顺手看一眼」，挡不住有意逆向。真正的保护按阶段升级：
  ① 眼下：黑盒分发 + 核心不进任何开源仓库（核心不入 public 的纪律已有）；
  ② 付费版/云端：核心逻辑迁服务端 API（黑盒的完全形态），本地只留界面；
  ③ 桌面版加固（可选）：Nuitka/Cython 编译核心模块，反编译成本数量级上升。
- **护城河的如实排序**：短期内护城河不是代码保密（可逆向），
  而是 ① 测量与迭代节奏（两对 A/B、8 包对矩阵、fail 分类器，都是公开侧没有的
  实证资产）② 包源生态与包源选择体验 ③ 「可核验 + 能跑得动」的产品语义。

### 2.3 未来开源路径（机主：迭代更强后再开源）

1. **抽取**：`app/fusion/*` → `packages/spark-core`（纯库，无 HTTP）——
   边界已满足，做时只需移动 + 改 import + 一份 `spark-core` 的公开 API 文档。
2. **分层开源**：界面与 example 开源（获客），spark-core 以商业许可发布
   （dual-license）；或核心开源 + 增值（云验证、包源市场、企业规则库）收费。
3. **开源前清点**：core 内不得有机主数据/密钥/私有包路径——`source_packs`
   的 curated 扫描是运行时行为，无嵌入；评估留档已全部脱敏。

## 三、验证

- `test_quick_mode.py` 3 条（auto_select 预算内+verify 过 / 专家流程 packet 置 None
  的门语义不变 / prepare+auto_select 可直接 preview）；
- `AutoSelectionTests` 2 条（verify 过 / SKILL.md 必留 + 逐条 reason）；
- fuse-pair 的 lean_selection 改为转发产品实现（消除双份漂移）。
