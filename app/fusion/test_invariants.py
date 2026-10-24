"""字段级不变量的自动守卫（2026-10-01）。

背景：真实 `glm-4.7` 跑完整方案，**五次全被契约拒绝**，而五次都是
「契约判对、提示词缺边界」。每条隐含约定都要靠一次真实调用去撞——
那个代价太高。

做法：契约的字段级不变量集中声明在 `contract.PROMPT_INVARIANTS`，
由 `render_invariants()` 渲染进 SYSTEM。本文件**逐条断言**：

1. 每条不变量都真的出现在 SYSTEM 里（防止渲染被绕过或手写覆盖）；
2. 每条不变量都**有对应的契约强制**（防止清单写了契约却不强制，变成空话）。

第 2 条是关键：它把「提示词说了」和「契约真的会因为它拒绝」绑在一起。
只测第 1 条的话，清单可能慢慢变成一份没人执行的文档。
"""
import unittest
from pathlib import Path

from app.fusion.contract import (
    PROMPT_INVARIANTS, RESULT_SCHEMA, render_invariants, validate_result,
)
from app.fusion.engine import SYSTEM
from app.fusion.packages import FusionError

#: 合成夹具（2026-10-02）：带标识符的跨域一对，用来验「A/B 必须锚到 collision_id」
#: 时**真的算得出可锚定落点**。内容全部虚构，与任何真实客户材料无关。
#: A 的硬性规则里放了 `audit_log`，因为碰撞锚点只取**规则侧**条目的标识符——
#: 全中文的条目抽不到任何标识符，落点就会没有抓手（2026-10-02 实测：跨域对
#: 19.2% 的候选零锚点）。
ANCHORED_A = '\n'.join([
    '# 合同审查清单',
    '',
    '1. 登记合同编号',
    '2. 核对签字与盖章是否齐全',
    '必须用 audit_log 记录每一次核对结论',
    '必须逐条引用法条编号',
    '',
])
ANCHORED_B = '\n'.join([
    '# 检修流程',
    '',
    '如果当前平台是 Windows：',
    '1. 拆下卡尺',
    '复核扭矩是否达标',
    '必须用 torque_wrench 施加扭矩',
    '',
])


# 每条不变量都必须**指明**由哪个测试证明它真会被契约拒绝。
# 没有这条映射的话，往清单里加一条规则而不写对应强制测试，测试**照样全绿**——
# 2026-10-01 反向验证时正是这样发现的（从清单里删掉一条，18 passed 不变）。
# 有了它：新增不变量却忘了声明强制测试，这里会红。
ENFORCEMENT = {
    'profiles_exactly_two': 'test_profiles_exactly_two',
    'candidates_exactly_three': 'test_candidates_exactly_three_with_each_orientation',
    'candidate_must_anchor_to_collision': 'test_ab_candidates_must_anchor_to_collision',
    'retention_arrays_non_empty': 'test_retention_arrays_non_empty',
    'capabilities_non_empty': 'test_capabilities_non_empty',
    'ablations_exactly_two': 'test_ablations_exactly_two',
    'baselines_exactly_five': 'test_baselines_exactly_five',
    'contribution_kind_required': 'test_contribution_kind_required',
    'gap_lock_both_directions': 'test_gap_lock_both_directions',
    'direction_reviews_mirror_suggestions': 'test_direction_reviews_mirror_suggestions',
    'independent_has_two_meanings': 'test_independent_has_two_meanings_keeps_the_underlying_rule',
    'evidence_path_must_exist': 'test_an_invented_evidence_path_is_rejected',
    'artifact_path_needs_extension': 'test_a_proposed_artifact_path_needs_an_extension',
    'blocked_needs_blockers': 'test_blocked_needs_blockers',
    'blockers_imply_blocked': 'test_a_candidate_with_blockers_cannot_be_a_hypothesis',
    'no_extra_fields': 'test_no_extra_fields',
    'coverage_reflects_missing': 'test_coverage_reflects_missing',
    'workflow_needed_unless_rejected': 'test_workflow_needed_unless_rejected',
    'no_unescaped_quotes_in_string_values': 'test_a_missing_escape_costs_the_whole_response',
    'no_evidence_sha256': 'test_evidence_sha256_is_filled_locally_and_never_accepted_from_the_model',
}


