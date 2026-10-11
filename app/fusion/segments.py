"""语义分段：把一次 2.5 万字符的长输出拆成自洽块，逐块本地校验、只重试失败的块。

    from app.fusion.segments import blocks, block_wire, segment_issues
    for seg in blocks(packet): ...

## 它修的是哪一个实测失败面（2026-10-06/07 两轮）

验收是 **65 项条件的合取**：每项 98% 可靠时整体上限 26.9%，实测 0/16
（`evaluations/requirement-arithmetic.txt`；`deep-cb-instruction-level.txt`
逐项实测 64/65 → 每项 0.985，坐实了估值）。已证伪的五次改造全部在动
「每项可靠性」，没有一次动过**合取结构**。

分段拼装本身也已经被真实调用试过一次（`evaluations/seg-assembly.txt`、
`seg-retry.txt`，n=3）：accepted 仍是 0/3，但失败**收敛到两条很具体的形状**——

    ① 段产物缺必填字段（candidates[2] 缺 problem）
    ② 段产物数组长度不合法（profiles[1].retention.keep）

两条的根因是同一个：**当时给模型的段级指令只有一句「只输出某顶层键」，
从没告诉它这一段确切要写哪些字段、每个数组几条**；而重试的判据只看
「JSON 解析成功 + 带对了 orientation」，缺 `problem` 的段被当成合格段放行，
直到所有调用花完才在最终契约上炸掉。

本模块补的就是这两块：

1. **段级指令从 schema 与骨架自动生成**（不手写、随契约漂移自动更新）：
   必填字段清单、本地钉死的取值、逐个数组边界、可引用文件清单。
2. **段级确定性校验**：每段一返回就判，产出**全部**不合格项；
   重试只针对失败的段，且把校验器的具体指控写进重试提示词。

## 「回灌」的边界（为什么重试提示词里可以写指控）

28 号文 §四 反对的是**没有外部反馈**的自我纠正（Huang et al.,
arXiv:2310.01798：模型自查自己的输出，性能反而退化）。这里重试时
**不回显模型上一次的输出**，只附**本地确定性校验器**的指控清单——
这属于外部反馈，正是 Let's Verify Step by Step（arXiv:2305.20050）
里「过程监督优于结果监督」的形态。

## 校验边界：段级只判「段内自洽」，跨段判据一道不少

段级校验**只做**该段自己能判的事（形状、枚举、数组边界、evidence 能否
归位、认领自洽、标题回声）。所有跨段判据（profiles 恰好 A/B 各一、
candidates 恰好 A/B/C 各一、候选引用的 direction 必须被采纳……）
**仍然只由最终的 `validate_result` 判**——装配后的对象与整份模式
走的是同一条验收链，**一道都没有放松，也没有跳过**。

纯函数、确定性、零模型调用；`block_wire` 之外的一切都不碰网络。
"""
from __future__ import annotations

import json
from copy import deepcopy

from . import skeleton as skeleton_module
from .contract import RESULT_SCHEMA, wire_item_schema
from .context import effective_gaps, selected_paths
from .packages import FusionError, canonical

#: 顶层键 → 该键下「一次一段」的对象模板的钉死取值字段。
_PINNED_FIELD = {'profiles': 'parent', 'candidates': 'orientation'}

#: 五基线集合（与 skeleton.BASELINES / contract 同一约定，此处只用于段级校验）。
BASELINES = {'A_only', 'B_only', 'concatenation', 'fusion', 'model_only'}

#: 生成顺序刻意是 profiles → reviews → candidates：
#: 候选要引用被采纳的方向，方向审查就应当先于候选定稿。
SECTION_ORDER = ('profiles', 'direction_reviews', 'candidates')


