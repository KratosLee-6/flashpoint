"""契约校验的诊断质量（2026-09-30 真实小模型实测后补）。

模型返回的 JSON 与约定不符时，错误必须说清「缺什么 / 多什么 / 约定是什么」，
否则用户在界面上分不清「连不上模型」和「方案不合格」——这两件事的处理完全不同。
"""
import unittest
from pathlib import Path

from app.fusion.contract import FusionError, RESULT_SCHEMA, check_shape


def ref(parent='A', line=1):
    return {'parent': parent, 'path': 'SKILL.md', 'sha256': 'a' * 64,
            'start_line': line, 'end_line': line, 'quote': 'x'}


def base_result():
    profile = lambda parent: {
        'parent': parent,
        'capabilities': [{'claim': 'c', 'basis': 'observed', 'evidence': [ref(parent)]}],
        'retention': {'keep': ['k'], 'discard': ['d'], 'reason': 'r'},
        'dependencies': [], 'missing': [], 'coverage': 'partial'}
    return {
        'profiles': [profile('A'), profile('B')],  # 契约要求 A/B 各一份
        'candidates': [],
        'direction_reviews': [],
    }


class ContractDiagnosticsTests(unittest.TestCase):
    def test_top_level_mismatch_names_what_is_missing(self):
        with self.assertRaises(FusionError) as raised:
            check_shape({'profiles': [], 'candidates': []}, RESULT_SCHEMA)
        message = str(raised.exception)
        self.assertIn('direction_reviews', message, '必须指出缺了哪个字段')
        self.assertIn('缺少', message)
        self.assertIn('约定必填', message, '必须把约定列出来，模型/用户才知道该怎么改')

    def test_nested_location_is_reported(self):
        result = base_result()
        del result['profiles'][0]['retention']
        with self.assertRaises(FusionError) as raised:
            check_shape(result, RESULT_SCHEMA)
        self.assertIn('retention', str(raised.exception))

    def test_unknown_extra_fields_are_named_not_silently_dropped(self):
        result = base_result()
        result['direction_reviews'] = []
        result['extra_thing'] = 1
        with self.assertRaises(FusionError) as raised:
            check_shape(result, RESULT_SCHEMA)
        self.assertIn('extra_thing', str(raised.exception))

    def test_type_mismatch_still_says_where(self):
        with self.assertRaises(FusionError) as raised:
            check_shape({'profiles': 'not-a-list', 'candidates': [], 'direction_reviews': []}, RESULT_SCHEMA)
        self.assertIn('result', str(raised.exception))


class OutputBudgetTests(unittest.TestCase):
    """产出预算守卫：schema 变胖时，模型会先被长度上限截断而不是被契约拒绝。

    2026-09-30 真实本机模型跑 `run` 时 `finish_reason` 不是 stop。当时有两种可能：
    模型弱，或者「最小合规结果」本身就装不下。后者会让任何本机模型都拿不到方案，
    属于产品缺陷，必须在 CI 里被挡住。实测：最小合规结果 ≈ 4,039 token（最激进口径），
    上限 8192，余量充足。
    """
    WORST_CHARS_PER_TOKEN = 1.5   # 偏保守的估计，宁可高估所需 token
    RUN_LIMIT = 8192
    FILL_LIMIT = 4096

    def minimal_run_result(self):
        import json
        import tempfile
        from app.fusion.engine import prepare
        from app.fusion.test_capability import LAW, REPAIR
        from app.fusion.test_fusion import proposed

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a, b = root / 'a', root / 'b'
            a.mkdir()
            b.mkdir()
            (a / 'SKILL.md').write_text(LAW, encoding='utf-8', newline='')
            (b / 'SKILL.md').write_text(REPAIR, encoding='utf-8', newline='')
            packet = prepare(a, b, '把合同审查的收敛方法用到汽车维修上')
            return json.dumps(proposed(packet), ensure_ascii=False, separators=(',', ':'))

    def test_a_minimal_compliant_result_fits_in_the_run_budget(self):
        chars = len(self.minimal_run_result())
        worst = chars / self.WORST_CHARS_PER_TOKEN
        self.assertLessEqual(
            worst, self.RUN_LIMIT,
            f'最小合规结果约 {worst:,.0f} token，超过 run 的 max_tokens={self.RUN_LIMIT}；'
            f'任何本机模型都会被截断，请提高上限或精简 schema')

    def test_a_fill_answer_is_far_inside_its_budget(self):
        import json
        payload = json.dumps({
            'target_title': '风险清单生成器',
            'addressed': [{'gap': g, 'resolution': '沿用贡献方已验证的做法',
                           'evidence': [ref('A')]} for g in ('引用规则未定义', '缺少目标域样例')],
            'still_missing': [],
            'disposition': 'usable',
            'revised': {'new_mechanism': '为每条结论生成可回查的引用指针，并记录复检结果',
                        'workflow': ['收集证据', '生成引用指针', '复检'],
                        'artifacts': [{'path': 'scripts/ref.py', 'purpose': '生成引用指针'}],
                        'retention': {'keep': ['逐条标注依据的做法'], 'discard': ['原域题库'],
                                      'reason': '全量合并会稀释双方'}},
        }, ensure_ascii=False, separators=(',', ':'))
        self.assertLessEqual(len(payload) / self.WORST_CHARS_PER_TOKEN, self.FILL_LIMIT)


if __name__ == '__main__':
    unittest.main()
