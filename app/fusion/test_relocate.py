"""全文归位的守卫（机主 2026-10-01 授权后实施）。

授权内容：**引文内容真实存在时，允许跳出行号、按内容在全文里归位**。

这与「放宽校验」的区别在于它有条硬边界——**必须唯一命中**。
同一段文字在文件里出现多处时**不归位**，宁可让契约拒绝。
捏造的引文在全文里也找不到，照旧被拒。
"""
import copy
import unittest
from pathlib import Path

from app.fusion.evidence_reanchor import reanchor_evidence


class WholeFileRelocationTests(unittest.TestCase):
    """全部用真实采样里抓到的场景：内容真实存在，只是模型给的区间指错了。"""

    REAL = (
        '第 1 行标题\n'
        '第 2 行\n'
        '# 不安装也能跑（降级模式 + 如实告知）\n'
        '中间隔了几行别的内容\n'
        '又一行\n'
        '再一行\n'
        '**降级通道必须如实告知用户** —— 不能假装有保护。\n'
        '第 9 行\n'
    )

    def file_entry(self, text=None):
        return {'text': text if text is not None else self.REAL, 'sha256': 'a' * 64}

    def run_one(self, quote, start, end, text=None):
        result = {'x': [{'parent': 'A', 'path': 'SKILL.md',
                         'start_line': start, 'end_line': end, 'quote': quote}]}
        return reanchor_evidence(result, {('A', 'SKILL.md'): self.file_entry(text)})

    def test_real_content_found_outside_the_declared_range_is_relocated(self):
        """真实场景：内容在第 3 行，模型却说它在第 8 行。"""
        quote = '# 不安装也能跑（降级模式 + 如实告知）'
        result = {'x': [{'parent': 'A', 'path': 'SKILL.md', 'start_line': 7,
                         'end_line': 8, 'quote': quote}]}
        fixed, rejected = reanchor_evidence(
            result, {('A', 'SKILL.md'): self.file_entry()})
        self.assertEqual(len(rejected), 0, '区间指错时应当能按内容归位')
        self.assertEqual(fixed, 1, '归位计入修复')
        ref = result['x'][0]
        self.assertEqual(ref['start_line'], 3, '归位后必须真的落在那一句上')
        self.assertTrue(ref.get('line_range_relocated'), '行号被重新定位过，必须留痕')

    def test_a_quote_that_appears_twice_is_not_relocated(self):
        """**不唯一就不归位**——归到哪一处都是猜。"""
        twice = self.REAL + self.REAL.splitlines()[2] + '\n'
        fixed, rejected = self.run_one('# 不安装也能跑（降级模式 + 如实告知）', 20, 21, twice)
        self.assertEqual(fixed, 0, '多处出现时绝不归位')
        self.assertTrue(rejected, '不唯一就必须交回契约拒绝')

    def test_a_fabricated_quote_is_still_rejected_after_relocation_is_available(self):
        """**最关键的反例**：全文归位不能变成「找不到就放行」。"""
        fixed, rejected = self.run_one('这一整段在任何地方都不存在的凭空内容 invented', 1, 3)
        self.assertEqual(fixed, 0)
        self.assertTrue(rejected, '捏造的引文必须仍然被拒')

    def test_the_declared_range_still_wins_and_is_never_skipped(self):
        """区间内能修好时，不该去全文里乱找——原路径优先。"""
        real = '第 1 行标题\n第 2 行\n第 3 行\n第 4 行\n第 5 行\n'
        fixed, rejected = self.run_one('第 3 行', 3, 3, real)
        # 本来就准 → 不算「修复」，也不算「拒绝」
        self.assertEqual(len(rejected), 0)
        self.assertEqual(fixed, 0, '本来就准的引文不计入修复数')


class RelocationMarkDoesNotBreakTheSchemaTests(unittest.TestCase):
    """🔴 **2026-10-04 抓到的自伤：我们在拒绝自己的结果。**

    `reanchor_evidence` 是**就地修复**，给归位过的引文留下
    `line_range_relocated = True` 做留痕。而契约的 `no_extra_fields`
    禁止 evidence 出现那六个规定键之外的任何字段。于是：

        归位成功 → 留下记账键 → 契约拒「多出 line_range_relocated」
        → **每一次成功的归位，都保证这份结果被拒**

    三个模型（`glm-4.7` / `glm-5.3` / `MiniMax M3`）的真实采样里都出现过
    这条拒绝理由——**那不是模型多写了字段，是本产品自己加的。**

    修法是**摘掉记账键**，不是把它加进 schema：后者等于给 `no_extra_fields`
    开口子，模型多写一个字段也会被当成合法。
    **记账信息归记账，形状归形状。**
    """

    REAL = WholeFileRelocationTests.REAL
    QUOTE = '# 不安装也能跑（降级模式 + 如实告知）'

    def relocated(self):
        result = {'profiles': [{'parent': 'A', 'capabilities': [
            {'evidence': [{'parent': 'A', 'path': 'SKILL.md', 'start_line': 7,
                           'end_line': 8, 'quote': self.QUOTE}]}]}]}
        reanchor_evidence(result, {('A', 'SKILL.md'): {
            'text': self.REAL, 'sha256': 'a' * 64}})
        return result

    def refs(self, result):
        from app.fusion.evidence_reanchor import _walk_refs
        return list(_walk_refs(result))

    def test_the_mark_is_still_left_by_reanchor_itself(self):
        """**留痕功能没有被删**——摘键发生在送进契约之前，不是把留痕删了。"""
        result = self.relocated()
        self.assertTrue([r for r in self.refs(result) if r.get('line_range_relocated')],
                        '归位过的引文必须留痕（上面的老守卫守的也是这条）')

    def test_stripping_the_mark_removes_only_that_key(self):
        from app.fusion.evidence_reanchor import strip_relocation_marks
        result = self.relocated()
        before = copy.deepcopy(result)
        removed = strip_relocation_marks(result)
        self.assertEqual(removed, 1, '这个夹具应当恰好归位过一处')
        for ref in self.refs(result):
            self.assertNotIn('line_range_relocated', ref,
                             '记账键还留在引文上——契约会因它拒绝这份结果')
            self.assertEqual(ref['start_line'], before['profiles'][0]['capabilities'][0]
                             ['evidence'][0]['start_line'], '摘键不该动到归位结果本身')

    def test_a_relocated_result_matches_the_strict_evidence_shape(self):
        """**这条才是判据**：归位并摘键之后，evidence 必须恰好是契约要求的字段集。"""
        from app.fusion.contract import EVIDENCE
        from app.fusion.evidence_reanchor import strip_relocation_marks
        required = set(EVIDENCE['properties'])
        self.assertEqual(required, {'parent', 'path', 'sha256',
                                    'start_line', 'end_line', 'quote'},
                         '契约的 evidence 字段集变了——本测试的判据要跟着重核')
        result = self.relocated()
        strip_relocation_marks(result)
        for ref in self.refs(result):
            self.assertEqual(set(ref), required)


