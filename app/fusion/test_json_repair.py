import json
import unittest

from app.fusion.json_repair import parse_lenient, repair
from app.fusion.packages import FusionError


class TokenizerSafetyTests(unittest.TestCase):
    """**字符串边界**——整个模块的安全基础。

    「完美解决」里最要紧的一条：证据引文完全可能包含 markdown 围栏和 JSON 片段。
    正则替换会把它们改坏；词法扫描不会。
    """

    def test_a_string_containing_json_is_never_touched(self):
        raw = json.dumps({'quote': '示例：\n```json\n{"a": 1,}\n```'}, ensure_ascii=False)
        # **故意让它含一个尾随逗号**——在字符串内部。
        self.assertIn('1,}', raw)
        ok, fixed, changes = repair(raw)
        self.assertTrue(ok)
        self.assertEqual(changes, [], '字符串内部的尾随逗号被当成瑕疵改了——词法扫描坏了')
        self.assertEqual(fixed, raw, '合法 JSON 却被重写了——修复必须零改动')
        self.assertEqual(json.loads(fixed)['quote'], '示例：\n```json\n{"a": 1,}\n```')

    def test_a_string_containing_escaped_quotes_and_braces(self):
        raw = '{"quote": "他说 \\"不该有,}\\" 然后走了"}'
        ok, fixed, _ = repair(raw)
        self.assertTrue(ok)
        self.assertEqual(json.loads(fixed)['quote'], '他说 "不该有,}" 然后走了')

    def test_an_unterminated_string_is_not_repaired(self):
        """没闭合引号是**根本性坏文本**，不是瑕疵。标记出来，不硬来。"""
        ok, fixed, changes = repair('{"a": "没关')
        self.assertFalse(ok, '没闭合的字符串被当成可修的瑕疵了')
        self.assertEqual(changes, [])


class TrailingCommaTests(unittest.TestCase):
    """尾随逗号：严格 JSON 里它非法，所以原文**只能**表示去掉它的那版。没有第二种读法。"""

    def test_the_real_measured_shape(self):
        """2026-10-03 实测的原文形状（char 29486）：
        `"purpose": "…注入",` 后面直接 `}`。"""
        raw = '{"artifacts": [{"path": "template/sorter.py", "purpose": "描述输入输出契约",},]}'
        ok, fixed, changes = repair(raw)
        self.assertTrue(ok)
        self.assertEqual(len(changes), 2, '对象与数组各一个尾随逗号')
        self.assertEqual(json.loads(fixed),
                         {'artifacts': [{'path': 'template/sorter.py',
                                         'purpose': '描述输入输出契约'}]})

    def test_the_repair_does_not_change_any_value(self):
        raw = '{"a": 1, "b": [1, 2, ], "c": {"d": 3, }, }'
        ok, fixed, changes = repair(raw)
        self.assertTrue(ok)
        self.assertEqual(json.loads(fixed), {'a': 1, 'b': [1, 2], 'c': {'d': 3}})
        self.assertEqual([c['kind'] for c in changes],
                         ['trailing_comma'] * 3)

    def test_already_valid_json_is_returned_untouched(self):
        raw = '{"a": 1, "b": [1, 2]}'
        ok, fixed, changes = repair(raw)
        self.assertTrue(ok)
        self.assertIs(fixed, raw, '合法 JSON 被重写了')
        self.assertEqual(changes, [])


class MissingCommaTests(unittest.TestCase):
    """缺逗号：**只在唯一可判的那一种情形下补**，其余一律不猜。"""

    def test_a_missing_comma_between_object_members_is_repaired(self):
        raw = '{"a": 1 "b": 2}'
        ok, fixed, changes = repair(raw)
        self.assertTrue(ok)
        self.assertEqual(json.loads(fixed), {'a': 1, 'b': 2})
        self.assertEqual(changes[0]['kind'], 'missing_comma')

    def test_a_missing_comma_between_array_elements_is_REFUSED(self):
        """`[1 2]` 补成 `[1,2]` 是一种读法，**`[12]` 写成 `[1 2]` 也是一种**。
        有两种读法就是猜——本模块明确不猜。"""
        ok, fixed, changes = repair('[1 2]')
        self.assertFalse(ok, '数组元素间的缺逗号被补了——那是在猜')
        self.assertEqual(changes, [])
        self.assertEqual(fixed, '[1 2]', '修不了就必须原样交回')

    def test_a_missing_comma_before_a_nested_object_is_REFUSED(self):
        """`{"a":1 {"b":2}}` 同样有多种读法（`1` 后面是不是还该有别的）。不补。"""
        ok, _fixed, changes = repair('{"a": 1 {"b": 2}}')
        self.assertFalse(ok)
        self.assertEqual(changes, [])

    def test_a_missing_comma_before_an_array_inside_an_object_is_repaired(self):
        """下一个 token 是 `[`，**紧跟在键的冒号后面**——那只能是一个新成员。"""
        raw = '{"a": 1 "b": [2]}'
        ok, fixed, changes = repair(raw)
        self.assertTrue(ok)
        self.assertEqual(json.loads(fixed), {'a': 1, 'b': [2]})
        self.assertEqual(changes[0]['kind'], 'missing_comma')


