"""连通检查的失败原因必须可诊断。

2026-09-30 第一次跑真实本机模型时暴露：max_tokens 太小导致输出被截断，
产品只报「Basic JSON probe failed」，用户完全看不出是自己的模型写不完。
这几条把每种失败原因分开锁住。
"""
import json
import unittest
from unittest.mock import patch

from app.fusion.engine import probe_model, request_model
from app.fusion.packages import FusionError

ENDPOINT = 'https://model.example/v1/chat/completions'


def envelope(content, finish='stop', tool_calls=None):
    return {'choices': [{'finish_reason': finish,
                         'message': {'content': content, 'tool_calls': tool_calls}}]}


class ProbeDiagnosticsTests(unittest.TestCase):
    def probe(self, raw):
        with patch('app.fusion.engine.request_model', return_value=raw):
            return probe_model(ENDPOINT, 'some-model', api_key='k', allow_billable_probe=True)

    def test_probe_gives_the_model_enough_room_to_finish(self):
        # 32 会让小模型连 {"ok":true} 都写不完；这是真实踩到的坑
        with patch('app.fusion.engine.request_model', return_value=envelope('{"ok":true}')) as call:
            probe_model(ENDPOINT, 'm', api_key='k', allow_billable_probe=True)
        self.assertGreaterEqual(call.call_args[0][2]['max_tokens'], 256)

    def test_a_truncated_answer_says_so_instead_of_a_generic_failure(self):
        with self.assertRaises(FusionError) as raised:
            self.probe(envelope('{\n  "ok": tr', finish='length'))
        self.assertIn('截断', str(raised.exception))
        self.assertIn('不是连接问题', str(raised.exception))

    def test_a_wrong_payload_says_the_payload_was_wrong(self):
        with self.assertRaises(FusionError) as raised:
            self.probe(envelope('{"ok":false}'))
        self.assertIn('{"ok":true}', str(raised.exception))

    def test_a_tool_call_is_refused_without_sending_material(self):
        with self.assertRaises(FusionError) as raised:
            self.probe(envelope('', tool_calls=[{'id': '1'}]))
        self.assertIn('工具', str(raised.exception))

    def test_a_clean_probe_never_claims_to_have_verified_fusion(self):
        report = self.probe(envelope('{"ok": true}'))
        self.assertTrue(report['basic_json_ok'])
        self.assertFalse(report['source_sent'])
        self.assertFalse(report['fusion_schema_verified'])

    def test_consent_is_still_required_before_any_request(self):
        with patch('app.fusion.engine.request_model') as call:
            with self.assertRaises(FusionError):
                probe_model(ENDPOINT, 'm', api_key='k')
            call.assert_not_called()


class ThinkingSwitchTests(unittest.TestCase):
    """推理参数：接推理型模型的唯一障碍（2026-10-01 智谱实测）。

    - `glm-4.7` 会先把 max_tokens 预算花在推理链上：带 `thinking:{type:disabled}`
      → 2.5~7 秒 / finish=stop；不带 → 4.6 秒 / finish=length / content 为空。
    - `glm-5.3` **始终思考型**：`{type:"disabled"}` 与 `{type:"low"}` 都被 400 拒绝
      （`请使用 low、high 或 max`），`{type:"enabled", effort:"low"}` 才通过形状校验。

    所以真值是 `{type: "enabled"|"disabled", effort?: "low"|"high"|"max"}`——
    **强度是另一个键，不在 type 的枚举里**。
    """

    def probe_body(self, **kwargs):
        with patch('app.fusion.engine.request_model', return_value=envelope('{"ok":true}')) as call:
            probe_model(ENDPOINT, 'glm-4.7', api_key='k', allow_billable_probe=True, **kwargs)
        return call.call_args[0][2]

    def test_not_choosing_sends_no_thinking_field_at_all(self):
        # 不选就不能发：自动探测要先发一次计费请求，而连接检查必须先取得明确同意。
        self.assertNotIn('thinking', self.probe_body())
        self.assertNotIn('thinking', self.probe_body(thinking=None))

    def test_the_toggle_values_map_straight_through(self):
        self.assertEqual(self.probe_body(thinking='disabled').get('thinking'), {'type': 'disabled'})
        self.assertEqual(self.probe_body(thinking='enabled').get('thinking'), {'type': 'enabled'})

    def test_effort_levels_become_enabled_plus_effort(self):
        # 真实 API 反推：type 的枚举里没有 low，强度必须走另一个键，
        # 否则 glm-5.3 这类始终思考型模型会直接 400。
        for level in ('low', 'high', 'max'):
            with self.subTest(level=level):
                self.assertEqual(self.probe_body(thinking=level).get('thinking'),
                                 {'type': 'enabled', 'effort': level})

    def test_an_unknown_mode_is_refused_before_any_request(self):
        for bad in ('auto', 'off', '', 'LOW', 'medium'):
            with self.subTest(bad=bad):
                with patch('app.fusion.engine.request_model') as call:
                    with self.assertRaises(FusionError) as raised:
                        probe_model(ENDPOINT, 'm', api_key='k', allow_billable_probe=True, thinking=bad)
                self.assertIn('不选则不发送该字段', str(raised.exception))
                call.assert_not_called()

    def test_the_choice_is_recorded_so_the_result_can_be_audited(self):
        with patch('app.fusion.engine.request_model', return_value=envelope('{"ok":true}')):
            out = probe_model(ENDPOINT, 'm', api_key='k', allow_billable_probe=True, thinking='low')
        self.assertEqual(out['thinking'], 'low', '结果里必须留痕：这次到底用的什么推理设置')


