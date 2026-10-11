"""本地生成结果骨架：把**确定性的部分**从模型手里收回来。

    from app.fusion.skeleton import skeleton
    pre = skeleton(packet)          # 纯函数、零模型调用

## 它解决的是哪一个失败面

2026-10-03 实测（MiniMax M3，`thinking: disabled`，六次完整流程）暴露的失败里，
**形状类**占了一大片：

    Invalid array length at result.candidates
    多出 ['line_range_relocated']
    候选必须恰好三条、orientation 恰好 A/B/C
    ablation_checks 恰好两条
    baselines 恰好五个
    direction_reviews 必须与 rule_directions.suggestions 一一对应

而这些**全部是本地已知的**：`RESULT_SCHEMA` 是单一数据源，
`packet` 里数一数就知道 `direction_reviews` 该多长、有哪些 `collision_id`、
哪些文件名可以引用。**让模型去数，是把确定性活儿交给最不擅长数数的一方。**

## 同一条路上一堵墙：`json_schema` 靠不住（2026-10-03 实测）

「在生成期用解码器强制结构」本来是更省力的做法，**实测三家端点全部丢弃它**：
200 OK 之后整个 schema 消失，模型原话是「你没给我 schema」。
留档 `evaluations/json-schema-*.json`，守卫 `test_json_schema.py` 钉住。
**所以确定性只能走本地这条路。**

## 当前状态：**已接线**（2026-10-03 接线，本节原写「没接进 synthesize」已过时）

`engine.messages()` 把 `skeleton(packet)` 作为 `answer_template` 并进提示词，
`engine.synthesize()` 用 `assemble()` 把模型输出装回骨架后再走契约。
当时写下的测量要求仍有效：形状类失败（数组长度、多余字段、朝向缺失）的下降
要和**内容类**指标（引文可归位率）一起看，只有真实调用能回答。

## 2026-10-07 的延伸：语义分段（`segments.py`）

骨架的下一步合取结构改造：整份输出拆成自洽块（`segments.blocks`，其模板
就是这里的骨架条目），逐块生成、逐段校验、只重试失败的块——
`split_segments` 开关，默认关闭，A/B 留档见 `tools/ab-blocksplit.py`。
"""
from __future__ import annotations

from copy import deepcopy

from .packages import FusionError

#: `evaluation[].baselines` 契约强制恰好这五个，顺序由契约写死。
BASELINES = ('A_only', 'B_only', 'concatenation', 'fusion', 'model_only')

#: 候选的三个朝向，契约要求恰好 A、B、C 各一条。
ORIENTATIONS = ('A', 'B', 'C')

#: 消融检查恰好两条：分别去掉 A、去掉 B。
ABLATION_PARENTS = ('A', 'B')

#: 契约强制非空、但**具体条数由模型定**的数组：预填一条空槽。
#: 留空数组过去，模型的默认行为就是交空的——`retention.discard`
#: 历史上就是这么被拒的（`retention_arrays_non_empty`）。
NON_EMPTY_SLOTS = {
    'keep': 1,          # 1..8
    'discard': 1,       # 1..8
    'capabilities': 1,  # 1..16
}


def citable_paths(packet: dict) -> dict:
    """每个母包里**可以引用**的文件清单。

    `evidence_path_must_exist` 记录：编造路径是真实运行里**最高频**的拒绝原因
    （模型会把文档里的小标题当成文件名）。清单在这里先给全，
    模型就不必靠猜——**该由代码给的确定性，就不要靠模型记**。
    """
    out = {}
    for label, package in (packet.get('packages') or {}).items():
        out[label] = sorted(f['path'] for f in (package.get('files') or [])
                            if f.get('status') == 'text')
    return out


def _evidence_slot():
    # ⚠️ **这里刻意没有 `sha256`。**
    #
    # 2026-10-03 实测（`evaluations/copy-load.txt`）：`sha256` 段占模型输出的
    # **12.3%**（134 条 64 位十六进制），而 r02 那个失败样本**正是丢在这上面**——
    # 64 位被截到 24 位，值写到一半引号提前闭合，整个 JSON 崩掉。
    #
    # 那是**纯复制**，零推理价值，而**本地早就知道每个文件的哈希**。
    # 所以不问模型要，改为 `fill_evidence_hashes()` 按 `(parent, path)` 本地填。
    #
    # **这不是放宽校验**：契约里的 `no_extra_fields` 与 sha 一致性检查**一字未改**，
    # 只是值不再由模型提供。真正判定「模型是不是在编」的从来是
    # **`path` 在不在清单里** 与 **`quote` 是不是原文逐字**——那两条完全没动。
    # 而模型**本来就能编一个像模像样的哈希**，所以旧检查从来没有证明过它读过文件。
    return {'parent': 'A', 'path': '', 'start_line': 1, 'end_line': 1, 'quote': ''}