def blocks(packet: dict) -> list:
    """按生成顺序列出全部分段。

    profiles 与 candidates **一次一段**（一段就是一次 LLM 调用的产物单元）；
    direction_reviews 是一个小而原子的块（与 suggestions 一一对应），
    整块一次调用，且**只在建议非空时存在**——没有建议就不发这个请求。

    每段：`{'key', 'tag', 'index', 'count', 'template'}`——`index` 是装配时的
    目标下标；reviews 块 `count` 是整块条数，其余恒为 1。
    """
    pre = skeleton_module.skeleton(packet)
    out = []
    for key in SECTION_ORDER:
        templates = pre.get(key) or []
        if not templates:
            continue
        if key == 'direction_reviews':
            out.append({'key': key, 'tag': 'direction_reviews:all', 'index': 0,
                        'count': len(templates), 'template': templates})
            continue
        for index, template in enumerate(templates):
            out.append({'key': key,
                        'tag': '%s:%s' % (key, template.get(_PINNED_FIELD[key], index)),
                        'index': index, 'count': 1, 'template': template})
    return out


def _bound_text(schema: dict, where: str) -> str:
    lo, hi = schema.get('minItems'), schema.get('maxItems')
    if lo == hi:
        return '%s 恰好 %d 条，多一条少一条都直接拒收' % (where, lo)
    if lo >= 1:
        return '%s 至少 %d 条、至多 %s 条，写 0 条直接拒收' % (
            where, lo, hi if hi is not None else '不限')
    return '%s 可以是 0 条，但至多 %s 条' % (where, hi if hi is not None else '不限')


def _exact_tail(schema: dict, template) -> str:
    """恰好 N 条时，把模板里钉死的取值/枚举一并写明。**全部生成，不手写。**"""
    if not isinstance(template, list) or not template:
        return ''
    pinned = [item.get('removed_parent') for item in template
              if isinstance(item, dict) and item.get('removed_parent')]
    if pinned:
        return '（removed_parent 依次是 %s）' % '、'.join(str(p) for p in pinned)
    enum = (schema.get('items') or {}).get('enum')
    if enum and all(isinstance(t, str) for t in template):
        return '（恰好这些值，逐字照抄：%s）' % '、'.join(template)
    return ''


def item_schema(key: str) -> dict:
    """**验收用**的完整单项 schema（sha256 必填）。

    段级校验在**段级重锚定之后**跑（与最终链 assemble → reanchor → validate
    同序）：那时本地已把 sha256 按 (parent, path) 填进每条引文，
    与 `validate_result` 看到的形状一致——用 wire schema（无 sha256）判
    会把本地自己填的字段报成「多出字段」。
    """
    section = RESULT_SCHEMA.get('properties', {}).get(key)
    if not isinstance(section, dict) or 'items' not in section:
        raise FusionError(f'No per-item schema for section {key!r}')
    from copy import deepcopy as _dc
    return _dc(section['items'])


def _array_notes(schema: dict, where: str, template) -> list:
    notes = []
    kind = schema.get('type')
    if kind == 'object':
        for name, child in (schema.get('properties') or {}).items():
            child_template = (template or {}).get(name)
            notes += _array_notes(child, '%s.%s' % (where, name), child_template)
    elif kind == 'array':
        line = _bound_text(schema, where)
        if schema.get('minItems') is not None \
                and schema['minItems'] == schema.get('maxItems'):
            line += _exact_tail(schema, template)
        notes.append(line)
        child = template[0] if isinstance(template, list) and template else None
        if isinstance(schema.get('items'), dict):
            notes += _array_notes(schema['items'], where + '[]', child)
    return notes


def _pinned_text(template: dict, key: str) -> list:
    """本地钉死、模型必须照抄的取值。空值不钉（骨架里空串 = 待填）。"""
    fields = ('parent',) if key == 'profiles' else ('orientation', 'direction_id')
    pins = []
    for field in fields:
        want = template.get(field)
        if want:
            pins.append('%s 必须逐字写 "%s"' % (field, want))
    return pins


