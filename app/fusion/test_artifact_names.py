"""产出文件名扩展名推断的守卫（机主 2026-10-01 授权后实施）。

**这是推断，不是归位。** 归位的前提是「真值存在于某处」；这里模型没写扩展名，
所以必须守住三条：只从白名单关键词推断、推断必须留痕、推断不出来就保持原样。

白名单的每一条都来自**真实采样的原始输出**——2026-10-01 的循环探针一次就抓到
同一次调用里的 7 个产物：4 个写对、3 个写断。**判据必须从这个对照里读出来。**
"""
import unittest

from app.fusion.artifact_names import infer_artifacts, infer_extension


class InferenceIsNarrowTests(unittest.TestCase):
    def test_real_sampled_names_infer_from_real_sampled_purposes(self):
        cases = [
            ('audit_report_schema.', '双轨证明审查报告的JSON Schema定义', '.json'),
            ('soft_evidence_prover.', '通用双轨证明审查框架核心逻辑（不绑定具体领域）', '.py'),
            ('graded_degradation_report.', '通用审查报告', '.md'),
        ]
        for path, purpose, expected in cases:
            with self.subTest(path=path):
                guess = infer_extension(path, purpose)
                self.assertIsNotNone(guess, '真实样本必须能推断出来')
                self.assertEqual(guess['extension'], expected)
                self.assertTrue(guess['basis'], '推断必须给出依据，不允许「看着像」')

    def test_a_name_with_no_usable_evidence_is_left_alone(self):
        """没有依据就不猜——让它照常被契约拒绝。"""
        for path, purpose in (('mystery_thing.', '完全无关的描述'),
                               ('mystery_thing.', ''),
                               ('mystery_thing.', None)):
            with self.subTest(purpose=purpose):
                self.assertIsNone(infer_extension(path, purpose),
                                  '没有依据时绝不推断')

    def test_a_complete_name_is_never_touched(self):
        """模型已经写全了扩展名时，一个字都不能改——**不覆盖模型原话**。"""
        for path in ('ast_verify_scanner.py', 'schema.json', 'a/b/checklist.md'):
            with self.subTest(path=path):
                self.assertIsNone(infer_extension(path, '核心逻辑脚本'))

    def test_a_different_dotted_name_is_not_mistaken_for_a_truncation(self):
        self.assertIsNone(infer_extension('pkg.module.', '核心逻辑'),
                          '点后面还有内容时，不是被写断')


class SameCallControlGroupTests(unittest.TestCase):
    """同一次真实调用里的 4 对 3 错——判据只能从这里读，不能凭感觉。"""

    WRITTEN_CORRECTLY = [
        ('key_aware_scanner.py', '实现密钥感知的 AST 扫描逻辑与信任评级映射', '.py'),
        ('evidence_rated_attestation.py', '融合证据链评级的设备密钥证明实现', '.py'),
        ('security_scheme_evaluator.py', '通用安全方案评估器实现，支持路径提取、diff 量化与约束检查', '.py'),
        ('degradation_constraint_checklist.md', '降级通道如实告知与防重放的检查清单', '.md'),
    ]
    CUT_SHORT = [
        ('key_trust_schema.', '定义密钥信任评级的分类标准与约束映射'),
        ('evidence_classification_rules.', '定义签名参数与校验逻辑的证据分类规则'),
        ('evaluation_report_schema.', '定义评估报告的结构，包含差异度指标与约束满足度指标'),
    ]

    def test_the_names_it_got_right_stay_untouched(self):
        for path, purpose, extension in self.WRITTEN_CORRECTLY:
            with self.subTest(path=path):
                self.assertTrue(path.endswith(extension), '这个对照样本本身就是依据')
                self.assertIsNone(infer_extension(path, purpose),
                                  '已经写全的路径一个字都不能改')

    def test_the_names_it_cut_short_are_now_inferred(self):
        for path, purpose in self.CUT_SHORT:
            with self.subTest(path=path):
                guess = infer_extension(path, purpose)
                self.assertIsNotNone(guess,
                                     f'真实样本 {path!r} 必须能推断出来——purpose 写的是「定义…」')
                self.assertEqual(guess['extension'], '.json')

    def test_the_rule_is_not_about_the_word_schema_in_the_filename(self):
        """**这条守卫是为了钉住一个已经犯过的错。**

        我曾说「名字里就带着 schema，扩展名理应是 .json」——那是猜。
        `evidence_classification_rules.` 名字里没有 schema 也照样被截断，
        说明判据在 purpose 而不在文件名。
        """
        self.assertIsNone(infer_extension('no_schema_word_here.', '完全无关的描述'),
                          '文件名没有 schema 也不该因此被推断——判据是 purpose')
        guess = infer_extension('also_no_schema.', '定义校验规则')
        self.assertIsNotNone(guess, 'purpose 写了「定义…规则」就该推断，与文件名无关')
        self.assertEqual(guess['extension'], '.json')


