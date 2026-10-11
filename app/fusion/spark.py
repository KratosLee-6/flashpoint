"""火花评估：对一份已校验的融合结果做**确定性**的风险与类别读数。

机主 2026-09-30 的五类口径：1 比 A 好 / 2 比 B 好 / 3 与 A、B 无关但可用 /
4 比双方都好 / 5 不可用有缺失、需 AI 补足。

这条纪律很重要：M2 实验（210 次试验、7 臂、3 对）里，融合臂平均分与单臂相当甚至更低
（`fusion_without_A=6.1 > fusion=5.8`）。也就是说——**「我融合出了一个新东西」这句话，
本身没有任何证据力**。所以这里做的是「核对」而不是「相信」：

- `claimed` 是模型认领的类别，**不是结论**；
- `risks` 只报可从结果本身核对出来的问题（新机制与某一方的贡献几乎同词、
  没有把「机械拼接」和「仅模型」列入对照、缺消融检查、认领了第 5 类却没列缺口……）；
- `verified` 恒为 false——除非真的跑过对照实验，否则不声称已验证。

不调模型、不联网、不预测成败。与 `directions.py` / `capability.py` 同一条纪律。
"""
from __future__ import annotations

import re

CLASSES = {
    'beats_A': '比 A 好',
    'beats_B': '比 B 好',
    'independent_usable': '与 A、B 无关但可用',
    'beats_both': '比 A 和 B 都好',
    'needs_ai_fill': '不可用，有缺失，需要 AI 补足',
}
REQUIRED_BASELINES = ('A_only', 'B_only', 'concatenation', 'fusion', 'model_only')
_LATIN = re.compile(r'[A-Za-z0-9_]{2,}')
_CJK = re.compile(r'[一-鿿]+')


def _terms(text):
    """英文按词、中文按二元组切分。

    整段中文只会被当成一个 token，Jaccard 恒为 0——那样这个检查就形同虚设。
    """
    lowered = (text or '').casefold()
    terms = {w for w in _LATIN.findall(lowered)}
    for run in _CJK.findall(lowered):
        if len(run) == 1:
            terms.add(run)
        else:
            terms.update(run[i:i + 2] for i in range(len(run) - 1))
    return terms


def _containment(needle, haystack):
    """needle 有多大比例已经出现在 haystack 里。

    这里问的是「这段机制有多少是现成的」，不是「两段有多像」——用 Jaccard 会被
    新增部分稀释掉，一段几乎照抄、只多几个字的新机制反而得低分。
    """
    a, b = _terms(needle), _terms(haystack)
    return len(a & b) / len(a) if a else 0.0


def _overlap(left, right):
    a, b = _terms(left), _terms(right)
    return len(a & b) / len(a | b) if (a | b) else 0.0


# ── 能力层 ↔ 借用类型的接缝（39 号文 §7.8）──────────────────────────
# 「借了做法」这个声明此前**没有任何东西去核对**：`capability` 抽的是话语结构轴，
# `contribution.kind` 判的是借能力还是借做法，两套字段各说各话。
# 这里把它们接起来：看模型为「借了做法」给出的证据行，能力层在那一行上有没有结构指纹。
#
# 诚实边界——**没命中不等于说谎**：能力层有已知盲区（每轴最多留 6 条、超长行与测试路径
# 直接跳过）。所以这只是「我们手上没有这一行的结构指纹」的风险提示，不是判假。
LINKAGE_CAVEAT = '能力层每轴最多留 6 条命中，且跳过超长行与测试路径；证据行没命中只代表本层没留下指纹，不代表该声明不成立。'


def _fingerprint_index(capability):
    """(parent, path, line) -> 该行命中的结构轴集合。"""
    index = {}
    if not isinstance(capability, dict):
        return index
    for parent in ('A', 'B'):
        for axis, rows in ((capability.get(parent) or {}).get('items') or {}).items():
            for row in rows:
                ev = (row or {}).get('evidence') or {}
                key = (parent, ev.get('path'), ev.get('start_line'))
                index.setdefault(key, set()).add(axis)
    return index


def _attested(index, evidence):
    """这组证据行在能力层留下了哪些结构轴指纹。"""
    axes, unattested = set(), 0
    for ev in evidence or []:
        hit = index.get((ev.get('parent'), ev.get('path'), ev.get('start_line')))
        if hit:
            axes |= hit
        else:
            unattested += 1
    return axes, unattested