def _direction_pin(packet: dict, template: dict) -> list:
    """candidates 的 direction_id 取值规则——与 validate_result 的锚定判据同源。

    validate_result：A/B 候选的 direction_id 必须是本地落点 / 被采纳的同朝向建议 /
    "independent" 三者之一。当本地落点为空且该朝向没有建议时，**只剩 "independent"**
    可写——这时必须显式钉死，否则模型拿着骨架里的空串自由发挥，必被最终契约拒绝
    （2026-10-07 fuse-iter2 实测：无落点包对上 candidates:A 连挂 3 次）。
    """
    orientation = template.get('orientation')
    if orientation == 'C':
        return ['direction_id 必须逐字写 "independent"（第三类按定义与 A、B 无关）']
    landings = skeleton_module.collision_ids(packet)
    suggested = {d.get('id') for d in packet.get('rule_directions', {}).get('suggestions') or []
                 if d.get('orientation') == orientation and d.get('id')}
    if landings:
        return ['direction_id 必须逐字写 "%s"（本地给出的碰撞落点）' % template.get('direction_id')
                if template.get('direction_id') else
                'direction_id 必须取本地落点 collision_id 之一：%s'
                % '、'.join(landings[:6])]
    if not suggested:
        return ['direction_id 必须逐字写 "independent"（本次没有本地落点、也没有同朝向建议，'
                '自由探索的依据写进 rationale）']
    return ['direction_id 必须取以下建议 id 之一（并在 direction_reviews 里给 adopt/adapt）：%s；'
            '或写 "independent" 并在 rationale 说明依据' % '、'.join(sorted(suggested))]


def directive(packet: dict, seg: dict, context_notes=None) -> str:
    """一段的完整指令。**全部内容由 schema、骨架与 packet 生成**——
    契约改了这里自动跟着改；手写第二份必然漂移（`count_brief_lines` 同款纪律）。
    """
    key = seg['key']
    schema = wire_item_schema(key)
    template = seg['template']
    lines = ['【分段模式 · %s】' % seg['tag'],
             '本次只写一个顶层键 "%s"；其余顶层键一律不要出现。'
             '仍必须输出一个完整、合法的 JSON 对象。' % key]
    if key == 'direction_reviews':
        lines.append('数组恰好 %d 条，与 rule_directions.suggestions 一一对应：'
                     '每条 direction_id 逐字照抄建议里的 id，一条不多、一条不少；'
                     'decision 只能是 adopt / adapt / reject，每条都要给具体 reason。'
                     % len(template))
    else:
        required = schema.get('required') or []
        lines.append('输出封套形如 {"%s": [ { … } ]}——只有这一个顶层键，值为数组。'
                     '仍必须输出一个完整、合法的 JSON 对象。' % key)
        lines.append('数组恰好 1 个对象。必填字段（一个都不能少、也不许另造字段——'
                     'value_evidence 之类的自造字段一律禁止）：\n  %s' % ', '.join(required))
        pins = _pinned_text(template, key) if key == 'profiles' \
            else ['orientation 必须逐字写 "%s"' % template.get('orientation')] + _direction_pin(packet, template)
        if pins:
            lines.append('本地已钉死的取值（照抄；写别的会被本地覆盖并记为分歧）：\n  · %s'
                         % '\n  · '.join(pins))
        notes = _array_notes(schema, key, template)
        if notes:
            lines.append('数组边界（逐条数清楚）：\n  · %s' % '\n  · '.join(notes))
        citable = skeleton_module.citable_paths(packet)
        shown = '；'.join('%s: %s' % (p, ', '.join(paths) or '（无可引用文件）')
                          for p, paths in citable.items())
        lines.append('evidence 一律不写 sha256（本地按 (parent, path) 填真实哈希）；'
                     'path 必须逐字来自该母包的可引用清单：\n  %s' % shown)
        if key == 'profiles':
            gaps = effective_gaps(packet).get(template.get('parent')) or []
            if gaps:
                lines.append('missing 必须逐字包含以下已知缺口（一条都不能少，照抄原文）：\n  · %s'
                             % '\n  · '.join(gaps))
        if key == 'candidates':
            lines.append('title 必须是新的，不得复述母包条目或碰撞落点描述的原文。')
            lines.append('artifacts[].path 必须以真实扩展名结尾（如 tool.py、checklist.md、'
                         'schema.json）——写到点就停（"report."）会被直接拒收。')
            lines.append('disposition 与内容的锁（双向都判）：列了 blockers 就必须把 '
                         'disposition 设为 blocked 或 not_recommended，自称 hypothesis '
                         '却列 blockers 会被拒；outcome_class 是 needs_ai_fill 才允许列 '
                         'gaps_to_fill，其他四类必须留空。')
        lines.append('每条 evidence 必含 parent / path / start_line / end_line / quote 五个字段'
                     '（sha256 不要写）；输出宁精勿长：本项目实测超过约 7000 字符后'
                     ' JSON 断裂率急剧上升——profiles 的 capabilities 写 3～6 条最有代表性的'
                     '即可（不必写满 16 条上限），candidates 的 workflow/artifacts 同样取最有'
                     '代表性的几条。')
    if context_notes:
        lines.append('【已定稿的段（内容以它们为准，不要另起一套）】\n  · %s'
                     % '\n  · '.join(context_notes))
    return '\n'.join(lines)