class RefusesToGuessTests(unittest.TestCase):
    """**本模块最重要的守卫。** 凡是「只有一种读法」之外的，一律报错。"""

    def test_single_quotes_are_not_repaired(self):
        self.assertFalse(repair("{'a': 1}")[0])

    def test_trailing_semicolons_are_not_repaired(self):
        self.assertFalse(repair('{"a": 1;}')[0])

    def test_unquoted_keys_are_not_repaired(self):
        self.assertFalse(repair('{a: 1}')[0])

    def test_truncated_text_is_not_completed(self):
        """**绝不补闭合符号。** 截断的输出编不出内容，
        补齐等于替模型编造。"""
        self.assertFalse(repair('{"a": 1, "b": [1, 2')[0])
        self.assertFalse(repair('{"artifacts": [{"path": "x"')[0])

    def test_a_non_string_does_not_explode(self):
        for value in (None, 42, b'{}', [], {}):
            with self.subTest(value=value):
                ok, fixed, _ = repair(value)
                self.assertFalse(ok)


class StrictChecksSurviveRepairTests(unittest.TestCase):
    """**修复不等于放松。** 修好之后，重复键与非有限数字照旧必须被拒。"""

    def test_duplicate_keys_are_still_rejected_after_repair(self):
        """一个带尾随逗号、**且有重复键**的文本：语法修好了，重复键照样拒。"""
        raw = '{"a": 1, "a": 2,}'
        ok, _fixed, _ = repair(raw)
        self.assertTrue(ok, '语法瑕疵应当被修掉')
        with self.assertRaises(FusionError) as ctx:
            parse_lenient(raw)
        self.assertIn('Duplicate', str(ctx.exception))

    def test_non_finite_numbers_are_still_rejected_after_repair(self):
        raw = '{"v": NaN,}'
        ok, _fixed, _ = repair(raw)
        self.assertTrue(ok)
        with self.assertRaises(FusionError):
            parse_lenient(raw)


class ChangeTraceTests(unittest.TestCase):
    """**静默修好等于放宽校验。** 改了什么必须能说出来。"""

    def test_every_repair_is_reported(self):
        obj, changes = parse_lenient('{"a": 1, "b": 2,}')
        self.assertEqual(obj, {'a': 1, 'b': 2})
        self.assertTrue(changes, '修了却没留下痕迹')
        self.assertEqual(changes[0]['kind'], 'trailing_comma')
        self.assertIn('at', changes[0])

    def test_a_clean_response_reports_nothing(self):
        obj, changes = parse_lenient('{"a": 1}')
        self.assertEqual(obj, {'a': 1})
        self.assertEqual(changes, [], '没改任何东西却报了改动——那会让「改痕」这个信号失效')

    def test_unrepairable_text_raises_with_an_actionable_message(self):
        with self.assertRaises(FusionError) as ctx:
            parse_lenient('{"a": 1, "b": [1 2]}')
        message = str(ctx.exception)
        self.assertIn('trailing commas', message,
                      '报错要说明本产品到底修什么、不修什么——'
                      '「Invalid or ambiguous JSON」把三种原因混成一句话，吃过亏了')
        self.assertIn('guesses are refused', message)

    def test_the_message_carries_the_json_verdict_not_just_our_label(self):
        """**2026-10-04 实测：只看我们自己的文案会被带偏。**

        真实失败是引号转义不配对，而 `json` 报的是 `Expecting ',' delimiter`——
        **完全误导**（真实原因是引号不是逗号）。当时就是「只看这一句报错」排查错了方向。

        所以报错必须**带上 json 自己的判定**。"""
        broken = '{"q": "路径 (platform.system() == \\"Windows"）的分支"}'
        with self.assertRaises(FusionError) as ctx:
            parse_lenient(broken)
        message = str(ctx.exception)
        self.assertIn('json 自己的判定', message,
                      '报错里没有 json 的原始判定——那正是把排查带偏的那句')
        self.assertRegex(message, r'第 \d+ 行第 \d+ 列，字符 \d+',
                         '必须带位置，否则拿到报错也不知道去哪儿看')

    def test_the_message_must_never_echo_the_model_output(self):
        """**报错不许回显内容。** 供应商响应体可能含提示词或凭据。

        `_why_unrepairable` 只取 json 的 `msg` / 行列 / 字符位置，**不碰 `doc`**。"""
        secret = 'sk-SHOULD-NEVER-APPEAR-IN-AN-ERROR'
        with self.assertRaises(FusionError) as ctx:
            parse_lenient('{"a": 1, "b": [%s 2]}' % secret)
        self.assertNotIn(secret, str(ctx.exception),
                         '报错把模型原文回显了——那可能含提示词或凭据')