class ReanchorWiringTests(unittest.TestCase):
    """**上一条守卫的补充：它只测了函数本身，没测管线有没有用。**

    2026-10-04 反向验证时，把 `engine._reanchor` 里那句
    `strip_relocation_marks(parsed)` 去掉，**整条 `test_relocate.py` 仍然全绿**——
    因为守卫是**直接调用函数**的，管线断了它看不见。

    **门禁必须执行被检查的东西。** 所以这里走真实的 `engine._reanchor`：
    用真包、造一个需要归位的引文，然后断言**送进契约的对象上那个键已经不在了**。
    """

    def test_the_engine_path_actually_strips_the_mark(self):
        from app.fusion.engine import _reanchor, prepare

        a, b = (Path(p) for p in _example_packages())
        packet = prepare(a, b, goal='')
        text = next(f for f in packet['packages']['A']['files']
                    if f['path'] == 'SKILL.md')['text']
        line = next(ln for ln in text.splitlines()[2:] if len(ln.strip()) > 12)
        quote = line.strip()
        # 行号故意指错 → 触发「按内容在全文里归位」这条路
        parsed = {'profiles': [{'parent': 'A', 'capabilities': [
            {'claim': '', 'basis': 'observed', 'evidence': [
                {'parent': 'A', 'path': 'SKILL.md', 'start_line': 1,
                 'end_line': 2, 'sha256': '', 'quote': quote}]}]}]}

        report = _reanchor(parsed, packet)
        self.assertTrue(report and report.get('applied'))
        self.assertEqual(report.get('fixed'), 1,
                         '这个夹具应当恰好归位一处——否则本条守卫在测别的事')
        # **判据在「送进契约的对象」上，不是函数返回值上。**
        for ref in self.refs_of(parsed):
            self.assertNotIn('line_range_relocated', ref,
                             'engine._reanchor 没有摘掉记账键——'
                             '契约会因 no_extra_fields 拒绝这份结果')

    def test_the_relocation_is_still_counted_in_the_report(self):
        """摘键不许把留痕信息弄丢——归位了几处必须仍然说得出来。"""
        from app.fusion.engine import _reanchor, prepare

        a, b = (Path(p) for p in _example_packages())
        packet = prepare(a, b, goal='')
        text = next(f for f in packet['packages']['A']['files']
                    if f['path'] == 'SKILL.md')['text']
        quote = next(ln for ln in text.splitlines()[2:] if len(ln.strip()) > 12).strip()
        parsed = {'profiles': [{'parent': 'A', 'capabilities': [
            {'claim': '', 'basis': 'observed', 'evidence': [
                {'parent': 'A', 'path': 'SKILL.md', 'start_line': 1,
                 'end_line': 2, 'sha256': '', 'quote': quote}]}]}]}
        report = _reanchor(parsed, packet)
        self.assertEqual(report.get('relocation_marks_removed'), 1,
                         '摘掉的记账键数量必须留在报告里——「静默丢弃」不可接受')

    def refs_of(self, result):
        from app.fusion.evidence_reanchor import _walk_refs
        return list(_walk_refs(result))


def _example_packages():
    """返回一对**真实**示例包。少一个就直接炸——**不许 skip**。

    跳过比红更危险：本文件的两条接线守卫曾经因为路径层级算错而被静默跳过，
    看起来是绿的，其实什么都没验。
    """
    root = Path(__file__).resolve().parents[2]
    for folder in ('skillhub-skill', 'app/examples'):
        first = None
        for name in ('ast-static-scan-arch', 'device-key-attestation'):
            candidate = root / folder / name
            if not (candidate / 'SKILL.md').exists():
                continue
            if first is None:
                first = candidate
            else:
                return str(first), str(candidate)
    raise AssertionError('找不到成对的示例包（根目录 %s）' % root)


if __name__ == '__main__':
    unittest.main()