def retry_directive(issues: list) -> str:
    """重试追加段：**只带确定性校验器的指控**，不回显模型上一次的输出。

    外部确定性反馈是文献支持的形态（arXiv:2305.20050 过程监督）；
    「把烂输出原样喂回去让模型自查」才是被 Huang et al. 否掉的那种。
    """
    shown = '\n'.join('  %d. %s' % (i + 1, issue) for i, issue in enumerate(issues[:8]))
    return ('【上一段未通过本地确定性校验；以下各项必须逐条满足后重新生成本段】\n'
            + shown
            + '\n除修复以上各项外，本段其余要求全部不变。')


def block_wire(base_wire: list, packet: dict, seg: dict, context_notes=None,
               retry_issues=None, directive_override=None) -> list:
    """在整份请求的最后追加本段指令，返回**新**的 wire（不改 base_wire）。

    `directive_override`：二段化等场景传入整段指令文本（自带上下文摘要）；
    传入后不再叠加 `context_notes` 与默认 directive，避免重复。
    """
    wire = [dict(message) for message in base_wire]
    if directive_override is not None:
        text = directive_override
    else:
        text = directive(packet, seg, context_notes)
    if retry_issues:
        text += '\n\n' + retry_directive(retry_issues)
    wire[-1]['content'] = wire[-1]['content'] + '\n\n' + text
    return wire


# ── 段级确定性校验 ──────────────────────────────────────────────────

def shape_issues(value, schema, where='segment'):
    """`check_shape` 的**累积**版：报出全部不合格项而不是在第一处就抛。

    两者语义必须一致——`test_segments.py` 有一条漂移守卫：
    一批变异样本上，「这里报了」与「check_shape 抛了」必须逐一吻合。
    """
    issues = []
    kind = schema.get('type')
    matches = {'object': type(value) is dict, 'array': type(value) is list,
               'string': type(value) is str, 'integer': type(value) is int}
    if not matches.get(kind, False):
        return ['%s：类型应为 %s' % (where, kind)]
    if 'enum' in schema and value not in schema['enum']:
        issues.append('%s：必须是 %s 之一，实际 %r' % (where, '/'.join(schema['enum']), value))
    if kind == 'object':
        missing = sorted(set(schema['required']) - set(value))
        unknown = sorted(set(value) - set(schema['required']))
        if missing:
            issues.append('%s：缺少必填字段 %s' % (where, missing))
        if unknown:
            issues.append('%s：多出未约定的字段 %s' % (where, unknown))
        for name, child in (schema.get('properties') or {}).items():
            if name in value:
                issues += shape_issues(value[name], child, '%s.%s' % (where, name))
    elif kind == 'array':
        lo, hi = schema.get('minItems'), schema.get('maxItems')
        if not lo <= len(value) <= hi:
            issues.append('%s：数组长度 %d 不在 %d～%d' % (where, len(value), lo, hi))
        for index, child in enumerate(value):
            issues += shape_issues(child, schema['items'], '%s[%d]' % (where, index))
    elif kind == 'string':
        if not value.strip():
            issues.append('%s：空文本' % where)
        elif not schema.get('minLength', 1) <= len(value) <= schema.get('maxLength', 6000):
            issues.append('%s：文本长度 %d 越界' % (where, len(value)))
    elif value < schema.get('minimum', 0):
        issues.append('%s：整数小于下限' % where)
    return issues