class InvariantRenderingTests(unittest.TestCase):
    def test_every_invariant_reaches_the_system_prompt(self):
        for item in PROMPT_INVARIANTS:
            with self.subTest(invariant=item['id']):
                self.assertIn(item['text'], SYSTEM,
                              f"不变量 {item['id']} 没有出现在 SYSTEM 里——"
                              f"模型看不见的规则等于不存在")

    def test_ids_are_unique(self):
        ids = [i['id'] for i in PROMPT_INVARIANTS]
        self.assertEqual(len(ids), len(set(ids)), '不变量 id 重复，后续没法逐条定位')

    def test_every_invariant_declares_how_it_is_enforced(self):
        """新增不变量却忘了写强制测试 → 这里红。

        缺了这条，登记本身就退化成一份没人核对的文档：契约加了规则、清单加了条目、
        但没有任何测试证明那条规则真会拒绝结果。
        """
        for item in PROMPT_INVARIANTS:
            with self.subTest(invariant=item['id']):
                name = ENFORCEMENT.get(item['id'])
                self.assertIsNotNone(name, f"不变量 {item['id']} 没有在 ENFORCEMENT 里登记强制测试")
                self.assertTrue(hasattr(InvariantEnforcementTests, name),
                                f"ENFORCEMENT 指向的 {name} 不存在")

    def test_no_stale_registration(self):
        for name, method in ENFORCEMENT.items():
            with self.subTest(name=name):
                self.assertIn(name, {i['id'] for i in PROMPT_INVARIANTS},
                              f'{name} 已不在清单里，ENFORCEMENT 条目应一并删除')

    def test_the_block_is_labelled_as_enforced(self):
        rendered = render_invariants()
        self.assertIn('ENFORCED by the validator', rendered,
                      '必须声明这段是被强制的，否则模型会当成建议')