class ProviderErrorDiagnosticsTests(unittest.TestCase):
    """供应商错误必须可诊断，但**绝不回显敏感内容**。

    2026-10-01 智谱实测：它明确回了「该模型始终思考，不支持关闭思考；请使用 low、
    high 或 max」——这正是用户要的那句话，而产品当时只报 `Model HTTP 400`。
    于是改成白名单提取 `error.message`。代价是必须防住凭据从错误通道漏出去。
    """

    def body(self, message, code=400):
        return json.dumps({'error': {'message': message, 'code': code}}).encode('utf-8')

    def raise_http(self, code, raw):
        """走真实的 request_model，只把底层 opener 换成会抛 HTTPError 的东西。

        直接 mock `request_model` 是**测不到**错误转换的——转换就发生在它内部。
        """
        import io as _io
        from urllib import error as _error

        http = _error.HTTPError('https://m/v1/chat/completions', code, 'e', {}, _io.BytesIO(raw))

        class Failing:
            def open(self, *args, **kwargs):
                raise http

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with patch('app.fusion.engine.request.build_opener', return_value=Failing()):
            with self.assertRaises(FusionError) as raised:
                request_model(ENDPOINT, 'k', {'model': 'm', 'messages': []}, 10)
        return str(raised.exception)

    def test_the_actionable_provider_message_survives(self):
        message = self.raise_http(400, self.body('该模型始终思考，不支持关闭思考；请使用 low、high 或 max。'))
        self.assertIn('请使用 low、high 或 max', message, '用户要的那句话不能被丢掉')
        self.assertIn('请求被服务商拒绝', message)
        self.assertIn('400', message)

    def test_known_codes_get_a_reason_without_needing_the_body(self):
        for code, hint in [(401, '密钥'), (429, '额度'), (404, '端点路径')]:
            with self.subTest(code=code):
                message = self.raise_http(code, b'')
                self.assertIn(hint, message, f'HTTP {code} 应当有不需要响应体也能给出的说明')

    def test_credentials_never_come_back_out(self):
        for leak in ['Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz012345',
                     'api_key=abcd1234efgh5678',
                     'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.aBcDeFgHiJkLmNoPqRsTuVwXyZ012345',
                     'someone@example.com 的调用失败',
                     'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa']:
            with self.subTest(leak=leak[:24]):
                message = self.raise_http(400, self.body(f'调用失败：{leak}'))
                self.assertNotIn(leak.split('=')[-1].strip()[-12:], message,
                                 '疑似凭据的原片段绝不能出现在报错里')
                self.assertIn('请求被服务商拒绝', message, '只给固定文案，不给被污染的那句')

    def test_a_hostile_or_huge_body_does_not_crash_or_leak(self):
        for raw in [b'', b'not json at all', b'[]', b'{"error":{}}', b'\x80\xff\xfe']:
            with self.subTest(raw=raw[:12]):
                message = self.raise_http(500, raw)
                self.assertIn('500', message)
        huge = self.body('x' * 5000)
        self.assertIn('500', self.raise_http(500, huge), '超长错误体不得被整段带出')

    def test_an_unknown_code_still_says_something_useful(self):
        message = self.raise_http(418, self.body('模型暂时不可用'))
        self.assertIn('模型暂时不可用', message)
        self.assertIn('418', message)


if __name__ == '__main__':
    unittest.main()