def assess_candidate(candidate, profiles, index=None, shared_axes=(), parent_texts=None):
    """单个候选的类别读数与风险。纯函数，只读结果。"""
    risks = []
    claimed = candidate['expected_outcome']['outcome_class']
    gaps = list(candidate.get('gaps_to_fill') or [])
    borrowed = {}

    # 新机制若几乎全在某一方贡献里，多半只是搬运而不是新东西
    for parent, key in (('A', 'contribution_a'), ('B', 'contribution_b')):
        similarity = _containment(candidate['new_mechanism'], candidate[key]['mechanism'])
        if similarity >= 0.6:
            risks.append({
                'code': f'new_mechanism_near_{parent.lower()}',
                'detail': f'新机制有 {similarity:.0%} 的内容已在 {parent} 的贡献里，像搬运而不是新增',
            })
    if _containment(candidate['new_mechanism'], candidate['problem']) >= 0.7:
        risks.append({'code': 'new_mechanism_is_the_problem',
                      'detail': '新机制基本是问题的复述，没有给出可执行的新做法'})

    baselines = {b for row in candidate['evaluation'] for b in row['baselines']}
    missing = [b for b in REQUIRED_BASELINES if b not in baselines]
    if missing:
        risks.append({'code': 'missing_baselines',
                      'detail': '对照臂缺 ' + '、'.join(missing) + '：没有对照就说不清比谁好'})
    if {a['removed_parent'] for a in candidate['ablation_checks']} != {'A', 'B'}:
        risks.append({'code': 'missing_ablation', 'detail': '缺少分别去掉 A / B 的消融检查'})

    if claimed in ('beats_A', 'beats_B', 'beats_both') and gaps:
        risks.append({'code': 'claims_value_with_gaps',
                      'detail': '既认领了「比…好」又列了待补缺口，结论自相矛盾'})
    if claimed == 'needs_ai_fill' and not gaps:
        risks.append({'code': 'fill_class_without_gaps', 'detail': '认领「需 AI 补足」却没说要补什么'})
    if not candidate.get('workflow') and candidate['disposition'] != 'not_recommended':
        risks.append({'code': 'no_workflow', 'detail': '没有执行流程，无法判断是否真的可用'})

    # 「借了做法」要在能力层留得下痕迹。缺能力层时（index=None）整段跳过，不假装核过。
    if index is not None:
        for parent, key in (('A', 'contribution_a'), ('B', 'contribution_b')):
            contribution = candidate[key]
            kind = contribution.get('kind')
            axes, unattested = _attested(index, contribution.get('evidence'))
            shared = sorted(axes & set(shared_axes))
            borrowed[parent] = {'kind': kind, 'attested_axes': sorted(axes),
                                'shared_axes': shared, 'unattested_evidence': unattested}
            if kind == 'way_of_working' and not axes:
                risks.append({
                    'code': f'way_of_working_evidence_unattested_{parent.lower()}',
                    'detail': f'声明「借了 {parent} 的做法」，但给出的 {unattested} 条证据行在能力层都没有结构指纹。'
                              + LINKAGE_CAVEAT,
                })

    # ── 新颖性与借用可核验（2026-10-02 机主裁决：只报告，不拒绝）──
    #
    # **为什么只报告**：今天实测新颖率受命名语言与分母影响可达 5 倍
    # （标识符 3 个时报 100%、5 个时报 0%），做拒绝条件必然误杀。
    # 分母不足时 `measurable=false`、`ratio=None`——
    # **报「不可测」而不是 0**，因为 0 会被读成「测出来是零」。
    novelty_reading = {'measurable': False, 'ratio': None, 'new_marker_count': 0,
                      'min_denominator': None,
                      'note': '未做新颖性核对（没有母包文本）'}
    borrow_reading = {'checked': False, 'note': '未做借用可核验核对'}
    if parent_texts:
        from .collide import MIN_DENOMINATOR, markers, provenance
        text_a, text_b = parent_texts
        prov = provenance(candidate['new_mechanism'], text_a, text_b)
        own = markers(candidate['new_mechanism'])
        known = markers(text_a) | markers(text_b)
        # `novelty_raw` 是**旧口径**（不设分母门槛的裸比率），原样保留作对照。
        # 留着它是为了让「为什么加了门槛」这件事可核对，而不是只听我说。
        raw = round(len(own - known) / len(own), 3) if own else None
        novelty_reading = {'measurable': prov['measurable'],
                           'ratio': prov['ratio'],
                           'new_marker_count': prov['new'],
                           'marker_count': prov['markers'],
                           'min_denominator': MIN_DENOMINATOR,
                           'novelty_raw': raw,
                           'note': prov['note']}
        # 借用声明可核验：自述的指认能不能在母包核到。
        # 实测真实产出核验率 25%–100%，编造与泛泛声明都是 0——
        # **这条判据有区分力**，所以可以标风险（但不拒绝）。
        from .collide import claimed_source_verified
        va = claimed_source_verified(str(candidate['contribution_a'].get('mechanism', '')), text_a)
        vb = claimed_source_verified(str(candidate['contribution_b'].get('mechanism', '')), text_b)
        ratios = [v['ratio'] for v in (va, vb) if v['ratio'] is not None]
        borrow_reading = {'checked': True,
                          'ratio': round(sum(ratios) / len(ratios), 3) if ratios else None,
                          'a': va, 'b': vb}
        if ratios and max(ratios) == 0.0:
            risks.append({
                'code': 'claimed_borrow_unverified',
                'detail': '两边都声称借了对方的机制，但在母包原文里一处也核不到——'
                          '**这不代表它说谎**（模型常用自己的话转述），'
                          '只代表本层无法给它任何凭据。',
            })

    return {
        'orientation': candidate['orientation'],
        'title': candidate['title'],
        'disposition': candidate['disposition'],
        'claimed_class': claimed,
        'claimed_class_label': CLASSES[claimed],
        'claimed_basis': candidate['expected_outcome']['basis'],
        'gaps_to_fill': gaps,
        'novelty': novelty_reading,
        'borrow_verification': borrow_reading,
        'borrowed': borrowed,
        'risks': risks,
        'risk_count': len(risks),
    }