def _evidence_issues(refs, expected_parent, files, where):
    """段级能判的证据三件事：parent 归属、path 在可引用清单内、引文逐字在行号区间。
    sha256 此时还没填（那是装配后 `fill_evidence_hashes` 的事），不在此判。
    """
    issues = []
    if not isinstance(refs, list):
        return issues          # 类型错由 shape_issues 报；这里对字符串枚举会炸
    for i, ref in enumerate(refs):
        if not isinstance(ref, dict):
            issues.append('%s.evidence[%d]：evidence 条目必须是对象' % (where, i))
            continue
        spot = '%s.evidence[%d]' % (where, i)
        if ref.get('parent') != expected_parent:
            issues.append('%s：parent 必须是 %s' % (spot, expected_parent))
        entry = files.get((ref.get('parent'), ref.get('path')))
        if entry is None:
            issues.append('%s：path %r 不在该母包的可引用清单里' % (spot, ref.get('path')))
            continue
        lines = entry['text'].splitlines()
        start, end = ref.get('start_line'), ref.get('end_line')
        if not (type(start) is int and type(end) is int
                and 1 <= start <= end <= len(lines)):
            issues.append('%s：行号区间 %r 越界（该文件共 %d 行）' % (spot, (start, end), len(lines)))
            continue
        if ref.get('quote') not in '\n'.join(lines[start - 1:end]):
            issues.append('%s：quote 不是该行号区间内的逐字原文（重锚定也修不动）——'
                          '必须逐字复制 %s 的真实行文：不要转写、缩写、省略或加代码围栏；'
                          '若行号给错，改成引文真实所在的行号' % (spot, ref.get('path')))
    return issues


def _materials(packet):
    """标题回声判据用的母包材料（与 contract._check_title_echo 同一来源）。"""
    materials = []
    for cap in (packet['capability'].get('A'), packet['capability'].get('B')):
        materials.extend(str(entry.get('text', ''))
                         for entries in (cap.get('items') or {}).values()
                         for entry in entries or [])
    materials.extend(str(cand.get('gap', ''))
                     for cand in (packet.get('collision') or {}).get('candidates') or [])
    return materials


def _candidate_issues(item, packet, where):
    """候选的段内自洽判据——与 validate_result 的同名判据一条对一条，
    只去掉跨段的（方向引用要靠 reviews 块，留给最终契约判）。"""
    from .collide import echo_ratio
    from .contract import proposed_path

    issues = []
    # direction_id 的取值域与 validate_result 的锚定判据同源：
    # 本地落点 ∪ 同朝向建议 ∪ 'independent'。取值域外的 id 必被最终契约拒绝。
    orientation = item.get('orientation')
    landings = skeleton_module.collision_ids(packet)
    suggested = {d.get('id') for d in packet.get('rule_directions', {}).get('suggestions') or []
                 if d.get('orientation') == orientation and d.get('id')}
    valid_ids = set(landings) | suggested | {'independent'}
    did = item.get('direction_id')
    if did not in valid_ids:
        issues.append('%s：direction_id %r 不在合法取值域内'
                      '（本地落点 %s / 同朝向建议 %s / "independent"）'
                      % (where, did, sorted(landings) or '无', sorted(suggested) or '无'))
    blockers = item.get('blockers') or []
    disposition = item.get('disposition')
    gaps = item.get('gaps_to_fill') or []
    claimed_fill = item.get('expected_outcome', {}).get('outcome_class') == 'needs_ai_fill'
    if blockers and disposition == 'hypothesis':
        issues.append('%s：列了 blockers 却自称 hypothesis——二选一：'
                      '① 方案确实受阻 → disposition 改 "blocked"、outcome_class 改 '
                      '"needs_ai_fill"、并逐条列出 gaps_to_fill；'
                      '② 方案可立即构建 → 把 blockers 清成空数组' % where)
    if disposition == 'blocked' and not blockers:
        issues.append('%s：自称 blocked 却一条 blocker 都没列' % where)
    if claimed_fill != bool(gaps):
        issues.append('%s：needs_ai_fill 必须列 gaps_to_fill，且只有它才允许列' % where)
    if disposition == 'blocked' and not claimed_fill:
        issues.append('%s：blocked 必须同时认领 needs_ai_fill' % where)
    if disposition != 'not_recommended' and (not item.get('workflow') or not item.get('artifacts')):
        issues.append('%s：非 not_recommended 的候选必须给出 workflow 与 artifacts' % where)
    seen = set()
    artifacts = item.get('artifacts') if isinstance(item.get('artifacts'), list) else []
    for i, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            issues.append('%s.artifacts[%d]：artifacts 条目必须是对象' % (where, i))
            continue
        try:
            path = proposed_path(artifact.get('path', '')).casefold()
        except FusionError as exc:
            issues.append('%s.artifacts[%d]：%s' % (where, i, exc))
            continue
        if path in seen:
            issues.append('%s.artifacts[%d]：产出路径重复' % (where, i))
        seen.add(path)
    ablation = item.get('ablation_checks') if isinstance(item.get('ablation_checks'), list) else []
    removed = {a.get('removed_parent') for a in ablation if isinstance(a, dict)}
    if removed != {'A', 'B'}:
        issues.append('%s：ablation_checks 必须分别去掉 A 与 B' % where)
    evaluation = item.get('evaluation') if isinstance(item.get('evaluation'), list) else []
    for i, test in enumerate(evaluation):
        if not isinstance(test, dict):
            issues.append('%s.evaluation[%d]：evaluation 条目必须是对象' % (where, i))
            continue
        if set(test.get('baselines') or []) != BASELINES:
            issues.append('%s.evaluation[%d]：baselines 必须恰好是五个基线' % (where, i))
    title = item.get('title', '')
    for material in _materials(packet):
        hit = echo_ratio(title, material)
        if hit['echo']:
            issues.append('%s：title 是所给材料的回声（%s）——碰撞必须起新标题' % (where, hit['why']))
            break
    return issues


