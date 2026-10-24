"""火花评估的回归。

它存在的理由是一条实测教训：M2 实验里融合臂平均分**没有**胜过单臂
（`fusion_without_A=6.1 > fusion=5.8`）。所以「我融合出了新东西」这句话本身没有证据力，
这一层只做**核对**，不做背书——`verified` 必须恒为 False。
"""
import unittest

from app.fusion.spark import CLASSES, assess, assess_candidate


def candidate(**over):
    base = {
        'orientation': 'C',
        'disposition': 'hypothesis',
        'title': '候选方案',
        'expected_outcome': {'outcome_class': 'beats_both', 'basis': '两侧机制叠加后省掉一半步骤'},
        'gaps_to_fill': [],
        'new_mechanism': '按证据链回溯，把症状与假设的可追溯记录自动生成',
        'problem': '同类故障反复出现却无人留下可复用的经验',
        'contribution_a': {'mechanism': '列出所有可能的原因', 'evidence': []},
        'contribution_b': {'mechanism': '逐条复检并留下记录', 'evidence': []},
        'workflow': ['收集现象', '建立假设', '验证并记录'],
        'ablation_checks': [{'removed_parent': 'A', 'task': 't', 'expected_loss': 'l'},
                            {'removed_parent': 'B', 'task': 't', 'expected_loss': 'l'}],
        'evaluation': [{'task': 't', 'baselines': ['A_only', 'B_only', 'concatenation', 'fusion', 'model_only'],
                        'success_criterion': 's'}],
    }
    base.update(over)
    return base


class SparkTests(unittest.TestCase):
    def codes(self, c):
        return {r['code'] for r in assess_candidate(c, {})['risks']}

    def test_a_clean_candidate_carries_no_risk(self):
        self.assertEqual(self.codes(candidate()), set())

    def test_never_claims_verified(self):
        result = assess({'profiles': [{'parent': 'A'}, {'parent': 'B'}], 'candidates': [candidate()]})
        self.assertFalse(result['verified'])
        self.assertIn('对照实验', result['reason_unverified'])

    def test_claimed_class_is_reported_as_a_claim_not_a_conclusion(self):
        result = assess({'profiles': [{'parent': 'A'}, {'parent': 'B'}], 'candidates': [candidate()]})
        self.assertEqual(result['claimed_classes'], ['beats_both'])
        self.assertEqual(result['claimed_class_labels'], [CLASSES['beats_both']])
        self.assertIn('不是结论', result['meaning'])

    def test_a_mechanism_that_restates_a_parent_contribution_is_flagged(self):
        # 几乎照抄 A 的贡献、只多两个字——这正是「全量合并」的典型形态
        c = candidate(new_mechanism='列出所有可能的原因，记录')
        self.assertIn('new_mechanism_near_a', self.codes(c))

    def test_a_genuinely_new_mechanism_is_not_flagged_as_a_merger(self):
        self.assertNotIn('new_mechanism_near_a', self.codes(candidate()))
        self.assertNotIn('new_mechanism_near_b', self.codes(candidate()))

    def test_missing_baselines_are_flagged(self):
        c = candidate(evaluation=[{'task': 't', 'baselines': ['A_only', 'fusion'], 'success_criterion': 's'}])
        self.assertIn('missing_baselines', self.codes(c))

    def test_claiming_value_while_listing_gaps_is_flagged_as_contradictory(self):
        c = candidate(gaps_to_fill=['缺少题库'])
        self.assertIn('claims_value_with_gaps', self.codes(c))

    def test_the_fifth_class_is_a_work_item_not_a_failure(self):
        c = candidate(disposition='blocked', expected_outcome={'outcome_class': 'needs_ai_fill', 'basis': '缺题库'},
                      gaps_to_fill=['题库'], blockers=['缺题库'])
        self.assertEqual(self.codes(c), set())
        result = assess({'profiles': [{'parent': 'A'}, {'parent': 'B'}], 'candidates': [c]})
        self.assertEqual(result['needs_ai_fill'], ['候选方案'])

    def test_claiming_the_fifth_class_without_gaps_is_flagged(self):
        c = candidate(expected_outcome={'outcome_class': 'needs_ai_fill', 'basis': '缺题库'})
        self.assertIn('fill_class_without_gaps', self.codes(c))