def _parent_texts(capability: dict):
    """两侧母包的抽出的条目拼成的原文，供新颖性与借用核验比对。"""
    if not capability:
        return None
    out = []
    for side in ('A', 'B'):
        cap = capability.get(side) or {}
        out.append('\n'.join(
            str(entry.get('text', ''))
            for entries in (cap.get('items') or {}).values()
            for entry in entries or []))
    return tuple(out) if any(out) else None


def assess(result, capability=None):
    """整份结果的火花读数。`verified` 恒为 False——本层不做实验。

    `capability` 是 `prepare` 抽出的能力结构层。给了才做「借了做法」的接缝核对；
    没给就整段跳过并如实标出没有核过，不产出假读数。
    """
    by_parent = {p['parent']: p for p in result['profiles']}
    index = _fingerprint_index(capability)
    shared_axes = list((capability or {}).get('bridge', {}).get('shared_axes') or [])
    # 母包原文：新颖性与借用可核验都要拿它当基准。没给 capability 就整段不做，
    # 并如实标出「没核过」——**宁可没有读数，不要假读数**。
    parent_texts = _parent_texts(capability) if capability is not None else None
    candidates = [assess_candidate(c, by_parent, index if capability is not None else None,
                                   shared_axes, parent_texts)
                  for c in result['candidates']]
    claimed = sorted({c['claimed_class'] for c in candidates})
    flagged = [c for c in candidates if c['risks']]
    return {
        'version': 'spark-read-v2',
        'classes': CLASSES,
        'claimed_classes': claimed,
        'claimed_class_labels': [CLASSES[name] for name in claimed],
        'candidates': candidates,
        'needs_ai_fill': [c['title'] for c in candidates if c['claimed_class'] == 'needs_ai_fill'],
        'candidates_with_risks': len(flagged),
        'capability_linkage': {
            'checked': capability is not None,
            'shared_axes': shared_axes,
            'caveat': LINKAGE_CAVEAT if capability is not None else '未提供能力结构层，本轮未做借用类型核对。',
        },
        'meaning': '类别是模型认领的，不是结论；风险来自结果本身可核对的字段。',
        'verified': False,
        'reason_unverified': '未运行任何对照实验；认领的类别一律未经证实。',
    }