class InferenceLeavesATraceTests(unittest.TestCase):
    def test_inference_is_recorded_outside_the_artifact_object(self):
        """痕迹必须在**结果外层**，绝不能挂到产物上。

        2026-10-01 亲历的教训：我在产物上挂了一个 `inferred_extension` 字段，
        而契约的 `artifacts` 是 `additionalProperties=False`——
        **九批采样 0/6，全部死在自己加的这个痕迹字段上**。
        """
        from app.fusion.contract import RESULT_SCHEMA
        result = {'candidates': [{'title': 'c', 'artifacts': [
            {'path': 'audit_report_schema.', 'purpose': 'JSON Schema 定义'}]}]}
        record = infer_artifacts(result)
        artifact = result['candidates'][0]['artifacts'][0]
        self.assertEqual(artifact['path'], 'audit_report_schema.json')
        self.assertEqual(set(artifact), {'path', 'purpose'},
                         '产物上只能有契约约定的字段，痕迹一律放外层')
        self.assertEqual(set(RESULT_SCHEMA['properties']['candidates']['items']
                             ['properties']['artifacts']['items']['required']),
                         {'path', 'purpose'}, '契约本身也确认一下：产物只有这两个必填字段')
        self.assertTrue(record['applied'])
        self.assertEqual(record['inferred'][0]['from'], 'audit_report_schema.')

    def test_unresolvable_names_are_listed_not_silently_dropped(self):
        result = {'candidates': [{'artifacts': [
            {'path': 'mystery_thing.', 'purpose': '完全无关的描述'}]}]}
        record = infer_artifacts(result)
        self.assertEqual(result['candidates'][0]['artifacts'][0]['path'], 'mystery_thing.',
                         '推不出来就保持原样，让契约照常拒绝')
        self.assertFalse(record['applied'])
        self.assertEqual(record['unresolved'], ['mystery_thing.'], '推不出来的要如实记账')

    def test_a_result_whose_names_were_inferred_actually_passes_the_contract(self):
        """**端到端**：推断后的产物必须真的过得了契约。

        2026-10-01 漏了这一步，于是九批采样 0/6 全挂在 `check_shape` 上。
        单测只验了推断函数本身，没验推断之后的结构还合不合契约——
        「推断成功」与「推断后仍可提交」是两件事。
        """
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

        payload = proposed(packet)
        for candidate in payload['candidates']:
            for artifact in candidate['artifacts']:
                artifact['path'] = 'graded_report.'          # 模拟模型写到点为止
                artifact['purpose'] = '通用审查报告'            # 依据：报告 -> .md

        # 让 synthesize 自己走推断。不要在这里先调 infer_artifacts——
        # 那样路径已被改好，synthesize 自然无话可说，测的就不是真实路径了。
        with provider(payload) as (endpoint, _):
            out = synthesize(packet, endpoint=endpoint, model='test-only', api_key='k')
        self.assertTrue(out['artifact_names']['applied'], '结果里要留住推断痕迹')
        self.assertEqual(out['artifact_names']['inferred'][0]['to'], 'graded_report.md')
        self.assertEqual(out['artifact_names']['inferred'][0]['from'], 'graded_report.')
        for candidate in out['result']['candidates']:
            for artifact in candidate['artifacts']:
                self.assertEqual(set(artifact), {'path', 'purpose'},
                                 '推断之后产物上仍只能有契约约定的字段')


if __name__ == '__main__':
    unittest.main()
