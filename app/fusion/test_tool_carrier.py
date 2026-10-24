"""`carrier='tool'`（function-call 承载）的强制测试——2026-10-05 新增，默认关闭。

## 这条不变量在守什么

把结构化结果从「裸 JSON 正文」改由 function-call 的 `arguments` 承载，
是为了消掉 `json_syntax` 那一类失败（n=16 基线里占 **13/16 = 81%**）。
依据是 `evaluations/toolcall-probe.json`：MiniMax-M3 的 function-call 参数
**确实受 schema 约束**，而 `response_format` 整族被实测为空操作。

## 为什么「不兜底」是这里最要紧的一条

`carrier='tool'` 拿不到 `arguments` 时，**必须报错，绝不退回读正文**。
一旦悄悄回退，A/B 的两臂在失败时走的是不同路径，读数立刻失去意义——
而这类「失败时换个路走」的退化正是本项目栽过多次的坑。

所以有一条测试**故意让正文里放着一份合法 JSON、而 arguments 缺失**，
断言它**必须失败**。这条测试在「偷偷加兜底」的实现上会变红。

## 反向验证

把 `engine.synthesize` 里的 `carrier == 'tool'` 分支删掉（或把 `raw_json`
改回 `reply['text']`），下面这几条必须变红：
- `test_payload_comes_from_arguments_not_body`（正文与 arguments 内容不同）
- `test_missing_arguments_is_rejected_and_never_falls_back_to_body`
"""
from __future__ import annotations

import json
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time

from app.fusion import engine, providers
from app.fusion.engine import inspect_packages
from app.fusion.packages import FusionError

#: 本文件在 `app/fusion/` 下，仓库根是 parents[2]。
#: 写成 parents[1]（= app/）会**静默**找不到包，报错信息还指向包名，很有迷惑性。
ROOT = Path(__file__).resolve().parents[2]


@contextmanager
def server_for(payloads):
    """payloads: 一个函数，按收到的请求序号返回要回的响应体。"""
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            seen.append(body)
            data = payloads(len(seen) - 1, body)
            try:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(data).encode())
            except (BrokenPipeError, ConnectionResetError):
                pass

    srv = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f'http://127.0.0.1:{srv.server_port}/v1/chat/completions', seen
    finally:
        srv.shutdown()
        srv.server_close()
        t.join()


def envelope(content='', finish='stop', arguments=None):
    msg = {'content': content}
    if arguments is not None:
        msg['tool_calls'] = [{'id': '1', 'type': 'function', 'function': {
            'name': engine.RESULT_TOOL_NAME, 'arguments': arguments}}]
    return {'choices': [{'finish_reason': finish, 'message': msg}]}


def sample_result():
    """一份结构合法的最小结果——够 `parse_lenient` 吃下。"""
    return {'profiles': [{'parent': 'A', 'capabilities': [], 'retention': {
        'keep': [], 'discard': [], 'reason': 'x'}, 'dependencies': [], 'missing': [],
        'coverage': 'x'}], 'candidates': [], 'direction_reviews': []}


