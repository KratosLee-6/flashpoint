"""能力结构抽取的回归：重点是「跨域且零共同词汇」必须仍然产生结构落点。

这是 39 号文 40 号裁决的核心断言——`directions.py` 在这个场景产出为空，
`capability.py` 必须给出东西，否则换原语这件事就只是换了个名字。
"""
import hashlib
import re
import tempfile
import unittest
from pathlib import Path

from app.fusion.capability import POLICY, bridge, extract, policy_digest
from app.fusion.directions import directions
from app.fusion.engine import messages, prepare, select_context, verify_packet
from app.fusion.packages import FusionError


def source(text, label='SKILL.md'):
    digest_hex = hashlib.sha256(text.encode('utf-8')).hexdigest()
    return {'label': label, 'sha256': digest_hex,
            'files': [{'path': label, 'status': 'text', 'text': text, 'sha256': digest_hex}],
            'references': []}


LAW = """# 合同审查要点
1. 识别关键条款并标注效力层级
2. 评估风险等级
3. 出具风险清单
交付物：风险审查报告。
必须逐条标注依据条款；不负责代写合同。
"""
REPAIR = """# 汽车故障诊断
1. 确认异响位置与工况
2. 列出可能原因
3. 按序验证并排除
如果验证不通过，则回到第 2 步重新假设。
校验：复检确认故障消失后交付维修建议。
"""
TEACH = """# 课堂讲授设计
1. 拆解知识点
2. 设计分层练习
如果学生卡住，改用讲解路径。
必须给出可观察的达标标准；不支持一对一辅导。
"""


class CapabilityTests(unittest.TestCase):
    def test_structure_is_extracted_from_unrelated_domains(self):
        law = extract('A', source(LAW))
        repair = extract('B', source(REPAIR))
        for cap, must in ((law, ['steps', 'deliverables', 'constraints', 'boundaries']),
                          (repair, ['steps', 'conditions', 'checks', 'deliverables'])):
            for axis in must:
                self.assertIn(axis, cap['profile']['has'], f'{cap["label"]} 应抽出 {axis}')

    def test_cross_domain_pair_with_no_shared_vocabulary_still_finds_a_surface(self):
        law, repair = extract('A', source(LAW)), extract('B', source(REPAIR))
        result = bridge(law, repair)
        self.assertEqual(result['status'], 'cross_domain_surface_found')
        self.assertIn('steps', result['shared_axes'])
        self.assertTrue(result['surfaces'], '跨域且零共同词汇时必须给出结构落点')
        self.assertFalse(result['semantic_verified'], '本层不得声称已理解语义')

    def test_every_item_carries_traceable_evidence(self):
        cap = extract('A', source(LAW))
        for axis, rows in cap['items'].items():
            for row in rows:
                ev = row['evidence']
                self.assertTrue(ev['path'] and ev['start_line'] and ev['sha256'],
                                f'{axis} 的条目必须可回查')

    def test_shapes_require_their_axes_together(self):
        cap = extract('A', source(REPAIR))
        self.assertIn('ordered_verified', cap['profile']['shapes'], '步骤+校验应命中 ordered_verified')
        self.assertIn('deliverable_making', cap['profile']['shapes'])

    def test_disjoint_structures_report_no_overlap_instead_of_inventing_one(self):
        # 这份材料一个字都没写做法：能力层抽不出任何轴，这才是「真·不相交」。
        flat = extract('A', source('# 标题\n一段没有任何结构标记的说明文字。\n'))
        self.assertEqual(flat['profile']['has'], [], '这个夹具必须真的抽不出轴，否则测的不是不相交')
        result = bridge(flat, extract('B', source(REPAIR)))
        self.assertEqual(result['status'], 'no_structural_overlap')
        self.assertEqual(result['surfaces'], [])
        self.assertEqual(result['no_surface_reason'], 'no_shared_axes')

    def test_one_shared_axis_is_reported_as_below_threshold_not_as_no_overlap(self):
        """「没落点」有两种性质完全不同的机制，混成一句话就是在骗人。

        真实包实测（`layer_contrast.py`，9 包 36 对）里 3 个负面案例**全部**是
        「共有 1 个轴、门槛 2」——此时若报「没有共同项」，用户会以为材料不兼容，
        而真实情况是差一个轴就够。给不出落点不等于没有交集。
        """
        thin = extract('A', source('# 校验\n逐条核对结果并留下记录。\n'))
        self.assertEqual(thin['profile']['has'], ['checks'])
        result = bridge(thin, extract('B', source(REPAIR)))
        self.assertEqual(result['status'], 'no_structural_overlap')
        self.assertEqual(result['surfaces'], [])
        self.assertEqual(result['no_surface_reason'], 'below_threshold')
        self.assertTrue(result['shared_axes'], '既然是「差一个轴」，就必须如实报出共有哪几个轴')
        self.assertEqual(result['min_shared_axes'], POLICY['bridge']['min_shared_axes'])
        self.assertIn('门槛', result['note'], '必须说清是门槛没到，而不是材料不兼容')
        self.assertNotIn('没有共同项', result['note'], '这句是假的：两边确实有共同轴')

    def test_a_pair_with_no_shared_axes_says_so_plainly(self):
        """与上面那条互补：这里只管**措辞**——真不相交时要说「没有共同项」，
        并且绝不能把门槛扯进来。两类失败的文案不能互相污染。"""
        flat = extract('A', source('# 标题\n一段没有任何结构标记的说明文字。\n'))
        result = bridge(flat, extract('B', source(REPAIR)))
        self.assertEqual(result['no_surface_reason'], 'no_shared_axes')
        self.assertIn('没有任何共同项', result['note'])
        self.assertNotIn('门槛', result['note'], '这里和门槛无关，别把两类失败搅在一起')

    def test_negated_capability_becomes_a_boundary_not_a_capability(self):
        cap = extract('A', source(TEACH))
        self.assertIn('boundaries', cap['profile']['has'])
        # 「不支持一对一辅导」不得被当成能力项
        self.assertNotIn('audience', cap['profile']['has'])

    def test_policy_is_versioned_and_digest_is_stable(self):
        self.assertEqual(POLICY['version'], 'capability-structure-v1')
        self.assertEqual(policy_digest(), policy_digest())

    def test_extraction_ignores_test_paths(self):
        pkg = {'label': 'SKILL.md', 'sha256': 'x',
               'files': [{'path': 'tests/fixture.md', 'status': 'text',
                          'text': '1. fake step\n2. fake step', 'sha256': 'x'}],
               'references': []}
        self.assertEqual(extract('A', pkg)['profile']['axis_counts']['steps'], 0)


