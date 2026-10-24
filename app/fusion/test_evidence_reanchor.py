"""evidence_reanchor 单测：转写误差修复、子区间收紧、拒绝路径、指纹覆盖。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]).replace('/app', ''))

from app.fusion.evidence_reanchor import reanchor_evidence


def make_files():
    text_a = '\n'.join(['# T'] + [f'A line {i} with **bold** and ```py' for i in range(1, 30)])
    text_b = '\n'.join([f'B{i} content here' for i in range(1, 40)])
    mk = lambda t: {'sha256': 'sha256:' + str(abs(hash(t))), 'text': t}
    return {('A', 'SKILL.md'): mk(text_a), ('B', 'RUN.md'): mk(text_b)}


class ReanchorTests(unittest.TestCase):
    def test_exact_quote_untouched(self):
        files = make_files()
        lines = files[('A', 'SKILL.md')]['text'].splitlines()
        quote = lines[4]
        ref = {'parent': 'A', 'path': 'SKILL.md', 'start_line': 5, 'end_line': 5,
               'quote': quote, 'sha256': 'wrong'}
        fixed, rej = reanchor_evidence({'x': [ref]}, files)
        self.assertEqual((fixed, rej), (0, []))
        self.assertEqual(ref['sha256'], files[('A', 'SKILL.md')]['sha256'],
                         '指纹以真实文件覆盖')

    def test_transcription_error_fixed_in_range(self):
        files = make_files()
        lines = files[('A', 'SKILL.md')]['text'].splitlines()
        real = lines[9]                                    # 含 ** 与 ```py
        mangled = real.replace('**', '').replace('```py', 'py')
        ref = {'parent': 'A', 'path': 'SKILL.md', 'start_line': 10, 'end_line': 10,
               'quote': mangled, 'sha256': 'x'}
        fixed, rej = reanchor_evidence({'x': [ref]}, files)
        self.assertEqual(fixed, 1)
        self.assertEqual(ref['quote'], real, 'quote 被真实文本覆盖')
        self.assertEqual(ref['sha256'], files[('A', 'SKILL.md')]['sha256'])

    def test_range_tightened_to_best_subinterval(self):
        files = make_files()
        text = files[('B', 'RUN.md')]['text']
        seg = '\n'.join(text.splitlines()[5:20])           # 真实来源 B6-B20
        mangled = seg.replace('content', 'c0ntent')        # 轻微转写误差
        ref = {'parent': 'B', 'path': 'RUN.md', 'start_line': 1, 'end_line': 39,
               'quote': mangled, 'sha256': 'x'}
        fixed, rej = reanchor_evidence({'x': [ref]}, files)
        self.assertEqual(fixed, 1)
        self.assertEqual((ref['start_line'], ref['end_line']), (6, 20),
                         '区间收紧到真实子区间')
        self.assertEqual(ref['quote'], seg)

    def test_structural_distortion_rejected(self):
        files = make_files()
        ref = {'parent': 'A', 'path': 'SKILL.md', 'start_line': 1, 'end_line': 10,
               'quote': '完全捏造的内容\n混入了不存在的话', 'sha256': 'x'}
        fixed, rej = reanchor_evidence({'x': [ref]}, files)
        self.assertEqual(fixed, 0)
        self.assertTrue(rej, '结构失真被拒绝并记录')

    def test_invalid_range_reported(self):
        files = make_files()
        ref = {'parent': 'A', 'path': 'SKILL.md', 'start_line': 100, 'end_line': 200,
               'quote': 'x', 'sha256': 'x'}
        fixed, rej = reanchor_evidence({'x': [ref]}, files)
        self.assertEqual(fixed, 0)
        self.assertEqual(rej[0][2], 'range_invalid')

    def test_unknown_file_skipped(self):
        files = make_files()
        ref = {'parent': 'Z', 'path': 'GONE.md', 'start_line': 1, 'end_line': 1,
               'quote': 'x', 'sha256': 'x'}
        fixed, rej = reanchor_evidence({'x': [ref]}, files)
        self.assertEqual((fixed, rej), (0, []), '文件级错误留给契约处理')


class ThresholdIsNotSilentlyTunableTests(unittest.TestCase):
    """0.90 这个判据**从未被标定**，现在它是真实卡点——但也不许被悄悄改掉。

    2026-10-01 真实 `glm-4.7` 采样 6 次 2 次通过，失败样本里修不动的引文相似度
    落在 0.639–0.84。这个区间提示「阈值可能偏高」，但**同一个区间也可能是
    凭空编造的证据**——两者的正确处理完全相反。

    所以这里锁两件事：
      1. 默认值 0.90 不得被静默改动（改了必须同时更新标定记录）；
      2. 阈值是**参数**，允许按材料/实验标定，但不允许藏在函数体里写死。
    """

    def test_the_default_threshold_is_explicit_and_currently_uncalibrated(self):
        import inspect
        from app.fusion.evidence_reanchor import reanchor_evidence
        signature = inspect.signature(reanchor_evidence)
        self.assertIn('min_similarity', signature.parameters,
                      '相似度判据必须是显式参数，不能写死在函数体里')
        self.assertEqual(signature.parameters['min_similarity'].default, 0.90,
                         '默认判据被改动——必须先更新 evaluations/e2-glm47-audit.json 的标定记录')

    def test_rejected_reasons_carry_the_quote_so_the_verdict_can_be_judged(self):
        """只有相似度分不出「抄错几个字」和「凭空编造」，必须带上引文摘要。"""
        from app.fusion.evidence_reanchor import reanchor_evidence
        f = {'text': '真实的一行内容在这里\n第二行也有内容\n', 'sha256': 'a' * 64}
        _, rejected = reanchor_evidence(
            {'x': [{'parent': 'A', 'path': 'SKILL.md', 'start_line': 1, 'end_line': 2,
                    'quote': '完全无关的凭空内容 invented text'}]},
            {('A', 'SKILL.md'): f})
        self.assertTrue(rejected, '明显编造的引文必须被拒')
        self.assertIn('avg=', rejected[0][2])
        self.assertIn('quote=', rejected[0][2],
                      '拒绝原因必须带引文摘要，否则事后无法判断是转写还是捏造')
        self.assertIn('invented text', rejected[0][2])


class RealTranscriptionNoiseTests(unittest.TestCase):
    """用**真实采样里抓到的原句**验证去噪有效——不是自己编一个好看的夹具。

    2026-10-01 真实 `glm-4.7` 采样 6 次里，失败样本的修不动引文逐条打开后
    **全部指向真实内容**，且只有两种模式：
      ① 丢了 ```` ```python ```` 的围栏，只剩裸的 `python`
      ② 用 `...` 把中间行省略掉
    （`evaluations/e2-glm47-audit.json` 有逐条留档）

    相似度落在 0.469–0.683。**修法不是降判据**（降判据会把真捏造一起放进来），
    而是定向剥掉这两种排版噪声，剩下内容仍按原判据比对。
    """

    REAL_B = (
        '### 3) 服务端校验\n'
        '```python\n'
        'def verify_proof(pub_hex: str, proof_str: str, method: str, url: str,\n'
        '                 cache: ReplacedNonce) -> bool:\n'
    )
    # 模型抄出来的：围栏没了，中间两行被 `...` 省略
    MODEL_B = (
        '### 3) 服务端校验\n'
        'python\n'
        'def verify_proof(pub_hex: str, proof_str: str, method: str, url: str,\n'
        '...\n'
    )

    # A 那条**不是**纯排版噪声：模型把真实存在的 `def visit_Call(self, node):` 和
    # 一条**文件里根本没有的注释** `# open(...) / Path(...)` 拼在了一起
    # （真实文件写的是 `# 收集 read() / open() / Path() 这类调用`）。
    # 它是**部分捏造**，必须继续被拒——这正好界定了去噪的边界。
    REAL_A = (
        'class Extract(ast.NodeVisitor):\n'
        '    def run(self, tree):\n'
        '        self.visit_Call(tree)\n'
        '    # 收集 read() / open() / Path() 这类调用\n'
    )
    MODEL_A_COMPOSITE = (
        'class Extract(ast.NodeVisitor):\n'
        '    ...\n'
        '    def visit_Call(self, node):\n'
        '        # open(...) / Path(...)\n'
    )

    def file_entry(self, text):
        return {'text': text, 'sha256': 'a' * 64}

    def run_one(self, real, model):
        from app.fusion.evidence_reanchor import reanchor_evidence
        files = {('A', 'SKILL.md'): self.file_entry(real)}
        result = {'x': [{'parent': 'A', 'path': 'SKILL.md', 'start_line': 1,
                        'end_line': len(real.splitlines()), 'quote': model}]}
        return reanchor_evidence(result, files)

    def test_a_lost_code_fence_is_transcription_noise_and_gets_repaired(self):
        """真实采样里的 B 例：只丢了 ```` ```python ```` 的围栏，其余逐字一致。"""
        fixed, rejected = self.run_one(self.REAL_B, self.MODEL_B)
        self.assertEqual(len(rejected), 0, '围栏噪声应被剥掉并修复')
        self.assertEqual(fixed, 1, '应被修复并计数')

    def test_a_composite_of_real_and_invented_lines_is_still_rejected(self):
        """真实采样里的 A 例：省略号 + **拼了一条文件里没有的注释**。

        去噪只处理排版噪声，**不会**把「真行 + 假注释」这种部分捏造放进来——
        这条界线正是本轮要守住的。
        """
        fixed, rejected = self.run_one(self.REAL_A, self.MODEL_A_COMPOSITE)
        self.assertEqual(fixed, 0, '部分捏造不得被接受')
        self.assertTrue(rejected, '部分捏造必须继续被拒')

    def test_denoising_does_not_make_fabrication_pass(self):
        """**最关键的反例**：去噪只处理已知排版噪声，绝不能顺带把捏造放进来。"""
        from app.fusion.evidence_reanchor import reanchor_evidence
        fabricated = '```python\n...\npython\n这一整段在任何文件里都不存在 invented line\n'
        files = {('A', 'SKILL.md'): self.file_entry(self.REAL_B)}
        result = {'x': [{'parent': 'A', 'path': 'SKILL.md', 'start_line': 1,
                        'end_line': len(self.REAL_B.splitlines()), 'quote': fabricated}]}
        fixed, rejected = reanchor_evidence(result, files)
        self.assertEqual(fixed, 0, '编造的内容不得因为「像排版噪声」而被接受')
        self.assertTrue(rejected, '编造必须仍被拒绝')

    def test_a_bare_language_word_in_real_source_is_not_silently_dropped(self):
        """围栏残留的剥离是有边界的：真实正文里的一整行 `text` / `json` 之类
        不该被当成噪声丢掉——那会让比对悄悄变松。"""
        from app.fusion.evidence_reanchor import _norm
        self.assertEqual(_norm('json\n真实的一行'), '真实的一行')
        self.assertEqual(_norm('这是关于 JSON 的说明'), '这是关于 JSON 的说明')


if __name__ == '__main__':
    unittest.main(verbosity=2)