def segment_issues(item, packet: dict, seg: dict) -> list:
    """一个段的**全部**段内不合格项；空列表 = 本段可定稿。

    只判段内自洽；跨段判据由装配后的 `validate_result` 全量执行。
    """
    key = seg['key']
    where = '%s[%s]' % (key, seg['tag'])
    if key == 'direction_reviews':
        issues = []
        if not isinstance(item, list):
            return ['%s：direction_reviews 段必须是数组' % where]
        if len(item) != seg['count']:
            issues.append('%s：必须恰好 %d 条（与 suggestions 一一对应），实际 %d 条'
                          % (where, seg['count'], len(item)))
        for i, entry in enumerate(item):
            if not isinstance(entry, dict):
                issues.append('%s[%d]：条目必须是对象' % (where, i))
                continue
            issues += shape_issues(entry, item_schema(key), '%s[%d]' % (where, i))
        suggested = {d['id'] for d in packet.get('rule_directions', {}).get('suggestions') or []}
        got = [r.get('direction_id') for r in item if isinstance(r, dict)]
        if len(got) != len(item) or set(got) != suggested:
            issues.append('%s：direction_id 集合必须与 suggestions 一一对应'
                          '（缺 %s，多 %s）'
                          % (where, sorted(suggested - set(got)) or '无',
                             sorted(set(got) - suggested) or '无'))
        return issues

    issues = shape_issues(item, item_schema(key), where)
    files = {(label, f['path']): f
             for label, package in (packet.get('packages') or {}).items()
             for f in package.get('files') or []
             if f.get('status') == 'text' and f.get('path') in selected_paths(packet, label)}
    if key == 'profiles':
        parent = item.get('parent')
        capabilities = item.get('capabilities') if isinstance(item.get('capabilities'), list) else []
        for i, cap in enumerate(capabilities):
            if not isinstance(cap, dict):
                issues.append('%s.capabilities[%d]：capabilities 条目必须是对象' % (where, i))
                continue
            issues += _evidence_issues(cap.get('evidence') or [], parent, files,
                                       '%s.capabilities[%d]' % (where, i))
        missing = item.get('missing') or []
        for gap in effective_gaps(packet).get(parent) or []:
            if gap not in missing:
                issues.append('%s：用户审出的已知缺口 %r 必须逐字进 missing' % (where, gap))
        if missing and item.get('coverage') != 'partial':
            issues.append('%s：missing 非空时 coverage 必须是 partial' % where)
    else:
        issues += _evidence_issues((item.get('contribution_a') or {}).get('evidence') or [],
                                   'A', files, '%s.contribution_a' % where)
        issues += _evidence_issues((item.get('contribution_b') or {}).get('evidence') or [],
                                   'B', files, '%s.contribution_b' % where)
        issues += _candidate_issues(item, packet, where)
    return issues