class CapabilityLinkageTests(unittest.TestCase):
    """「借了做法」这个声明此前没人核对：`capability` 抽结构轴，
    `contribution.kind` 判借什么，两套字段各说各话（39 号文 §7.8）。"""

    CAPABILITY = {
        'A': {'items': {'steps': [{'evidence': {'path': 'SKILL.md', 'start_line': 3}}],
                        'checks': [{'evidence': {'path': 'SKILL.md', 'start_line': 9}}]}},
        'B': {'items': {'steps': [{'evidence': {'path': 'SKILL.md', 'start_line': 4}}]}},
        'bridge': {'shared_axes': ['steps']},
    }

    def ev(self, line):
        return [{'parent': 'A', 'path': 'SKILL.md', 'start_line': line}]

    def borrowing(self, kind, line):
        return candidate(
            contribution_a={'kind': kind, 'mechanism': '逐步推进', 'evidence': self.ev(line)},
            contribution_b={'kind': kind, 'mechanism': '逐步推进', 'evidence': self.ev(line)})

    def linkage_codes(self, c, capability):
        return {r['code'] for r in assess({'profiles': [{'parent': 'A'}, {'parent': 'B'}],
                                           'candidates': [c]}, capability)['candidates'][0]['risks']}

    def test_borrowed_method_pointing_at_a_fingerprinted_line_is_accepted(self):
        read = assess({'profiles': [{'parent': 'A'}, {'parent': 'B'}],
                       'candidates': [self.borrowing('way_of_working', 3)]},
                      self.CAPABILITY)['candidates'][0]['borrowed']
        self.assertEqual(read['A']['attested_axes'], ['steps'])
        self.assertEqual(read['A']['shared_axes'], ['steps'], 'A、B 共同的结构轴应被标出来')
        self.assertEqual(read['A']['unattested_evidence'], 0)
        self.assertNotIn('way_of_working_evidence_unattested_a', self.linkage_codes(
            self.borrowing('way_of_working', 3), self.CAPABILITY))

    def test_borrowed_method_pointing_at_a_bare_line_is_flagged_with_its_blind_spots(self):
        c = self.borrowing('way_of_working', 42)
        self.assertIn('way_of_working_evidence_unattested_a', self.linkage_codes(c, self.CAPABILITY))
        detail = next(r['detail'] for r in assess(
            {'profiles': [{'parent': 'A'}, {'parent': 'B'}], 'candidates': [c]},
            self.CAPABILITY)['candidates'][0]['risks']
            if r['code'] == 'way_of_working_evidence_unattested_a')
        self.assertIn('每轴最多留 6 条', detail,
                      '没命中不等于说谎——能力层有盲区，风险提示必须把盲区说出来')

    def test_borrowing_a_capability_is_not_judged_by_the_structure_fingerprint(self):
        # 能力（领域职能）本来就不该用结构轴证明；拿结构层去质疑它是误判。
        c = self.borrowing('capability', 42)
        self.assertNotIn('way_of_working_evidence_unattested_a', self.linkage_codes(c, self.CAPABILITY))

    def test_without_a_capability_layer_the_check_is_skipped_and_says_so(self):
        result = assess({'profiles': [{'parent': 'A'}, {'parent': 'B'}],
                         'candidates': [self.borrowing('way_of_working', 42)]})
        self.assertFalse(result['capability_linkage']['checked'])
        self.assertIn('未做借用类型核对', result['capability_linkage']['caveat'])
        self.assertEqual(result['candidates'][0]['borrowed'], {}, '没核对就不产出读数')

    def test_a_prepared_packet_wires_its_own_capability_layer_in(self):
        """端到端接线：引擎必须把能力层传下去，否则这段核对在真跑时等于没开。"""
        import tempfile
        from pathlib import Path
        from app.fusion.engine import prepare, synthesize
        from app.fusion.test_capability import LAW, REPAIR
        from app.fusion.test_fusion import proposed, provider

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a, b = root / 'a', root / 'b'
            a.mkdir()
            b.mkdir()
            (a / 'SKILL.md').write_text(LAW, encoding='utf-8', newline='')
            (b / 'SKILL.md').write_text(REPAIR, encoding='utf-8', newline='')
            packet = prepare(a, b, '把合同审查的收敛方法用到汽车维修上')
        with provider(proposed(packet)) as (endpoint, _):
            result = synthesize(packet, endpoint=endpoint, model='test-only', api_key='test-secret')
        linkage = result['spark']['capability_linkage']
        self.assertTrue(linkage['checked'], '引擎没有把能力层交给火花核对器')
        self.assertTrue(linkage['shared_axes'], '这两个合成包在结构上应有共同轴')


if __name__ == '__main__':
    unittest.main()
