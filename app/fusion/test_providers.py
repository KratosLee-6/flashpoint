"""协议适配层与厂商目录的守卫。

## 为什么需要这些守卫

接 AI 家族最容易出的三类错，而且**都会静默地错**：

1. **请求体对不上** —— 拿 OpenAI 的 `messages`/`max_tokens`/`response_format`
   去打 Anthropic 的 `/v1/messages`，会拿到 400；而 400 的文案往往指向 key 或配额，
   **用户会去换 key，而真因是请求形状**。
2. **响应解析取不到文本** —— Anthropic 的文本在 `content[]` 里、Gemini 在
   `candidates[0].content.parts[]` 里。不特判就会 IndexError/KeyError，
   报出来是「响应格式异常」——**看不出是哪一家**。
3. **凭据漏出去** —— 错误通道或调试输出里出现真 key。

所以每条协议都要有**正例（能取到文本）**与**反例（形状不对必须报错，
且报错里不含凭据）**。
"""
import json
import unittest

from app.fusion import providers as P


class ProtocolInferenceTests(unittest.TestCase):
    def test_the_three_protocols_are_told_apart_by_shape(self):
        cases = {
            'https://api.deepseek.com/v1/chat/completions': P.OPENAI,
            'https://open.bigmodel.cn/api/paas/v4/chat/completions': P.OPENAI,
            'http://127.0.0.1:11434/v1/chat/completions': P.OPENAI,
            'https://api.anthropic.com/v1/messages': P.ANTHROPIC,
            'https://generativelanguage.googleapis.com/v1beta/models/x:generateContent':
                P.GEMINI,
        }
        for endpoint, expected in cases.items():
            with self.subTest(endpoint=endpoint):
                self.assertEqual(P.protocol_for(endpoint), expected)

    def test_an_unknown_shape_is_reported_as_unknown(self):
        """**认不出就说认不出**，不硬套 OpenAI——猜错会让 400 指向错误的成因。"""
        self.assertEqual(P.protocol_for('https://example.com/whatever'), '')

    def test_check_endpoint_refuses_what_it_cannot_recognise(self):
        with self.assertRaises(ValueError) as ctx:
            P.check_endpoint('https://example.com/whatever')
        self.assertIn('认不出', str(ctx.exception))

    def test_check_endpoint_refuses_a_shape_mismatched_protocol(self):
        with self.assertRaises(ValueError) as ctx:
            P.check_endpoint('https://api.anthropic.com/v1/messages', P.OPENAI)
        self.assertIn('不符', str(ctx.exception))

    def test_loopback_http_is_allowed_for_local_runners(self):
        """Ollama / vLLM / LM Studio 都跑在 loopback 的明文 HTTP 上。"""
        for endpoint in ('http://127.0.0.1:11434/v1/chat/completions',
                         'http://127.0.0.1:8000/v1/chat/completions'):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(P.check_endpoint(endpoint), endpoint)

    def test_plain_http_off_loopback_is_refused(self):
        with self.assertRaises(ValueError):
            P.check_endpoint('http://example.com/v1/chat/completions')

    def test_credentials_in_the_url_are_refused(self):
        with self.assertRaises(ValueError):
            P.check_endpoint('https://user:pw@api.deepseek.com/v1/chat/completions')


