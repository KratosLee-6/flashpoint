"""补足闭环的回归：核心是「缺口必须被真正处理，且不得靠自说自话变成可用」。

M2 的教训：模型说「我融合出了新东西」没有证据力；同理，模型说「已补足」同样没有。
所以补完后必须由同一个核对器复评，且 `verified` 永远为 False。
"""
import tempfile
import unittest
from pathlib import Path

from app.fusion.contract import FusionError
from app.fusion.engine import prepare
from app.fusion.fill import FILL_SCHEMA, build_messages, validate

LAW = '# 合同审查要点\n1. 识别关键条款\n2. 出具风险清单\n必须逐条标注依据条款。\n'
REPAIR = '# 汽车故障诊断\n1. 确认异响位置\n2. 按序验证并排除\n如果验证不通过，回到第 2 步。\n'


def candidate(**over):
    base = {
        'orientation': 'C',
        'disposition': 'blocked',
        'title': '风险清单生成器',
        'expected_outcome': {'outcome_class': 'needs_ai_fill', 'basis': '缺依据条款的引用规则'},
        'gaps_to_fill': ['引用规则未定义'],
        'blockers': ['引用规则未定义'],
        'new_mechanism': '把症状证据整理成可回归的检查序列',
        'problem': '同类问题反复出现却无人留下可复用经验',
        'contribution_a': {'mechanism': '逐条标注依据', 'evidence': []},
        'contribution_b': {'mechanism': '每轮复检并记录', 'evidence': []},
        'workflow': ['收集证据', '标注依据', '复检'],
        'artifacts': [{'path': 'scripts/check.py', 'purpose': '标注依据'}],
        'ablation_checks': [{'removed_parent': 'A', 'task': 't', 'expected_loss': 'l'},
                            {'removed_parent': 'B', 'task': 't', 'expected_loss': 'l'}],
        'evaluation': [{'task': 't', 'baselines': ['A_only', 'B_only', 'concatenation', 'fusion', 'model_only'],
                        'success_criterion': 's'}],
    }
    base.update(over)
    return base


def fill(sha='a' * 64, **over):
    base = {
        'target_title': '风险清单生成器',
        'addressed': [{'gap': '引用规则未定义',
                       'resolution': '沿用贡献方的逐条标注依据做法',
                       'evidence': [ref('A', sha=sha)]}],
        'still_missing': [],
        'disposition': 'usable',
        'revised': {'new_mechanism': '为每条结论生成可回查的引用指针，并记录复检结果',
                    'workflow': ['收集证据', '生成引用指针', '复检'],
                    'artifacts': [{'path': 'scripts/ref.py', 'purpose': '生成引用指针'}],
                    'retention': {'keep': ['逐条标注依据的做法'], 'discard': ['原域的题库'], 'reason': '全量合并会稀释'}},
    }
    base.update(over)
    return base


def ref(parent, line=1, sha='a' * 64):
    return {'parent': parent, 'path': 'SKILL.md', 'sha256': sha,
            'start_line': line, 'end_line': line, 'quote': 'x'}


class FillLoopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.a, self.b = root/'a', root/'b'
        for path, text in ((self.a, LAW), (self.b, REPAIR)):
            path.mkdir()
            (path/'SKILL.md').write_text(text, encoding='utf-8', newline='')
        self.packet = prepare(self.a, self.b, '把合同审查方法用到汽车维修')
        real = next(f for f in self.packet['packages']['A']['files'] if f['path'] == 'SKILL.md')
        self.sha = real['sha256']

    # —— 提示词 ——
    def test_second_round_reuses_the_frozen_snapshot_and_names_every_gap(self):
        messages_ = build_messages(self.packet, candidate(), ['引用规则未定义', '缺少样例'])
        self.assertIn('fill_declared_gaps', messages_[1]['content'])
        self.assertIn('引用规则未定义', messages_[1]['content'])
        self.assertIn('缺少样例', messages_[1]['content'])
        self.assertIn('1. 识别关键条款', messages_[1]['content'], '第一轮的冻结材料必须原样带上')
        self.assertIn('not_recommended', messages_[0]['content'], '如实回答「不建议」必须是被允许的出路')

    def test_filling_a_candidate_without_gaps_is_refused(self):
        with self.assertRaises(FusionError):
            build_messages(self.packet, candidate(gaps_to_fill=[]), [])

    # —— 校验 ——
    def test_a_clean_fill_is_accepted_but_never_verified(self):
        report = validate(fill(sha=self.sha), candidate(), self.packet)
        self.assertTrue(report['usable_claimed'])
        self.assertFalse(report['verified'])
        self.assertIn('对照实验', report['reason_unverified'])
        self.assertEqual(report['reassessment']['claimed_class'], 'beats_both')

    def test_every_declared_gap_must_be_addressed_exactly_once(self):
        with self.assertRaises(FusionError):
            validate(fill(addressed=[]), candidate(), self.packet)
        with self.assertRaises(FusionError):
            validate(fill(addressed=[{'gap': '别的缺口', 'resolution': 'r', 'evidence': [ref('A')]}]),
                     candidate(), self.packet)
        dup = [fill()['addressed'][0], fill()['addressed'][0]]
        with self.assertRaises(FusionError):
            validate(fill(addressed=dup), candidate(), self.packet)

    def test_claiming_usable_with_gaps_left_is_rejected(self):
        with self.assertRaises(FusionError):
            validate(fill(still_missing=['引用规则未定义']), candidate(), self.packet)

    def test_unresolved_gap_must_come_from_the_declared_list(self):
        with self.assertRaises(FusionError):
            validate(fill(still_missing=['凭空冒出的缺口']), candidate(), self.packet)

    def test_a_honest_partial_fill_keeps_the_candidate_in_the_fifth_class(self):
        report = validate(fill(sha=self.sha, disposition='still_blocked', still_missing=['引用规则未定义']), candidate(), self.packet)
        self.assertFalse(report['usable_claimed'])
        self.assertEqual(report['reassessment']['claimed_class'], 'needs_ai_fill')

    def test_filling_the_wrong_candidate_is_refused(self):
        with self.assertRaises(FusionError):
            validate(fill(target_title='别的方案'), candidate(), self.packet)

    def test_the_fill_schema_requires_every_field(self):
        with self.assertRaises(FusionError):
            validate({k: v for k, v in fill().items() if k != 'still_missing'}, candidate(), self.packet)
        self.assertIn('still_missing', FILL_SCHEMA['required'])


if __name__ == '__main__':
    unittest.main()