class CapabilityInPacketTests(unittest.TestCase):
    """能力层进入 prepare 的 packet 之后，必须仍然满足同一套纪律。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.a, self.b = root/'a', root/'b'
        for path, text in ((self.a, LAW), (self.b, REPAIR)):
            path.mkdir()
            (path/'SKILL.md').write_text(text, encoding='utf-8', newline='')

    def packet(self, goal='把合同审查的收敛方法用到汽车维修上'):
        return prepare(self.a, self.b, goal)

    def test_prepare_carries_the_capability_layer(self):
        packet = self.packet()
        self.assertIn('capability', packet)
        for side in ('A', 'B'):
            self.assertIn(side, packet['capability'])
            self.assertFalse(packet['capability'][side]['understood'])
        self.assertIn('bridge', packet['capability'])

    def test_packet_schema_moved_to_v6_and_still_verifies(self):
        packet = self.packet()
        self.assertEqual(packet['schema_version'], 6)
        verify_packet(packet)  # 不抛异常即通过

    def test_the_packet_carries_local_collision_candidates(self):
        packet = self.packet()
        self.assertIn('collision', packet)
        self.assertIn('candidates', packet['collision'])
        self.assertFalse(packet['collision']['semantic_verified'])
        # 碰撞候选必须**随请求送出去**，否则模型看不到落点，
        # 「碰撞发现权收回本地」就变成了一句空话。
        sent = messages(packet)
        blob = '\n'.join(m['content'] for m in sent)
        self.assertIn('collision', blob)
        for cand in packet['collision']['candidates']:
            self.assertIn(cand['collision_id'], blob,
                          f'落点 {cand["collision_id"]} 必须出现在送给模型的内容里')

    def test_tampering_with_collision_candidates_breaks_verification(self):
        """碰撞候选是**契约锚点唯一可信的来源**（模型碰不到它），
        所以它必须和 capability 一样受重算校验保护。"""
        packet = self.packet()
        packet['collision']['candidates'].append({'collision_id': 'forged'})
        with self.assertRaises(FusionError):
            verify_packet(packet)

    def test_tampering_with_the_capability_layer_breaks_the_packet_hash(self):
        packet = self.packet()
        packet['capability']['bridge']['shared_axes'] = ['steps']
        with self.assertRaises(FusionError):
            verify_packet(packet)

    def test_cross_domain_pair_gets_a_surface_where_the_old_layer_gave_nothing(self):
        packet = self.packet()
        self.assertEqual(packet['rule_directions']['status'], 'needs_semantic_exploration',
                         '旧层在这个场景本就产不出方向（这是换原语的动机）')
        self.assertEqual(packet['capability']['bridge']['status'], 'cross_domain_surface_found')
        self.assertTrue(packet['capability']['bridge']['surfaces'])
        self.assertFalse(packet['capability']['bridge']['semantic_verified'])

    def test_capability_surfaces_carry_traceable_quotes(self):
        surfaces = self.packet()['capability']['bridge']['surfaces']
        self.assertTrue(surfaces)
        for line in surfaces:
            self.assertIn('——', line, '每条落点都要说明它为什么是落点')

    def test_select_context_recomputes_both_layers_consistently(self):
        packet = self.packet()
        result = select_context(packet, packet['context_selection'])
        self.assertEqual(result['capability'], packet['capability'],
                         '同一份材料上下文下，能力层必须是确定的')
        self.assertEqual(result['rule_directions'], packet['rule_directions'])


class CapabilityReachesTheModelTests(unittest.TestCase):
    """能力层必须真的到达模型，否则它只是界面上的装饰。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.a, self.b = root/'a', root/'b'
        for path, text in ((self.a, LAW), (self.b, REPAIR)):
            path.mkdir()
            (path/'SKILL.md').write_text(text, encoding='utf-8', newline='')
        self.packet = prepare(self.a, self.b, '把合同审查的收敛方法用到汽车维修上')

    def wire(self):
        from app.fusion.engine import messages
        import json as _json
        return _json.loads(messages(self.packet)[1]['content'])

    def test_model_payload_carries_the_capability_layer(self):
        user = self.wire()
        self.assertIn('capability', user)
        self.assertEqual(user['capability']['bridge']['status'], 'cross_domain_surface_found')

    def test_cross_domain_still_reaches_the_model_when_rules_find_nothing(self):
        user = self.wire()
        self.assertEqual(self.packet['rule_directions']['status'], 'needs_semantic_exploration')
        self.assertTrue(user['capability']['bridge']['surfaces'],
                        '规则层空的时候，结构层必须顶上，否则跨域对等于直接断路')

    def test_system_prompt_forbids_wholesale_merging_and_demands_explicit_tradeoffs(self):
        from app.fusion.engine import SYSTEM
        lowered = SYSTEM.lower()
        self.assertIn('never merge the two packages wholesale', lowered)
        self.assertIn('retention', lowered)
        self.assertIn('diluting both sources is a real failure mode', lowered)

    def test_a_result_without_explicit_retention_is_rejected(self):
        from app.fusion.contract import validate_result
        from app.fusion.test_fusion import proposed
        result = proposed(self.packet)
        del result['profiles'][0]['retention']
        with self.assertRaises(FusionError):
            validate_result(result, self.packet)

    def test_retention_requires_both_sides_to_state_something_kept_and_dropped(self):
        from app.fusion.contract import validate_result
        from app.fusion.test_fusion import proposed
        for mutate in (lambda r: r['profiles'][0].__setitem__('retention', {'keep': ['x'], 'discard': [], 'reason': 'r'}),
                       lambda r: r['profiles'][1].__setitem__('retention', {'keep': [], 'discard': ['x'], 'reason': 'r'})):
            result = proposed(self.packet)
            mutate(result)
            with self.assertRaises(FusionError):
                validate_result(result, self.packet)


