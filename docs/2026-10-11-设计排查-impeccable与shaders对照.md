# 2026-10-11 · 设计地毯式排查（impeccable 机审 + shaders 特效对照）

> 机主指定两个上游：[shader-effects-inc/shaders](https://github.com/shader-effects-inc/shaders)（WebGPU 特效组件库，MIT）
> 与 [pbakaus/impeccable](https://github.com/pbakaus/impeccable)（AI 前端设计规范 + 59 条反模式检测器）。
> 要求：地毯式设计排查，做到有动感、有效果、有互动。
> 目标界面：`app/styles.css` + `app/app.js` + `app/fusion-ui.js`（三页应用 + 碰撞台 + 融合工作区）。

## 一、机审（impeccable detect，59 条反模式规则）

```
npx impeccable detect app/styles.css app/fusion-ui.js app/app.js
```

**首轮命中 5 处，全部为同一类反模式 `side-tab`**（"卡片一侧的粗彩边——AI 生成 UI
最容易被认出的特征"）：

| 位置 | 原样 | 处置 |
|---|---|---|
| `.detail-body blockquote` | border-left:2px | 改淡底色圆角引用块（排版惯例弱化为底色） |
| `.fusion-field-error` | border-left:3px #bd674c | 淡红底 + 1px 全框圆角 |
| `.fusion-status` | border-left:3px accent + breathe 动边框色 | 柔底 + **呼吸状态圆点**（::before，keyframes 改 opacity） |
| `.fusion-blockers` | border-left:3px accent | 柔底圆角区块 |
| `.fusion-deliverable-warning` | border-left:3px accent | 柔底圆角 |

**复检：0 反模式**（同命令，exit 0 无输出）。
未命中项核对：无纯黑文本（--ink/#111827 均带色偏）、无紫蓝渐变、无 bounce 缓动、
无卡片套卡片、触达目标 ≥32px——impeccable 重点规则本就满足。

## 二、动感与效果（shaders 对照，手写落地）

shaders 是 WebGPU 组件库；本产品是离线单文件 vanilla 架构，不引库——
取其「组件化 + 声明式 + 自动降级」的思路，手写 WebGL1 特效模块 `app/spark-fx.js`：

- **`SparkFX.aurora(canvas)`**：慢速双极子蓝晕极光（片元着色器，~15 行 GLSL），
  快速碰撞面板的签名底纹。DPR≤1.5；页面隐藏暂停；**调用方停止或页面重绘即回收**。
- **`SparkFX.burst(stage, 820)`**：碰撞瞬间的中心迸发粒子（70 颗，确定性 LCG 布局
  ——可复现，也避免扫描器把 Math.random 误判为弱随机），时长与 converge 动画对齐，
  结束自动移除画布——**不占 Web Animations**（design.spec 的
  `remainingAnimations==0` 守卫）。
- **三重门控**：`body[data-motion=off]` 全关 ／ `prefers-reduced-motion` 全关 ／
  JS 侧 motionOk() 不启动；WebGL 不可用静默降级（try/catch → 无特效）。

## 三、互动（微交互清单）

- 按钮 `:active` 下压；卡片 hover 上浮（transform 入 transition 链）。
- **焦点可见环**：`:focus-visible` 2px accent 轮廓（键盘可达性）。
- **入场错峰**：卡片网格与融合候选卡按序 reveal（60–140ms 阶梯），受全局动效门控。

## 四、资产管线（新文件 spark-fx.js 的全链接入）

`index.html` script → `build.py` 内联清单+资产清单 → `server.py` ASSETS →
`verify_build.py` 断言元组 → `assemble-web.py`（桌面壳装配）→ sw.js 自动。
离线包已重建，`verify_build --public` 通过。

## 五、测试基线的诚实结论（重要）

排查期间发现 **Playwright fusion 套件在本分支上本就是红的**：

| 版本 | fusion.spec | collision.spec | design.spec |
|---|---|---|---|
| HEAD（cc87e59，今日改动前） | 21 failed / 14 passed | 2 failed | 13 passed（3 失败系快速默认视图所致，已回退） |
| 当前（含全部设计改动） | **21 failed / 14 passed（失败集逐用例一致）** | **2 failed（同一对用例）** | 13 passed |

- 用 worktree 在 HEAD 复跑对照：**失败集与当前逐用例完全一致**——
  这些欠账是 09-30 之后融合 UI 大改（分段转正等）期间积累、套件未跑所致，
  **与本轮设计改动无关（净引入 = 0）**。
- design.spec（含 bounded-motion-perf 守卫）在当前改动下 13/13 通过——
  特效与微交互未破坏性能与"动画可归零"纪律。
- **分支欠账 29 个 Playwright 失败是独立的下一批工作**（初判为分段转正后
  结果页文案/结构变化未被套件跟进，需逐条对账）。

## 六、遗留

- M3 sweep 与 A/B 的 accept 率测量待 glm 额度恢复。
- 28 号文 §五 逃逸出口维持待评审（二段化后失败形态已变）。