def add_finalized(finalized: dict, seg: dict, item):
    """把定稿段放进结果容器。**reviews 块（count>1）必须 extend 而不是 append**——
    它的 `item` 本身就是整张列表；套一层列表会让 assemble 把 2 条 review 数成 1 条
    （2026-10-07 glm-4.5-air 实测：`Model returned 1 direction_reviews but the
    contract requires 2`）。M3 时代的运行全部死在更早的块，这个雷一直没炸。
    """
    if seg['count'] > 1 and isinstance(item, list):
        finalized[seg['key']].extend(item)
    else:
        finalized[seg['key']].append(item)


def summarize(seg: dict, item) -> str:
    """已定稿段的一行摘要，随上下文传给后面的块（28 号文 §3.2）。"""
    if seg['key'] == 'profiles':
        keep = len((item.get('retention') or {}).get('keep') or [])
        discard = len((item.get('retention') or {}).get('discard') or [])
        return ('profiles[%s] 已定稿：coverage=%s，keep %d 条 / discard %d 条'
                % (item.get('parent'), item.get('coverage'), keep, discard))
    if seg['key'] == 'candidates':
        outcome = (item.get('expected_outcome') or {}).get('outcome_class')
        return 'candidates[%s] 已定稿：title=%r，disposition=%s，outcome_class=%s' % (
            item.get('orientation'), str(item.get('title'))[:60],
            item.get('disposition'), outcome)
    decisions = {}
    for review in (item if isinstance(item, list) else []):
        decisions[review.get('decision')] = decisions.get(review.get('decision'), 0) + 1
    return 'direction_reviews 已定稿：%s' % json.dumps(decisions, ensure_ascii=False)


def take_segment(obj, seg: dict):
    """从一段响应里取出本段要的对象；拿不到返回 None。

    单条段先按钉死字段匹配（orientation / parent），匹配不到取第 `index` 条；
    reviews 块整表返回。装配仍按骨架位置归位并留痕，这里只负责「找出来」。
    """
    items = (obj or {}).get(seg['key'])
    if not isinstance(items, list) or not items:
        return None
    if seg['count'] > 1:
        return items
    field = _PINNED_FIELD[seg['key']]
    want = (seg['template'] or {}).get(field)
    if want:
        for entry in items:
            if isinstance(entry, dict) and entry.get(field) == want:
                return entry
    if seg['index'] < len(items):
        return items[seg['index']]
    return None


# ── 候选块二段化（2026-10-11，开关默认关）──────────────────────────
#
# 依据：分段 A/B 与 sweep 的残余失败全部集中在 candidates 块，且两类失败
# 性质不同——叙述字段（自造字段、锁没守住）是**创作**问题；evidence
# （缺字段、quote 编造）是**逐字保真**问题。一锅烩时互相干扰，分开生成
# 各自变简（P2「把生成问题变检索问题」的候选块版）。
#
# 第一段：叙述（evidence 留空数组——wire 放宽仅此一处，验收链不变）；
# 第二段：拿着定稿叙述生成两条证据数组（每条 1～8 条、可归位）。
# 合并后仍走同一条 `segment_issues` 全量校验与最终 `validate_result`。

def two_phase_wire_schema(key: str) -> dict:
    """二段化第一段的 wire schema：contribution 的 evidence 允许空数组。
    仅 wire 放宽；验收用 `item_schema` 一字不变。"""
    import re as _re
    schema = wire_item_schema(key)
    for side in ('contribution_a', 'contribution_b'):
        node = (schema.get('properties') or {}).get(side)
        if node and 'properties' in node and 'evidence' in node['properties']:
            node['properties']['evidence']['minItems'] = 0
    return schema