class RequestShapeTests(unittest.TestCase):
    WIRE = [{'role': 'system', 'content': 'SYS'}, {'role': 'user', 'content': 'USR'}]

    def test_openai_keeps_the_shape_it_always_had(self):
        _h, body, _u = P.build_request(P.OPENAI, self.WIRE, 'm', 4096, {'stream': False})
        self.assertEqual(body['model'], 'm')
        self.assertEqual(len(body['messages']), 2)
        self.assertEqual(body['messages'][0]['role'], 'system')

    def test_anthropic_lifts_system_out_of_messages(self):
        """Anthropic 的 `system` 是**顶层字段**，不是 messages 里的一条。
        放错位置它会忽略整段系统提示词——**而我们的不变量全在 SYSTEM 里**。"""
        _h, body, _u = P.build_request(P.ANTHROPIC, self.WIRE, 'claude-x', 4096, {})
        self.assertEqual(body['system'], 'SYS')
        self.assertEqual([m['role'] for m in body['messages']], ['user'])
        self.assertIsInstance(body['messages'][0]['content'], list,
                              'Anthropic 的 content 必须是数组')

    def test_anthropic_always_carries_its_version_header(self):
        headers, _b, _u = P.build_request(P.ANTHROPIC, self.WIRE, 'm', 100, {})
        self.assertEqual(headers['anthropic-version'], '2023-06-01')

    def test_gemini_puts_the_system_prompt_in_its_own_field(self):
        _h, body, _u = P.build_request(P.GEMINI, self.WIRE, 'gemini-2.5-pro', 4096, {})
        self.assertNotIn('messages', body)
        self.assertIn('systemInstruction', body)
        self.assertEqual([c['role'] for c in body['contents']], ['user'])
        self.assertEqual(body['generationConfig']['maxOutputTokens'], 4096)

    def test_json_mode_is_never_sent_to_a_protocol_that_rejects_it(self):
        """`response_format` 是 OpenAI 独有的；发给 Anthropic 会被 400。"""
        for proto in (P.ANTHROPIC, P.GEMINI):
            with self.subTest(protocol=proto):
                _h, body, _u = P.build_request(
                    proto, self.WIRE, 'm', 100, {'response_format': {'type': 'json_object'}})
                self.assertNotIn('response_format', body)

    def test_the_api_key_never_lands_in_the_body(self):
        """**凭据只在最后一刻进请求头**，永远不进 body——
        body 会被日志、错误通道、留档碰到。"""
        for proto in P.PROTOCOLS:
            with self.subTest(protocol=proto):
                _h, body, _u = P.build_request(proto, self.WIRE, 'm', 100, {})
                self.assertNotIn('SECRET-KEY', json.dumps(body, ensure_ascii=False))
                self.assertNotIn('sk-', json.dumps(body, ensure_ascii=False))

    def test_the_placeholder_is_replaced_only_by_install_key(self):
        for proto, header in ((P.ANTHROPIC, 'x-api-key'),
                              (P.GEMINI, 'x-goog-api-key'),
                              (P.OPENAI, 'Authorization')):
            with self.subTest(protocol=proto):
                h, _b, _u = P.build_request(proto, self.WIRE, 'm', 100, {})
                real = P.install_key(h, 'SECRET-KEY', proto)
                self.assertIn('SECRET-KEY', real[header])

    def test_an_empty_key_leaves_no_authorization_header(self):
        """本地模型可以没有 key——**不能硬塞一个空的 `Bearer `**。"""
        real = P.install_key({}, '', P.OPENAI)
        self.assertNotIn('Authorization', real)


class ResponseParsingTests(unittest.TestCase):
    def test_openai_shape(self):
        got = P.parse_response(P.OPENAI, {
            'choices': [{'finish_reason': 'stop', 'message': {'content': '{"ok":true}'}}]})
        self.assertEqual(got['text'], '{"ok":true}')
        self.assertFalse(got['tool_calls'])

    def test_anthropic_shape_joins_every_text_block(self):
        got = P.parse_response(P.ANTHROPIC, {
            'stop_reason': 'end_turn',
            'content': [{'type': 'text', 'text': '{"ok":'}, {'type': 'text', 'text': 'true}'}]})
        self.assertEqual(got['text'], '{"ok":true}')

    def test_gemini_shape_joins_every_part(self):
        part_a = {'text': '{"ok":'}
        part_b = {'text': 'true}'}
        payload = {
            'candidates': [{
                'finishReason': 'STOP',
                'content': {'parts': [part_a, part_b]},
            }],
        }
        got = P.parse_response(P.GEMINI, payload)
        self.assertEqual(got['text'], '{"ok":true}')

    def test_a_wrong_protocol_shape_is_reported_not_crashed(self):
        """把 Anthropic 的响应喂给 OpenAI 解析器，必须**说清楚**，
        而不是抛 IndexError/KeyError 让人以为网络坏了。"""
        with self.assertRaises(ValueError) as ctx:
            P.parse_response(P.OPENAI, {'content': [{'type': 'text', 'text': 'x'}]})
        self.assertIn('choices', str(ctx.exception))

    def test_anthropic_error_type_is_named(self):
        with self.assertRaises(ValueError) as ctx:
            P.parse_response(P.ANTHROPIC, {'type': 'error', 'error': {'message': 'bad key'}})
        self.assertIn('error', str(ctx.exception))

    def test_non_json_responses_are_refused(self):
        with self.assertRaises(ValueError):
            P.parse_response(P.OPENAI, ['not', 'a', 'dict'])