def fill_evidence_hashes(result: dict, files: dict) -> int:
    """按 `(parent, path)` 把真实 sha256 填进每条引文，返回填了几处。

    **`files` 是 `{(parent, path): file_entry}`**（`engine._text_files` 的形状）。

    填不上的（文件不存在）**照样把模型写的留着**——
    然后由契约去拒它。**这里不做判断，判断权在契约手里。**
    """
    from .evidence_reanchor import _walk_refs
    filled = 0
    for ref in _walk_refs(result):
        entry = files.get((ref.get('parent'), ref.get('path')))
        if entry is not None and entry.get('sha256'):
            ref['sha256'] = entry['sha256']
            filled += 1
    return filled


def _contribution_slot():
    return {'kind': '', 'mechanism': '', 'evidence': [_evidence_slot()]}


def _candidate_slot(orientation: str, direction_id: str):
    return {
        'orientation': orientation,
        'direction_id': direction_id,
        'disposition': '',
        'expected_outcome': {'outcome_class': '', 'basis': ''},
        'gaps_to_fill': [],
        'title': '', 'problem': '',
        'contribution_a': _contribution_slot(),
        'contribution_b': _contribution_slot(),
        'new_mechanism': '', 'workflow': [],
        'artifacts': [],
        'blockers': [], 'rationale': '',
        # 形状预填：契约要求恰好两条、分别去掉 A 与去掉 B。
        'ablation_checks': [{'removed_parent': parent, 'task': '', 'expected_loss': ''}
                            for parent in ABLATION_PARENTS],
        # 形状预填：契约要求恰好这五个、无重复。
        'evaluation': [{'task': '', 'baselines': list(BASELINES), 'success_criterion': ''}],
    }


def collision_ids(packet: dict) -> list:
    """本地算出的落点 id。**空列表是有意义的**——契约明说那时可以自由探索。"""
    return [c['collision_id'] for c in ((packet.get('collision') or {}).get('candidates') or [])]


def direction_ids(packet: dict) -> list:
    return [s.get('id') or s.get('direction_id')
            for s in ((packet.get('rule_directions') or {}).get('suggestions') or [])]


def skeleton(packet: dict) -> dict:
    """按本地已知的信息，造出 `RESULT_SCHEMA` 形状的**半成品**。

    **预填的全是确定性的**：字段名、数组长度、朝向、基线集合、可引用路径。
    **留空的都是要模型写的**：所有叙述性文本、分类判断、引文。
    空槽的形状与契约完全一致，所以模型填完的形状天然就对。

    纯函数：不调模型、不联网、不改 packet。
    """
    directions = direction_ids(packet)
    landings = collision_ids(packet)

    # A/B 朝向必须锚到某个 `collision_id`；C 是「与 A、B 无关」，按契约用 "independent"。
    # 一个都没有时**不能瞎编 id**——契约明说那时应当自由探索，所以留空让模型自己选。
    def default_direction(orientation: str) -> str:
        if orientation == 'C':
            return 'independent'
        return landings[0] if landings else ''

    profiles = []
    for parent in ('A', 'B'):
        profiles.append({
            'parent': parent,
            'capabilities': [{'claim': '', 'basis': '', 'evidence': [_evidence_slot()]}]
                             * NON_EMPTY_SLOTS['capabilities'],
            'retention': {'keep': [''] * NON_EMPTY_SLOTS['keep'],
                          'discard': [''] * NON_EMPTY_SLOTS['discard'],
                          'reason': ''},
            'dependencies': [], 'missing': [],
            'coverage': '',
        })

    return {
        'direction_reviews': [{'direction_id': d, 'decision': '', 'reason': ''}
                              for d in directions],
        'profiles': profiles,
        'candidates': [_candidate_slot(o, default_direction(o)) for o in ORIENTATIONS],
    }


# ── 装配：把模型填的内容装进本地骨架 ─────────────────────────────────

