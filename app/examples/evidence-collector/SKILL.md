---
name: evidence-collector
description: 从用户提供的本地资料中整理可追溯的事实，生成 evidence.json，供后续写作或规划使用。
---

# 本地证据整理

这是一份随 App 提供的原创示例 Skill。导入碰撞台只分析文本，不会自动执行以下步骤。

## Inputs
- brief.md

## Outputs
- evidence.json

## Constraints
- 只使用用户明确提供的本地资料，不访问网络。
- 每条事实必须保留来源文件名和段落或行号。
- 无法确认的内容标记为待核实，不补写为事实。

## Steps
- 阅读 brief.md，确认任务问题、资料范围和交付用途。
- 从指定本地资料中摘取与问题相关的事实。
- 逐条记录 claim、source、location 和 verification 字段。
- 将记录保存到 evidence.json，并标记尚未核实的条目。
- 人工复核来源位置是否能支持对应事实。

## Dependencies
- 用户提供的本地 Markdown 或文本资料。
- 能够按上述流程读写文件的人工操作者或 Agent 宿主。

## 验收
输出必须是可解析的 JSON 数组，每条记录包含上述四个字段；没有来源的结论不得标记为已核实。