class FinishReasonTests(unittest.TestCase):
    def test_each_protocol_says_completion_in_its_own_words(self):
        """**OpenAI 叫 `stop`、Anthropic 叫 `end_turn`、Gemini 叫 `STOP`。**

        只认 `stop` 的话，Anthropic 的每一个正常结果都会被当成
        「输出被截断」而拒掉，而用户会去调 max_tokens——**他的输出根本没被截断**。
        """
        self.assertTrue(P.completed(P.OPENAI, 'stop'))
        self.assertTrue(P.completed(P.ANTHROPIC, 'end_turn'))
        self.assertTrue(P.completed(P.GEMINI, 'STOP'))

    def test_a_truncated_reply_is_not_completed(self):
        for proto, reason in ((P.OPENAI, 'length'), (P.ANTHROPIC, 'max_tokens'),
                              (P.GEMINI, 'MAX_TOKENS')):
            with self.subTest(protocol=proto):
                self.assertFalse(P.completed(proto, reason))


class CatalogTests(unittest.TestCase):
    def test_every_entry_declares_a_known_protocol(self):
        for entry in P.CATALOG:
            with self.subTest(provider=entry['id']):
                self.assertIn(entry['protocol'], P.PROTOCOLS)

    def test_every_endpoint_passes_its_own_protocol_check(self):
        """**目录里的端点必须自洽**——写错了用户第一次连就撞墙。"""
        for entry in P.CATALOG:
            with self.subTest(provider=entry['id']):
                self.assertEqual(P.check_endpoint(entry['endpoint'], entry['protocol']),
                                 entry['endpoint'])

    def test_ids_are_unique(self):
        ids = [e['id'] for e in P.CATALOG]
        self.assertEqual(len(ids), len(set(ids)), '厂商 id 重复会让界面下拉出重复项')

    def test_the_catalog_carries_no_credentials(self):
        blob = json.dumps(P.CATALOG, ensure_ascii=False)
        for banned in ('sk-', 'Bearer ', 'api_key=', 'secret', 'token='):
            self.assertNotIn(banned, blob, f'厂商目录里不得出现 {banned}——它是纯本地说明书')

    def test_local_runners_are_marked_as_key_optional(self):
        for entry in P.CATALOG:
            if '本地' in entry.get('label', '') or entry['id'] in ('ollama', 'vllm', 'lmstudio'):
                with self.subTest(provider=entry['id']):
                    self.assertIn(P.CAVEATS['key_optional'], entry['caveats'],
                                  '本地运行时要告诉用户 key 可以不填')

    def test_gemini_entries_need_the_model_in_the_path(self):
        for entry in P.CATALOG:
            if entry['protocol'] == P.GEMINI:
                with self.subTest(provider=entry['id']):
                    self.assertIn('{model}', entry['endpoint'],
                                  'Gemini 把模型名放在路径里，端点模板必须留占位符')


