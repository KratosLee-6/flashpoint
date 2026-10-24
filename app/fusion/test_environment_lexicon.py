import unittest

from app.fusion.capability import (ENVIRONMENT_LEXICON, MAX_SCENARIO_TERMS,
                                   _scenario_terms, environment_hits)


class EnvironmentLexiconTests(unittest.TestCase):
    """**词表口径的守卫**（2026-10-04 机主裁定）。

    背景：分布诊断查出来，失效面只产出 **8/36** 对，而 **9 个包里只有 1 个**
    写得出来——因为 `_scenario_terms` 是**句法代理**（引号字面量 / `==` 右值 +
    大写或含 `/.`），换个书写风格就抽不出来。

    改成**词表白名单**之后，抽得出的包 1 → 2，主路径产出 **1 → 14** 对。

    ## 为什么是白名单而不是更大的启发式

    启发式（黑名单）**无法给噪声定上界**；白名单可以：
    **报出的强词数 ≤ 词表大小**，而词表有限、可审计、可逐条解释。
    """

    def test_matching_must_use_word_boundaries(self):
        """**子串匹配是这一版第一版的真 bug，必须能被这条断言抓到。**

        2026-10-04 第一版用 `term in text`，于是 `nt` 命中了
        `print` / `environment` / `different`——**一个假阳性把天花板
        从 15/36 虚报成 21/36**。

        ⚠️ 这条守卫本身也返工过一次：改用最终词表后，`nt` 已被移除，
        于是我最初选的两个字符串（`print(...)` / `os.name`）**不再能区分**
        词边界与子串——反向验证立刻报「没有守卫变红」。
        **没变红不等于守卫没用，也可能是守卫根本没在测。**

        现在改用**当前词表里真实存在的脆弱词**：`ios` 是
        `scenarios` / `portfolios` / `studios` 的子串，
        而「scenarios」是本项目方向规则里的常用词——真出现了就是一个假场景词。"""
        for text in ('print(different environment variable)',
                     'os.name',
                     '这些 scenarios 应该分开评估',
                     'portfolios 与 radios 无关'):
            with self.subTest(text=text):
                self.assertEqual(environment_hits(text), [],
                                 '子串被当成了环境场景词')

    def test_a_whole_word_platform_name_is_found(self):
        for text, expected in (
            ('if platform.system() == "Windows": pass', 'windows'),
            ('这个脚本在 macOS 上和 Linux 上行为不同', 'macos'),
            ('only runs on darwin', 'darwin'),
        ):
            with self.subTest(text=text):
                terms = [t for t, _ in environment_hits(text)]
                self.assertIn(expected, terms)

    def test_chinese_natural_language_conditions_now_work(self):
        """**这是本次改动的核心价值**：以前中文条件句一个词都抽不出来。"""
        strong, _ = _scenario_terms('这个脚本在 macOS 上和 Linux 上行为不同')
        self.assertEqual(sorted(strong), ['linux', 'macos'])

    def test_a_variable_name_is_still_not_an_environment(self):
        """`if k not in ALLOWED_KEYS:` 里的 `ALLOWED_KEYS` 是**变量**不是环境。

        放宽到 `not in` 右值看起来很自然，但那样会引入纯噪声——
        而噪声比漏报更糟。**所以不引入。**"""
        strong, weak = _scenario_terms('if k not in ALLOWED_KEYS: pass')
        self.assertEqual(strong, set(), '变量名被当成了环境场景词')
        self.assertNotIn('ALLOWED_KEYS', strong)

    def test_the_same_environment_is_not_reported_twice(self):
        """**必须按大小写去重。**

        句法路径给出原形 `Windows`（带引号），词表路径给出 `windows`（小写形态），
        同一个环境于是被报两次。"""
        strong, _ = _scenario_terms('if platform.system() == "Windows": pass')
        lowered = [t.lower() for t in strong]
        self.assertEqual(len(lowered), len(set(lowered)),
                         '同一个环境被当成两个词：%s' % sorted(strong))
        self.assertEqual(len(strong), 1, '应当只剩一个：%s' % sorted(strong))

    def test_the_lexicon_stays_small_and_auditable(self):
        """**词表是噪声的硬上界。** 它必须小、必须可逐条解释，
        而且**只能靠真实材料扩充**，不能凭想象加。

        2026-10-04 实测：架构档 0/9 包命中、运行时档 0/9 包命中——
        所以它们**没有进表**。加一个命中不了的档位只会让词表看起来更完备、
        实际行为完全不变。"""
        self.assertLessEqual(len(ENVIRONMENT_LEXICON), 30,
                             '词表明显变大了——扩充必须有真实材料支撑并记入 CHANGELOG')
        self.assertTrue(ENVIRONMENT_LEXICON)
        self.assertEqual(len(ENVIRONMENT_LEXICON), len(set(ENVIRONMENT_LEXICON)),
                         '词表里有重复项')


class ScenarioNoiseCriterionsTests(unittest.TestCase):
    """**三条噪声判据**（机主裁定的口径）——这是「完美解决」能兑现的部分。"""

    def test_the_per_side_cap_bounds_the_noise(self):
        """**每侧最多 `MAX_SCENARIO_TERMS` 个强场景词。**"""
        self.assertGreaterEqual(MAX_SCENARIO_TERMS, 1)
        self.assertLessEqual(MAX_SCENARIO_TERMS, 3,
                             '上限被放宽了——那等于取消噪声上界')

    def test_every_gap_carries_an_offset_into_the_original_text(self):
        """**噪声判据 ①：每个词必须能指回原文。**

        定位不到就不报——**宁可漏报，不可报个查无实据的**。
        偏移是大小写不敏感找的：词表项是小写 `macos`，原文常写 `macOS`，
        直接 `find` 会把词整个丢掉（这条当时把 `anchored_share` 从 0.914 砸到 0.857）。"""
        from app.fusion.capability import bridge_failures, extract

        class Pkg:
            def __init__(self, items):
                self.items = {axis: [{'text': t, 'evidence': None} for t in texts]
                              for axis, texts in items.items()}

        cap_a = {'items': {'constraints': [{'text': '必须逐条引用法条编号', 'evidence': None}],
                          'steps': [{'text': '1. 登记合同编号', 'evidence': None}]}}
        cap_b = {'items': {'conditions': [{'text': '这个脚本在 macOS 上行为不同',
                                           'evidence': None}],
                           'constraints': [{'text': '必须用 icacls 收紧权限',
                                            'evidence': None}]}}
        failures = bridge_failures(cap_a, cap_b)
        self.assertTrue(failures['gaps'], '这个夹具应当产出一个失效面 gap')
        gap = failures['gaps'][0]
        self.assertIn('term_offset', gap, 'gap 必须带字符偏移，否则无法核对')
        source = cap_b['items']['conditions'][0]['text']
        self.assertIn(gap['scenario_term'].lower(), source[gap['term_offset']:].lower(),
                      '偏移指错了位置')

    def test_weak_terms_never_become_gaps(self):
        """`nonce` / `val` / `tid` 变成场景词是 `capability.py` 里写明踩过的坑。
        这条保证那次修复没被回退。"""
        strong, weak = _scenario_terms('if any(n["val"] == nonce for n in self.seen[tid]):')
        self.assertEqual(strong, set())
        self.assertTrue(weak)