class UiAxisLabelParityTests(unittest.TestCase):
    """界面上的结构轴中文名必须与 POLICY 的轴一一对应。

    这条映射在 `fusion-ui.js` 里手写，服务端改了轴而界面没跟上时，
    用户会直接看到 `conditions` 这种内部键名——而界面上不该出现内部键名。
    """
    UI = Path(__file__).resolve().parents[1] / 'fusion-ui.js'

    def test_every_axis_has_a_ui_label_and_the_ui_invents_none(self):
        from app.fusion.capability import POLICY
        source = self.UI.read_text(encoding='utf-8')
        match = re.search(r'const POLICY_AXIS=\{(.*?)\};', source, re.S)
        self.assertIsNotNone(match, 'fusion-ui.js 里的 POLICY_AXIS 改名或被删了')
        pairs = dict(re.findall(r"(\w+):'([^']+)'", match.group(1)))
        self.assertEqual(set(pairs), set(POLICY['axes']),
                         f'界面轴名与服务端轴不一致；服务端新增/删除轴时要同步 POLICY_AXIS')
        for axis, label in pairs.items():
            # 界面允许用短名（「条件」对「条件分支 / conditions」），但不允许另起一个名字。
            self.assertIn(label, POLICY['axes'][axis]['label'],
                          f'轴 {axis} 的界面中文名与服务端不是同一个词')


class EmptyDirectionReviewsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        a, b = root/'a', root/'b'
        a.mkdir()
        b.mkdir()
        (a/'SKILL.md').write_text(LAW, encoding='utf-8', newline='')
        (b/'SKILL.md').write_text(REPAIR, encoding='utf-8', newline='')
        self.packet = prepare(a, b, '把合同审查的收敛方法用到汽车维修上')

    """旧层为空时 `direction_reviews` 必须为空——这条边界是被真实模型逼出来的。

    2026-10-01 首次真实 `glm-4.7` 完整生成：重锚定修好了 5 处引文后，契约拒绝的原因是
    `Every supplied direction needs exactly one review`。而那一对的
    `rule_directions.suggestions` 是**空数组**（旧层报 `needs_semantic_exploration`），
    契约因此要求 `direction_reviews` 也为空——模型却自己造了一条。
    契约本身是对的（不允许编造 direction ID），是 SYSTEM 只说了「review every suggestion」
    而没说「一个都没有时必须空着」，也没说**能力层的结构落点不是方向、不可审**。
    """

    def test_the_system_prompt_states_the_empty_case_explicitly(self):
        from app.fusion.engine import SYSTEM
        lowered = SYSTEM.lower()
        self.assertIn('must be an empty array', lowered,
                      '必须写明 suggestions 为空时 direction_reviews 是空数组')
        self.assertIn('not rule_directions suggestions', lowered,
                      '必须说清能力层落点不是可审方向，否则模型会去审它')

    def test_the_system_prompt_disambiguates_the_two_meanings_of_independent(self):
        """真实模型第三轮卡在这里：A/B 候选去挂了并不存在的规则方向。

        根因是 `independent` 在这套契约里有两个互不相干的含义：
        orientation `C`（独立新场景）与 direction_id `independent`（不挂规则方向）。
        提示词没说清，模型就把 A/B 也去挂规则方向，然后被契约拒。
        契约本身是对的——测试夹具里三个候选本来就都用 direction_id='independent'。
        """
        from app.fusion.engine import SYSTEM
        lowered = SYSTEM.lower()
        self.assertIn('means two unrelated things', lowered,
                      '必须点明 independent 的两个含义互不相干')
        self.assertIn('a-oriented or b-oriented candidate may legitimately use', lowered,
                      '必须写明 A/B 候选可以用 direction_id=independent')

    def test_the_system_prompt_states_both_directions_of_the_gap_lock(self):
        """真实模型第四轮卡在这里：认领了价值类却列了 gaps_to_fill。

        契约的规则是**双向锁**：`needs_ai_fill` 必须列缺口，其他四类必须把缺口留空。
        而 SYSTEM 里 `outcome_class` 与 `gaps_to_fill` 这两个字段名**一次都没出现过**，
        只说「may list what must be filled in」——模型的误解完全有理由。
        """
        from app.fusion.engine import SYSTEM
        lowered = SYSTEM.lower()
        self.assertIn('must leave gaps_to_fill empty', lowered,
                      '必须写明价值类的 gaps_to_fill 必须留空（双向锁的反向）')
        self.assertIn('both directions are checked', lowered,
                      '必须点明这条锁是双向的')

    def test_the_contract_still_rejects_an_invented_direction_id(self):
        """提示词补充不等于放宽契约：编造的 direction ID 照样被拒。"""
        from app.fusion.contract import validate_result
        from app.fusion.test_fusion import proposed
        result = proposed(self.packet)
        result['direction_reviews'] = [{'direction_id': 'invented-direction',
                                        'decision': 'adopt', 'reason': 'r'}]
        with self.assertRaises(FusionError) as raised:
            validate_result(result, self.packet)
        self.assertIn('direction', str(raised.exception))


if __name__ == '__main__':
    unittest.main()