class UiCatalogParityTests(unittest.TestCase):
    """**UI 的厂商目录与产品目录必须逐条一致。**

    2026-10-02 加 Coding Plan 端点时就撞到过这个：`providers.py` 加了、
    `fusion-ui.js` 没加，用户在界面里**根本选不到**那个端点——
    而那正是「买了套餐却报余额不足」的解法。

    两份清单不可能靠自觉同步：它们在不同文件、给不同运行时看。
    """

    def ui_entries(self):
        import re
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / 'fusion-ui.js').read_text(encoding='utf-8')
        block = re.search(r'const PROVIDERS=\[(.*?)\n  \];', src, re.S)
        self.assertIsNotNone(block, '没在 fusion-ui.js 里找到 PROVIDERS 目录')
        rows = re.findall(
            r"\['([a-z0-9-]+)','((?:[^'\\]|\\.)*)','([^']*)',\[[^\]]*\],'([a-z]+)',",
            block.group(1))
        self.assertTrue(rows, '没解析出 UI 目录条目')
        return {r[0]: (r[1], r[2], r[3]) for r in rows}

    def test_every_ui_provider_exists_in_the_product_catalog(self):
        for pid, (_label, endpoint, proto) in self.ui_entries().items():
            with self.subTest(provider=pid):
                self.assertIn(pid, P.BY_ID, f'UI 里的 {pid} 在产品目录里没有')
                self.assertEqual(endpoint, P.BY_ID[pid]['endpoint'],
                                 f'{pid} 的端点两边不一致——用户会在界面里踩到旧端点')
                self.assertEqual(proto, P.BY_ID[pid]['protocol'],
                                 f'{pid} 的协议两边不一致')

    def test_the_coding_plan_endpoint_is_offered(self):
        """**Coding Plan 端点必须能选到**——它是用套餐额度的前提，
        而「用通用端点报余额不足」是极强的误导（2026-10-02 实测）。"""
        ids = set(self.ui_entries())
        self.assertIn('zhipu-coding', ids)
        self.assertIn('zhipu', ids, '通用端点也要保留——没买套餐的人用它')
        self.assertIn('/api/coding/paas/v4',
                      P.BY_ID['zhipu-coding']['endpoint'])