def narrative_directive(packet: dict, seg: dict, context_notes=None) -> str:
    """二段化第一段指令：只写叙述，evidence 一律空数组。"""
    key = seg['key']
    template = seg['template']
    schema = two_phase_wire_schema(key)
    orientation = template.get('orientation')
    lines = ['【分段模式 · %s · 第一段：叙述】' % seg['tag'],
             '本次只写一个顶层键 "%s"，数组恰好 1 个对象；输出完整、合法的 JSON。' % key,
             '必填字段（一个都不能少、不许另造字段）：\n  %s'
             % ', '.join(schema.get('required') or []),
             'contribution_a 与 contribution_b 的 mechanism/kind 必须写实，'
             '但它们的 **evidence 一律写成空数组 []**——证据由下一段专门生成，'
             '本段写证据会被整段拒收。']
    pins = ['orientation 必须逐字写 "%s"' % orientation] + _direction_pin(packet, template)
    lines.append('本地已钉死的取值（照抄）：\n  · %s' % '\n  · '.join(pins))
    lines.append('title 必须是新的，不得复述母包条目或碰撞落点描述的原文。')
    lines.append('disposition 与内容的锁（双向都判）：列了 blockers 就必须把 '
                 'disposition 设为 blocked 或 not_recommended；outcome_class 是 '
                 'needs_ai_fill 才允许列 gaps_to_fill，其他四类必须留空。')
    if context_notes:
        lines.append('【已定稿的段（内容以它们为准，不要另起一套）】\n  · %s'
                     % '\n  · '.join(context_notes))
    return '\n'.join(lines)


def evidence_directive(packet: dict, seg: dict, item: dict) -> str:
    """二段化第二段指令：给定定稿叙述，只产出两条证据数组。"""
    sides = []
    for side, parent in (('evidence_a', 'A'), ('evidence_b', 'B')):
        mech = (item.get('contribution_%s' % side[-1]) or {}).get('mechanism', '')
        sides.append('%s：为 mechanism「%s」配 1～3 条原文证据（parent=%s）'
                     % (side, str(mech)[:120], parent))
    citable = skeleton_module.citable_paths(packet)
    shown = '；'.join('%s: %s' % (p, ', '.join(paths) or '（无可引用文件）')
                      for p, paths in citable.items())
    lines = ['【分段模式 · %s · 第二段：证据】' % seg['tag'],
             '只输出一个 JSON 对象：{"evidence_a": [ … ], "evidence_b": [ … ]}，'
             '不要其他键。每条证据必须含 parent / path / start_line / end_line / quote '
             '五个字段（不写 sha256）；parent 只能写所属侧（a→"A"，b→"B"）。',
             'quote 必须是该行号区间内的**逐字原文**——不许转写、缩写、加代码围栏；'
             '拿不准行号就先给出行号正确的短引文（哪怕一行）。',
             '可引用清单：%s' % shown,
             *sides]
    return '\n'.join(lines)


def take_evidence(obj: dict) -> tuple:
    """从第二段响应取出 (evidence_a, evidence_b)；拿不到返回 (None, None, issue)。"""
    if not isinstance(obj, dict):
        return None, None, '响应不是 JSON 对象'
    ea, eb = obj.get('evidence_a'), obj.get('evidence_b')
    if not isinstance(ea, list) or not ea:
        return None, None, 'evidence_a 缺失或为空'
    if not isinstance(eb, list) or not eb:
        return None, None, 'evidence_b 缺失或为空'
    return ea, eb, None


def merge_evidence(item: dict, ea: list, eb: list) -> dict:
    """把第二段证据并入第一段叙述（就地）。返回留痕 {'merged': 2}。"""
    item.setdefault('contribution_a', {})['evidence'] = ea
    item.setdefault('contribution_b', {})['evidence'] = eb
    return {'merged': 2}


def joined_raw(finalized: dict) -> str:
    """把各段拼回一份 JSON 文本——只用于完整性观测（classify），不用于验收。"""
    return canonical({key: deepcopy(value) for key, value in finalized.items()})
