---
name: outline-writer
description: 将 evidence.json 中已核实的事实组织成带来源标注的写作提纲，输出 outline.md。
---

# 证据驱动的提纲

这是一份随 App 提供的原创示例 Skill。与 evidence-collector 有同名产物衔接，但仍需检查真实 JSON 字段及核实状态。

## Inputs
- evidence.json

## Outputs
- outline.md

## Constraints
- 只将 verification 为已核实的记录写成确定性事实。
- 每一节的关键论点都保留 source 和 location。
- 信息不足时保留待补充问题，不捏造证据。

## Steps
- 检查 evidence.json 是否是含 claim、source、location、verification 的记录数组。
- 分离已核实记录和待核实记录。
- 将已核实记录按主题分组，设计提纲层级。
- 在每个论点后标注原始来源及位置。
- 将提纲和待补充问题保存到 outline.md。

## Dependencies
- 可解析 JSON 并输出 Markdown 的人工操作者或 Agent 宿主。

## 验收
逐项核对提纲中的事实是否能回到 evidence.json 的已核实记录；待核实信息只能出现在问题清单中。