class ToolCarrierTests(unittest.TestCase):
    """全部只测 `carrier='tool'` 这一条路径；默认路径由既有 588 个用例守着。"""

    def test_request_carries_tools_and_forced_tool_choice(self):
        """请求里必须有 tools + **强制**的 tool_choice，且不发 response_format。

        这条只验**请求的形状**，所以直接打 `chat_complete`——
        请求装配是它的职责，不该被下游的契约校验连累。
        """
        got = {}

        def payloads(i, body):
            got.update(body)
            return envelope(arguments=json.dumps(sample_result()))

        with server_for(payloads) as (url, _):
            engine.chat_complete(engine.messages(_packet()), endpoint=url,
                                 model='m', carrier='tool', max_tokens=2048)
        self.assertIn('tools', got, 'carrier=tool 必须发 tools')
        self.assertEqual(got['tools'][0]['function']['name'],
                         engine.RESULT_TOOL_NAME)
        # **强制调用**：留 'auto' 的话模型可能只在正文说话，等于没换通道
        self.assertEqual(got.get('tool_choice'),
                         {'type': 'function',
                          'function': {'name': engine.RESULT_TOOL_NAME}})
        # response_format 在 M3 上已被实测为空操作，发了只是噪声
        self.assertNotIn('response_format', got,
                         'carrier=tool 时不该再发 response_format（实测空操作）')

    def test_tool_schema_is_the_strict_projection(self):
        from app.fusion.contract import strict_schema
        self.assertEqual(engine.result_tool()['function']['parameters'],
                         strict_schema())

    def test_payload_comes_from_arguments_not_body(self):
        """**正文根本不是 JSON**，arguments 是一份 JSON——结果必须按 arguments 走。

        判据用**报错内容**而不是「跑通了」：按 arguments 读，样本不满足骨架的
        数量约束，报的是 `profiles` 那类契约错；若按正文读，
        `parse_lenient` 会先炸，报的是 JSON 语法错。两者互斥，所以能区分。
        """
        def payloads(i, body):
            return envelope(content='<<< 这压根不是 JSON >>>',
                            arguments=json.dumps(sample_result()))

        with server_for(payloads) as (url, _):
            with self.assertRaises(FusionError) as ctx:
                engine.synthesize(_packet(), endpoint=url, model='m',
                                  carrier='tool', max_tokens=2048)
        msg = str(ctx.exception)
        self.assertIn('profiles', msg,
                      '按 arguments 读才该报 profiles 数量错；实际：%s' % msg)
        self.assertNotIn('JSON', msg,
                         '说明它去读正文了——正文那句不是 JSON：%s' % msg)

    def test_missing_arguments_is_rejected_and_never_falls_back_to_body(self):
        """**正文里放着一份合法 JSON，arguments 缺失 → 必须失败。**

        这是本文件最要紧的一条：一旦实现偷偷退回读正文，A/B 两臂在失败时
        走不同路径，读数作废。这条在「偷偷加兜底」的实现上必须变红。
        """
        def payloads(i, body):
            return envelope(content=json.dumps(sample_result()), arguments=None)

        with server_for(payloads) as (url, _):
            with TemporaryDirectory() as tmp:
                with self.assertRaises(FusionError) as ctx:
                    engine.synthesize(_packet(tmp), endpoint=url, model='m',
                                      carrier='tool', max_tokens=2048)
        self.assertIn('arguments', str(ctx.exception))

    def test_truncated_tool_call_is_rejected(self):
        def payloads(i, body):
            return envelope(finish='length',
                            arguments=json.dumps(sample_result()))

        with server_for(payloads) as (url, _):
            with TemporaryDirectory() as tmp:
                with self.assertRaises(FusionError) as ctx:
                    engine.synthesize(_packet(tmp), endpoint=url, model='m',
                                      carrier='tool', max_tokens=2048)
        self.assertIn('截断', str(ctx.exception))

    def test_unknown_carrier_is_rejected(self):
        with self.assertRaises(FusionError):
            engine.synthesize(None, endpoint='http://x/v1/chat/completions',
                              model='m', carrier='whatever')

    def test_tool_carrier_rejected_on_non_openai_protocol(self):
        with self.assertRaises(FusionError) as ctx:
            engine.chat_complete([], endpoint='https://api.anthropic.com/v1/messages',
                                 model='m', carrier='tool', protocol=providers.ANTHROPIC)
        self.assertIn('OpenAI', str(ctx.exception))

    def test_default_carrier_still_sends_response_format(self):
        """默认路径一个字都没变——既有行为保持原样。"""
        got = {}

        def payloads(i, body):
            got.update(body)
            return envelope(content=json.dumps(sample_result()))

        with server_for(payloads) as (url, _):
            with TemporaryDirectory() as tmp:
                with self.assertRaises(FusionError):
                    # 默认路径会因为契约/证据问题失败，这里只关心请求体
                    engine.synthesize(_packet(tmp), endpoint=url, model='m',
                                      max_tokens=2048)
        self.assertIn('response_format', got)
        self.assertNotIn('tools', got)

    def test_parse_response_exposes_tool_arguments(self):
        got = providers.parse_response(
            providers.OPENAI, envelope(arguments='{"a":1}'))
        self.assertTrue(got['tool_calls'])
        self.assertEqual(got['tool_arguments'], ['{"a":1}'])

    def test_parse_response_without_tool_calls_gives_empty_arguments(self):
        got = providers.parse_response(providers.OPENAI, envelope(content='hi'))
        self.assertFalse(got['tool_calls'])
        self.assertEqual(got['tool_arguments'], [])


#: 复用仓库里真实的示例包——临时空目录过不了 `verify_packet`（它要 SKILL.md）
PAIR = ('ast-static-scan-arch', 'device-key-attestation')
_PACKET = None


def _packet(tmp=None):
    """真实包造出来的 packet，模块内缓存一次即可。"""
    global _PACKET
    if _PACKET is None:
        _PACKET = inspect_packages(_locate(PAIR[0]), _locate(PAIR[1]),
                                   goal='把静态分析里的证据链方法用到设备密钥证明上')
    return _PACKET


def _locate(name):
    for folder in ('skillhub-skill', 'app/examples'):
        p = ROOT / folder / name
        if (p / 'SKILL.md').exists():
            return p
    raise FusionError('missing package: ' + name)


if __name__ == '__main__':
    unittest.main()