class RealMeasuredSampleTests(unittest.TestCase):
    """**真实样本守卫**（2026-10-03）。

    之前只拿合成样本测，**结果全是绿的，而真实样本一条都修不了**。
    把真实留档钉进来，是这个模块能不能信的分水岭。

    ## 一次重要的认知修正

    我原先记的是「尾随逗号 @29486」——**只根据 Python 报的第一个错**。
    实际上这份样本有 **5 处尾随逗号**，而且它还带着 markdown 围栏。
    两者合起来才是完整病因；**只按首个报错下结论会漏掉其余 4 处**。

    这正是本项目吃过亏的那一条：`Invalid or ambiguous JSON` 把不同病因
    混成一句话，看报错永远分不出来。

    ## 报告位置与实际位置差了 7 个字符

    因为当初定位时用的是**剥掉围栏之后**的文本，而留档写的是**带围栏的原文**。
    差值正好是 `“```json\\n”` 的长度。位置口径必须在留档里写清楚，否则复核会找错地方。
    """

    SAMPLE = 'app/fusion/evaluations/m3-fail-sample.txt'

    def setUp(self):
        from pathlib import Path
        # test_json_repair.py 在 app/fusion/ 下，所以留档就在同级的 evaluations/。
        path = Path(__file__).resolve().parent / 'evaluations' / 'm3-fail-sample.txt'
        if not path.exists():
            self.skipTest('真实样本留档不在仓库里——它就是本测试的判据来源')
        self.raw = path.read_text(encoding='utf-8')

    def test_the_raw_sample_alone_cannot_be_parsed(self):
        """**先确认它真的是坏的。** 守卫最怕的就是「样本其实早就能解析」，
        那样后面所有断言都在测一个不存在的问题。"""
        import json as _json
        with self.assertRaises(ValueError):
            _json.loads(self.raw)

    def test_the_full_pipeline_repairs_it_entirely(self):
        from app.fusion.engine import unfence
        obj, changes = parse_lenient(unfence(self.raw))
        self.assertEqual(sorted(obj), ['candidates', 'direction_reviews', 'profiles'])
        self.assertEqual(len(obj['profiles']), 2)
        self.assertEqual(len(obj['candidates']), 3)
        self.assertEqual([c['orientation'] for c in obj['candidates']], ['A', 'B', 'C'])

    def test_it_records_every_single_change(self):
        """5 处尾随逗号，一处都不能少报。"""
        from app.fusion.engine import unfence
        _obj, changes = parse_lenient(unfence(self.raw))
        self.assertEqual(len(changes), 5,
                         '真实样本里的尾随逗号不止一处——只报一处就等于掩盖了其余几处')
        self.assertTrue(all(c['kind'] == 'trailing_comma' for c in changes))

    def test_the_change_trace_carries_a_character_offset(self):
        """**报告位置必须能在原文里定位**，否则复核时无从查起。"""
        from app.fusion.engine import unfence
        body = unfence(self.raw)
        _obj, changes = parse_lenient(body)
        for change in changes:
            with self.subTest(change=change):
                offset = change['at']
                self.assertIsInstance(offset, int)
                # 偏移必须落在 body 的长度范围内，且那里确实曾是一个逗号
                self.assertLess(offset, len(body))
                self.assertEqual(body[offset], ',',
                                 '改动位置在原文里不是一个逗号——偏移口径错了')


class IdempotenceTests(unittest.TestCase):
    """修完再修，必须是恒等的——否则第二次调用会改出新问题。"""

    def test_repairing_twice_changes_nothing_the_second_time(self):
        for raw in ('{"a": 1,}', '{"a": 1 "b": 2}', '[1 2]', '{"a": [1,], "b": {"c": 2,},}'):
            with self.subTest(raw=raw):
                ok1, once, _ = repair(raw)
                ok2, twice, changes2 = repair(once)
                self.assertEqual(ok1, ok2)
                if ok1:
                    self.assertEqual(once, twice, '第二次修复又动了手')
                    self.assertEqual(changes2, [])