#: 这些字段**由本地说了算**，模型的写法一律作废。
#: 它们的共同点是：值在发请求之前就已经确定了，模型没有决定权。
#: 契约本来也会强制它们（`candidates_exactly_three` 等）——预置只是把
#: 「事后拒绝」提前成「事前告知」，并让模型不必数数。
PINNED = {
    'profiles': ('parent',),
    'candidates': ('orientation', 'direction_id'),
}


def assemble(pre: dict, produced: dict) -> tuple:
    """把模型返回的 `produced` 装进本地骨架 `pre`。

    返回 `(装配结果, 诊断)`。

    ## 三条硬纪律

    1. **绝不替模型编造内容。** 数组比要求短 → **直接拒绝**（编不出来），
       比要求长 → 截断并记进诊断（多出来的那些在结构上不可能存在）。
    2. **只覆盖本地已知的结构位**（`parent` / `orientation` / `direction_id`）。
       其余一律是模型的，契约照旧全量校验——**一道都没放松**。
    3. **覆盖必须留痕。** 模型写了别的值就记进 `disagreements`，
       它同时是「骨架到底有没有帮上忙」的测量数据。悄悄改掉不留痕，
       那就和放宽校验没有区别了。

    ## 为什么覆盖 `orientation` 不算「放宽」

    契约本来就要求三个朝向恰好 A、B、C 各一条——**位置与朝向是一一对应的**。
    我们只是提前告诉模型「第 0 条是 A」。而模型若把内容放错了位置，
    那条候选的**内容级**检查（贡献类型、引文出处、认领类别）仍然会失败，
    不会因为朝向被改对就蒙混过关。
    """
    if not isinstance(produced, dict):
        raise FusionError('Model result is not an object')
    report = {'model_shape': {}, 'truncated': [], 'disagreements': [], 'pinned': []}
    out = deepcopy(produced)

    for section, required in (('direction_reviews', len(pre['direction_reviews'])),
                              ('profiles', len(pre['profiles'])),
                              ('candidates', len(pre['candidates']))):
        got = out.get(section)
        report['model_shape'][section] = len(got) if isinstance(got, list) else None
        if not isinstance(got, list):
            raise FusionError(f'Model did not return a {section} array')
        if len(got) < required:
            # **编不出来就别编。** 短了说明模型没按要求填，那是要报给用户的事实，
            # 不是可以用默认值填平的「小问题」。
            raise FusionError(
                f'Model returned {len(got)} {section} but the contract requires {required}; '
                f'content cannot be invented locally')
        if len(got) > required:
            report['truncated'].append({'section': section, 'kept': required,
                                        'dropped': len(got) - required})
            del got[required:]

    for section, keys in PINNED.items():
        for i, (item, template) in enumerate(zip(out[section], pre[section])):
            for key in keys:
                want = template.get(key)
                have = item.get(key)
                if want == '':
                    continue                     # 本地没算出来，就不表态
                if have != want:
                    report['disagreements'].append(
                        {'path': f'{section}[{i}].{key}', 'model': have, 'local': want})
                item[key] = want
                report['pinned'].append(f'{section}[{i}].{key}')

    # 消融的 `removed_parent` 可以覆盖：它与「恰好两条」是一起来的，
    # 而条数与 `removed_parent` 的取值在契约里是同一个判定。
    #
    # ⚠️ **`baselines` 刻意不覆盖。** 2026-10-03 接线时踩过一次：
    # 装配阶段把 `baselines` 填回那五个，于是
    # `test_a_result_missing_a_required_baseline_is_rejected_not_downgraded` 立刻红了——
    # 一条真实的契约检查被自己改成了**永远不会失败的空断言**。
    # 骨架在**提示词里**告知模型这五个值已经够了（那是有用的），
    # 但**验收时必须让缺项真的被拒**（那更重要）。
    # 提示减失败、验收不放松——两条都要。
    for i, (item, template) in enumerate(zip(out['candidates'], pre['candidates'])):
        got = item.get('ablation_checks')
        want = template['ablation_checks']
        if not isinstance(got, list):
            raise FusionError(f'candidates[{i}].ablation_checks is not an array')
        if len(got) != len(want):
            raise FusionError(f'candidates[{i}].ablation_checks must hold exactly {len(want)}')
        for j, (g, w) in enumerate(zip(got, want)):
            if g.get('removed_parent') != w['removed_parent']:
                report['disagreements'].append(
                    {'path': f'candidates[{i}].ablation_checks[{j}].removed_parent',
                     'model': g.get('removed_parent'), 'local': w['removed_parent']})
            g['removed_parent'] = w['removed_parent']

    return out, report