class ThinkBlockInContentTests(unittest.TestCase):
    """**2026-10-02 实测：MiniMax M3 把思考塞进 `content`，用 `<think>` 包起来。**

    真实响应的一字不差（`api.minimax.cn`）：
        content = '<think>The user is asking me to return a specific JSON object. '
                  'This is a straightforward request to output exactly `{"ok":true}`.</think>'
                  '\\n\\n{"ok":true}'
        usage.completion_tokens_details.reasoning_tokens = 0

    不剥就直接 `json.loads` 必然失败，而报错会指向「模型返回的不是 JSON」，
    **完全看不出真因是正文前面多了一段思考**——这类错最难查。
    """

    REAL = ('<think>The user is asking me to return a specific JSON object. '
            'This is a straightforward request to output exactly `{"ok":true}`.</think>'
            '\n\n{"ok":true}')

    def test_thinking_is_stripped_from_content(self):
        got = P.parse_response(P.OPENAI, {
            'choices': [{'finish_reason': 'stop',
                         'message': {'content': self.REAL}}]})
        self.assertEqual(got['text'], '{"ok":true}',
                         '剥完思考块必须正好剩下 JSON')
        self.assertTrue(got['thinking_stripped'], '应当记下剥过——排查时要用')

    def test_the_stripped_text_actually_parses(self):
        import json
        got = P.parse_response(P.OPENAI, {
            'choices': [{'finish_reason': 'stop', 'message': {'content': self.REAL}}]})
        self.assertEqual(json.loads(got['text']), {'ok': True})

    def test_text_without_thinking_is_untouched(self):
        got = P.parse_response(P.OPENAI, {
            'choices': [{'finish_reason': 'stop', 'message': {'content': '{"ok":true}'}}]})
        self.assertEqual(got['text'], '{"ok":true}')
        self.assertFalse(got['thinking_stripped'], '没有思考就不该说剥过')

    def test_multiple_think_blocks_are_all_stripped(self):
        raw = '<think>a</think>\n{"k":1}\n<think>b</think>\n'
        got = P.parse_response(P.OPENAI, {
            'choices': [{'finish_reason': 'stop', 'message': {'content': raw}}]})
        self.assertEqual(got['text'], '{"k":1}')

    def test_an_unclosed_think_block_is_refused_not_guessed(self):
        """**思考块没闭合 = 模型被截断在思考里**，正文根本不存在。
        这时绝不能返回半截文本让上层去解析一个空壳——
        必须明确报错，且说清是「思考没结束」而不是「JSON 不合法」。"""
        with self.assertRaises(ValueError) as ctx:
            P.parse_response(P.OPENAI, {
                'choices': [{'finish_reason': 'length',
                             'message': {'content': '<think>还在想，没写完'}}]})
        self.assertIn('思考', str(ctx.exception))

    def test_the_length_parameter_differs_per_endpoint(self):
        """**参数名不一致是最阴的一类坑**：
        MiniMax 已弃用 `max_tokens` 改叫 `max_completion_tokens`，
        照旧名发过去要么 400，要么**被静默忽略然后用默认长度**——
        表现成「模型怎么老是写一半」，完全不像参数问题。"""
        msgs = [{'role': 'user', 'content': 'x'}]
        _h, deepseek, _u = P.build_request(
            P.OPENAI, msgs, 'm', 8192, {}, 'https://api.deepseek.com/v1/chat/completions')
        _h, mm, _u = P.build_request(
            P.OPENAI, msgs, 'm', 8192, {}, 'https://api.minimax.cn/v1/chat/completions')
        self.assertIn('max_tokens', deepseek)
        self.assertNotIn('max_tokens', mm)
        self.assertIn('max_completion_tokens', mm)
        self.assertEqual(mm['max_completion_tokens'], 8192, '长度值本身不能被改名吃掉')

    def test_the_length_field_says_the_same_thing_the_body_does(self):
        """**判据与实际行为必须只有一个来源。**

        2026-10-02 踩过：`max_tokens_field()` 按**协议**查表决定用哪个参数名，
        而真正发请求的却是另一个按域名判断的函数。两套机制里，
        前者对 MiniMax 会给出**错误答案**（MiniMax 用的就是 openai 协议）。

        留着这样一个「说了 A、做了 B」的函数，谁调用它谁就踩雷，
        所以直接断言二者对每一家的说法都一致——不一致就是回归。"""
        for entry in P.CATALOG:
            if entry['protocol'] != P.OPENAI:
                continue
            with self.subTest(provider=entry['id']):
                said = P.max_tokens_field(entry['endpoint'], entry['protocol'])
                _h, body, _u = P.build_request(
                    entry['protocol'], [{'role': 'user', 'content': 'x'}], 'm', 4096,
                    {}, entry['endpoint'])
                if said is None:
                    self.assertNotIn('max_tokens', body)
                    self.assertNotIn('max_completion_tokens', body)
                else:
                    self.assertIn(said, body, f'{entry["id"]} 判据说 {said}，body 里却没有')

    def test_minimax_is_recognised_from_its_endpoint_not_its_protocol(self):
        """**MiniMax 用的是 openai 协议**——所以只能按端点认，不能按协议认。
        这条钉住「为什么必须按域名分流」，免得有人把域名表删掉「反正协议一样」。"""
        for host in ('https://api.minimax.io/v1/chat/completions',
                     'https://api.minimax.cn/v1/chat/completions'):
            with self.subTest(endpoint=host):
                self.assertEqual(P.protocol_for(host), P.OPENAI)
                self.assertEqual(P.max_tokens_field(host), 'max_completion_tokens')

    def test_the_endpoint_is_passed_explicitly_not_through_a_global(self):
        """**不许用模块级全局记端点**——它会让并发调用互相污染。"""
        import inspect
        source = inspect.getsource(P)
        self.assertNotIn('_CURRENT_ENDPOINT', source,
                         '端点必须显式传参；全局变量会让并发请求互相污染')
        # 端点必须真的**传下去**，而不是被 builder 忽略掉。
        # 2026-10-02 这条守卫曾断言字面量 'endpoint=endpoint'，
        # 而实现是位置传参——**守卫自己红了，说的却不是它想管的事**。
        # 改成断言「调用 builder 时端点确实在参数里」，与实现写法解耦。
        self.assertRegex(inspect.getsource(P.build_request),
                         r'_BUILDERS\[.*\]\(.*endpoint.*\)',
                         'build_request 必须把 endpoint 传给协议构造器')


class DisciplineTests(unittest.TestCase):
    def test_the_module_never_performs_io(self):
        import inspect
        source = inspect.getsource(P)
        for banned in ('urllib.request', 'http.client', 'socket', 'open('):
            self.assertNotIn(banned, source,
                             f'providers 只做形状转换，不得出现 {banned}')

    def test_the_catalog_is_a_literal_not_a_fetch(self):
        import inspect
        source = inspect.getsource(P)
        self.assertNotIn('requests', source)
        self.assertNotIn('urlopen', source)


if __name__ == '__main__':
    unittest.main()