class InvariantEnforcementTests(unittest.TestCase):
    """每条不变量都要有**真的会让结果被拒**的对应规则。"""

    def setUp(self):
        import tempfile
        from pathlib import Path
        from app.fusion.engine import prepare
        from app.fusion.test_capability import LAW, REPAIR
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        a, b = root / 'a', root / 'b'
        a.mkdir()
        b.mkdir()
        (a / 'SKILL.md').write_text(LAW, encoding='utf-8', newline='')
        (b / 'SKILL.md').write_text(REPAIR, encoding='utf-8', newline='')
        self.packet = prepare(a, b, '把合同审查的收敛方法用到汽车维修上')

    def proposal(self):
        from app.fusion.test_fusion import proposed
        return proposed(self.packet)

    def rejects(self, mutate, why):
        self.rejects_on(self.packet, mutate, why)

    def rejects_on(self, packet, mutate, why):
        from app.fusion.test_fusion import proposed
        result = proposed(packet)
        mutate(result)
        with self.assertRaises(FusionError, msg=why):
            validate_result(result, packet)

    def anchored_packet(self):
        """一个**确实算出了可锚定落点**的 packet。

        为什么不用共享夹具（`LAW` × `REPAIR`）：那两份是纯中文散文，
        条目里一个英文标识符都没有，2026-10-02 起「零抓手的候选」改列
        `collision.unanchored`——于是 `candidates` 是空的，
        拿它跑「A/B 必须锚到 collision_id」等于**什么都没测**，
        旧写法会 `skipTest` 掉。宁可多建一个夹具，也不能让守卫悄悄不跑。

        合成内容，与真实客户材料无关。
        """
        import tempfile
        from pathlib import Path
        from app.fusion.engine import prepare
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        a, b = root / 'a', root / 'b'
        a.mkdir()
        b.mkdir()
        (a / 'SKILL.md').write_text(ANCHORED_A, encoding='utf-8', newline='')
        (b / 'SKILL.md').write_text(ANCHORED_B, encoding='utf-8', newline='')
        packet = prepare(a, b, '把合同审查的收敛方法用到汽车维修上')
        self.assertTrue((packet.get('collision') or {}).get('candidates'),
                        '这个夹具必须算出可锚定落点，否则下面那条守卫又是空跑')
        return packet

    def test_profiles_exactly_two(self):
        self.rejects(lambda r: r['profiles'].pop(), '少一个 profile 必须被拒')

    def test_candidates_exactly_three_with_each_orientation(self):
        self.rejects(lambda r: r['candidates'].pop(), '少一个方向必须被拒')
        self.rejects(lambda r: r['candidates'][1].__setitem__('orientation', 'A'),
                     '方向重复、缺一个 orientation 必须被拒')

    def test_retention_arrays_non_empty(self):
        for key in ('keep', 'discard'):
            for index in (0, 1):
                self.rejects(
                    lambda r, k=key, i=index: r['profiles'][i]['retention'].__setitem__(k, []),
                    f'retention.{key} 为空必须被拒')

    def test_capabilities_non_empty(self):
        self.rejects(lambda r: r['profiles'][0].__setitem__('capabilities', []),
                     'capabilities 为空必须被拒')

    def test_ablations_exactly_two(self):
        self.rejects(lambda r: r['candidates'][0]['ablation_checks'].pop(),
                     '消融检查少于两条必须被拒')

    def test_baselines_exactly_five(self):
        self.rejects(lambda r: r['candidates'][0]['evaluation'][0].__setitem__(
            'baselines', ['A_only', 'B_only', 'concatenation', 'fusion']),
            '对照臂少于五臂必须被拒')

    def test_contribution_kind_required(self):
        self.rejects(lambda r: r['candidates'][0]['contribution_a'].pop('kind'),
                     'contribution.kind 缺失必须被拒')
        self.rejects(lambda r: r['candidates'][0]['contribution_a'].__setitem__('kind', 'vibes'),
                     'contribution.kind 非法值必须被拒')

    def test_gap_lock_both_directions(self):
        # 夹具里三个候选本来都是 needs_ai_fill；测「反向」必须**同时**改成价值类，
        # 只加缺口不算违规——那正是 needs_ai_fill 应有的样子。
        def value_class_with_gaps(result):
            result['candidates'][0]['expected_outcome'] = {
                'outcome_class': 'beats_both', 'basis': 'mechanism beats both parents'}
            result['candidates'][0]['gaps_to_fill'] = ['缺题库']
        self.rejects(value_class_with_gaps, '价值类候选列缺口必须被拒（反向）')
        self.rejects(lambda r: r['candidates'][2].__setitem__('gaps_to_fill', []),
                     '第 5 类不列缺口必须被拒（正向）')

    def test_direction_reviews_mirror_suggestions(self):
        self.rejects(lambda r: r['direction_reviews'].append(
            {'direction_id': 'invented', 'decision': 'adopt', 'reason': 'r'}),
            '编造 direction ID 必须被拒')

    def test_independent_has_two_meanings_keeps_the_underlying_rule(self):
        # 提示词解释「independent 两义」，契约真正强制的是第 3 类的 kind 要求
        def claim_independent(result):
            result['candidates'][2]['expected_outcome'] = {
                'outcome_class': 'independent_usable',
                'basis': 'borrowed the working shape from both'}
            result['candidates'][2]['disposition'] = 'hypothesis'
        self.rejects(claim_independent,
                     '认领第 3 类但贡献仍按 capability 交账必须被拒')

    def test_an_invented_evidence_path_is_rejected(self):
        """2026-10-01 真实采样抓到：模型在**凭空编造文件名**。

        `safe_path` 报出的 offender 是 `auth_audit_rules.` 与 `auth_sinks.`——
        查真实包，两个包里**只有** SKILL.md / icon.png / icon.svg，
        根本没有这两个文件。模型把 SKILL.md 里的章节标题
        （"### 4) 5 类越界规则"、"SINKS"）当成了文件名。
        这是**捏造**不是转写误差，因此**绝不放宽 safe_path**，只补提示词边界。
        """
        for invented in ('auth_audit_rules.md', 'auth_sinks.py', 'notes/extra.md'):
            with self.subTest(path=invented):
                self.rejects(
                    lambda r, p=invented: r['profiles'][0]['capabilities'][0]['evidence'][0]
                    .__setitem__('path', p),
                    f'包内不存在的路径 {invented} 必须被拒')

    def test_the_actual_file_of_that_package_is_still_accepted(self):
        """补边界不能误伤：清单里真实存在的路径照常可用。"""
        from app.fusion.engine import inspect_packages
        for entry in self.packet['packages']['A']['files']:
            with self.subTest(path=entry['path']):
                result = self.proposal()
                result['profiles'][0]['capabilities'][0]['evidence'][0]['path'] = entry['path']
                validate_result(result, self.packet)

    def test_blocked_needs_blockers(self):
        def blocked_without_blockers(result):
            result['candidates'][0]['disposition'] = 'blocked'
            result['candidates'][0]['blockers'] = []
        self.rejects(blocked_without_blockers, '判 blocked 却不给 blockers 必须被拒')

    def test_a_candidate_with_blockers_cannot_be_a_hypothesis(self):
        """**这条规则原先只有一半在提示词里。**

        2026-10-01 跑 E3 的融合材料时撞上：`A candidate with blockers must be blocked
        or not_recommended`——模型给了非空 blockers 却标 `hypothesis`。
        不变量清单里原本只有 `blocked_needs_blockers`（blocked 必须有 blockers），
        **缺了反向的「有 blockers 就不能是 hypothesis」**，而这两条在契约里是成对的。
        """
        def blockers_but_hypothesis(result):
            # 必须**同时**把 disposition 改成 hypothesis：夹具里候选本来就是 blocked，
            # 只加 blockers 并不构成违规（这正是我第一版测试写错的地方）。
            result['candidates'][0]['disposition'] = 'hypothesis'
            result['candidates'][0]['blockers'] = ['缺少目标域的资料']
        self.rejects(blockers_but_hypothesis, '列了 blockers 却标 hypothesis 必须被拒')

        def blockers_and_blocked_is_fine(result):
            result['candidates'][2]['disposition'] = 'blocked'
            result['candidates'][2]['blockers'] = ['缺少目标域的资料']
            result['candidates'][2]['expected_outcome'] = {
                'outcome_class': 'needs_ai_fill', 'basis': '缺目标域资料'}
            result['candidates'][2]['gaps_to_fill'] = ['目标域资料']
        # 反方向：blocked + blockers 应当被接受（不得因为补了这条就把合法写法堵掉）
        result = self.proposal()
        blockers_and_blocked_is_fine(result)
        validate_result(result, self.packet)

    def test_no_extra_fields(self):
        self.rejects(lambda r: r.__setitem__('extra_thing', 1), '多出字段必须被拒')
        self.rejects(lambda r: r['candidates'][0].__setitem__('bonus', 1),
                     '嵌套层级多出字段同样必须被拒')

    def test_coverage_reflects_missing(self):
        def covered_with_missing(result):
            result['profiles'][0]['missing'] = ['缺一份题库']
            result['profiles'][0]['coverage'] = 'sufficient_for_proposal'
        self.rejects(covered_with_missing, '有 missing 却声称充分必须被拒')

    def test_workflow_needed_unless_rejected(self):
        self.rejects(lambda r: r['candidates'][0].__setitem__('workflow', []),
                     '非 not_rejected 方向没有流程必须被拒')

    def test_evidence_sha256_is_filled_locally_and_never_accepted_from_the_model(self):
        """`no_evidence_sha256` 的「强制」：**值只能来自本地**。

        这条不是契约规则（契约里 `sha256` 仍然必填、仍然校验一致性），
        它管的是**模型不许自己写**。所以这里要验两件事：

        1. **模型写了会被本地覆盖**——不给模型「编一个像模像样的哈希」的机会；
        2. **本地填的永远是真实文件的值**——`path` 不在清单里就填不上，
           于是那条引文会**因为缺 sha256 而被契约拒**，而不是被悄悄放过。

        顺带说明为什么这不是放宽：真正判定「模型是不是在编」的
        **从来是 `path` 在不在清单 + `quote` 是不是原文逐字**，那两条一个字没动。
        而 sha256 从来就没有证明力——任何模型都能编一个格式正确的哈希。
        """
        from app.fusion.engine import prepare, _text_files
        from app.fusion.skeleton import fill_evidence_hashes

        a, b = (Path(r) for r in _example_packages())
        packet = prepare(a, b, goal='')
        files = _text_files(packet)

        real = next(iter(files.values()))['sha256']
        on_disk = {'evidence': [{'parent': 'A', 'path': next(iter(files))[1],
                                 'sha256': 'f' * 64, 'start_line': 1, 'end_line': 1,
                                 'quote': 'x'}]}
        filled = fill_evidence_hashes(on_disk, files)
        self.assertEqual(filled, 1)
        self.assertEqual(on_disk['evidence'][0]['sha256'], real,
                         '模型自己写的假哈希没被本地真值覆盖——那等于又让它编')

        missing = {'evidence': [{'parent': 'A', 'path': '不存在的文件.md',
                                'start_line': 1, 'end_line': 1, 'quote': 'x'}]}
        self.assertEqual(fill_evidence_hashes(missing, files), 0)
        self.assertNotIn('sha256', missing['evidence'][0],
                         '文件不存在却给它填了哈希——那条引文必须由契约拒，而不是被放过')

    def test_a_missing_escape_costs_the_whole_response(self):
        """`no_unescaped_quotes_in_string_values` 的「强制」不是契约规则，
        而是**后果**：模型照做失败时，整份产出被拒，**什么都不会被静默存下来**。

        2026-10-04 实测（M3）：六次采样里四次死在严格 JSON 上，
        其中就有这种「开引号转了、闭引号忘了」的形态。提示词侧已经加了一条规则
        降低它，但这个测试守的是**兜底**：万一模型还是犯了，结果必须是**被拒**
        而不是被猜着修好——那正是本产品反复拒绝的「从一堆文本里猜哪段是答案」。
        """
        from app.fusion.json_repair import parse_lenient

        broken = '{"quote": "分支（platform.system() == \\"Windows"）的说明"}'
        with self.assertRaises(FusionError) as ctx:
            parse_lenient(broken)
        message = str(ctx.exception)
        self.assertIn('json 自己的判定', message,
                      '报错必须带 json 的原始判定和位置，否则无从定位')
        self.assertNotIn('Windows', message.replace('json 自己的判定', ''),
                         '报错不得回显模型原文（它可能含提示词或凭据）')

    def test_a_proposed_artifact_path_is_not_judged_by_the_package_path_rules(self):
        """**这是产品缺陷，不是模型问题**（2026-10-01 真实 glm-4.7 抓包发现）。

        `artifacts[].path` 指的是**新 Skill 要造出来的文件**，本来就不在包里；
        但契约对它调用了 `safe_path`（包内相对路径校验器）。真实输出里模型写的
        graded_degradation_report.py / soft_evidence_prover.py /
        policy_compliance_checker.py **全部是正确写法**，却被判成
        `Unsafe package-relative path`。

        此前六批都在改提示词、试图让模型「别编造文件名」——而模型根本没在
        `evidence.path` 上编造（那一轮原始输出里全是 `SKILL.md`，完全正确）。
        **真正的 offender 在 artifacts 字段上。**
        """
        from app.fusion.contract import proposed_path
        for name in ('graded_degradation_report.py', 'soft_evidence_prover.py',
                     'policy_compliance_checker.py', 'scripts/tool.py'):
            with self.subTest(name=name):
                self.assertEqual(proposed_path(name), name,
                                 '产出处路径是合法的，不该套用包内路径规则')

    def test_a_proposed_artifact_path_still_cannot_escape_or_hit_a_device(self):
        """放开「必须在包内」不等于放开安全——路径穿越与保留名仍必须被拒。"""
        from app.fusion.contract import proposed_path, FusionError as ContractError
        for name, hint in (('../evil.py', 'stay inside'), ('a/../../b.py', 'stay inside'),
                           ('con.py', 'reserved device'), ('C:/x.py', 'plain relative'),
                           ('a\\b.py', 'plain relative')):
            with self.subTest(name=name):
                with self.assertRaises(ContractError) as raised:
                    proposed_path(name)
                self.assertIn(hint, str(raised.exception))

    def test_a_proposed_artifact_path_needs_an_extension(self):
        """契约里可验证的规则，不是又一段提示词。

        实据来自循环探针抓到的原始输出：模型写出 `audit_report_schema.`，
        而它的 purpose 明明是「JSON Schema 定义」——**扩展名没写完**。
        同一份 content：6776/8192 token、reasoning_tokens 0、finish_reason stop、
        JSON 解析成功、截断点之后结构完整 → **不是输出截断，是模型主动写错**。

        因此**不能自动补全**（补成 .py/.json/.md 都没有依据），只能要求它写全。
        """
        from app.fusion.contract import proposed_path, FusionError as ContractError
        for good in ('ast_verify_scanner.py', 'schema.json', 'checklist.md'):
            with self.subTest(path=good):
                self.assertEqual(proposed_path(good), good)
        for bad in ('audit_report_schema', 'plain_name'):
            with self.subTest(path=bad):
                with self.assertRaises(ContractError) as raised:
                    proposed_path(bad)
                self.assertIn('extension', str(raised.exception),
                              '报错要说清是缺扩展名，而不是含糊的「路径非法」')

    def test_a_truncated_artifact_name_says_so_instead_of_looking_like_a_traversal_attack(self):
        """以点结尾几乎总是「文件名被写断了」，报错要说清楚，别让用户以为有安全问题。"""
        from app.fusion.contract import proposed_path, FusionError as ContractError
        with self.assertRaises(ContractError) as raised:
            proposed_path('auth_audit_rules.')
        self.assertIn('cut short', str(raised.exception))
        self.assertNotIn('unsafe', str(raised.exception).lower())

    def test_schema_still_declares_every_field_required(self):
        # 不变量清单与 schema 必须同步演化：schema 放松会削弱清单的强制性
        for key in ('profiles', 'candidates', 'direction_reviews'):
            with self.subTest(key=key):
                self.assertIn(key, RESULT_SCHEMA['required'])


    def test_ab_candidates_must_anchor_to_collision(self):
        """A/B 方向的 `direction_id` 必须是本地算出的 `collision_id`。

        这条同时堵死 `independent` 的两义：orientation 与 direction_id 是两个
        互不相干的字段，模型过去能把 A/B 也挂 `independent`，契约只能靠
        「恰好可满足」放过去。锚点由本地给之后，A/B 无处可挂。

        **必须在一个真的算出了落点的 packet 上跑**：共享夹具是纯中文散文，
        零抓手的候选已改列 `unanchored`，拿它跑会变成空跑并 `skipTest`。
        所以这里在两个 packet 上各跑一遍，并断言至少有一个真的有落点。
        """
        checked = 0
        for packet in (self.packet, self.anchored_packet()):
            collisions = (packet.get('collision') or {}).get('candidates') or []
            if not collisions:
                continue
            checked += 1
            for orientation in ('A', 'B'):
                self.rejects_on(
                    packet,
                    lambda r, o=orientation: r['candidates'][
                        'ABC'.index(o)].__setitem__('direction_id', 'independent'),
                    f'orientation {orientation} 挂 independent 必须被拒')
                self.rejects_on(
                    packet,
                    lambda r, o=orientation: r['candidates'][
                        'ABC'.index(o)].__setitem__('direction_id', 'invented-id'),
                    f'orientation {orientation} 挂不存在的 collision_id 必须被拒')
        self.assertGreater(checked, 0, '两个夹具都没算出落点——本条等于没跑')

    def test_orientation_c_may_still_use_independent(self):
        """**第 3 类是一等公民**：C 方向没有母包落点可锚，必须仍被允许。

        这条是防误杀的对称面：只测「A/B 被拒」而不管 C，
        新判据就可能悄悄把第三类打死。
        """
        from app.fusion.contract import validate_result
        result = self.proposal()
        result['candidates'][2]['direction_id'] = 'independent'
        validate_result(result, self.packet)   # 不抛异常即通过

    def test_a_title_that_echoes_the_material_is_rejected(self):
        """标题复述 → **硬拒绝**（复述分级处理的第一级）。"""
        from app.fusion.capability import POLICY
        text = next(iter((self.packet['capability']['A']['items']
                          .get('constraints') or [{'text': 'x unique supplied rule text'}])))['text']
        self.rejects(lambda r: r['candidates'][0].__setitem__('title', text[:40]),
                     '标题照抄材料必须被拒')

    def test_a_new_title_is_still_accepted(self):
        """**不误杀的对称面**：只测「复述被拒」而不测「新标题被收」，
        这条判据就可能变成「什么都不许写」。"""
        from app.fusion.contract import validate_result
        result = self.proposal()
        for i, cand in enumerate(result['candidates']):
            cand['title'] = f'Distinctly new proposal number {i} with its own wording'
        validate_result(result, self.packet)   # 不抛异常即通过


def _example_packages():
    """一对**真实**示例包。少一个就直接炸——**不许 skip**：
    跳过比红更危险，路径层级算错会让守卫看着是绿的、其实什么都没验
    （`test_relocate.py` 就这么被静默跳过过一轮）。"""
    root = Path(__file__).resolve().parents[2]
    for folder in ('skillhub-skill', 'app/examples'):
        found = [root / folder / n
                 for n in ('ast-static-scan-arch', 'device-key-attestation')
                 if (root / folder / n / 'SKILL.md').exists()]
        if len(found) == 2:
            return str(found[0]), str(found[1])
    raise AssertionError('找不到成对的示例包（根目录 %s）' % root)


if __name__ == '__main__':
    unittest.main()
