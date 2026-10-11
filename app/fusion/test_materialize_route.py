"""`materialize` 路由的集成测试——真实 server + 真实 Workspace + 哨兵 mock 模型。

    python -m pytest app/fusion/test_materialize_route.py -q

## 守什么

「物化并自测」是机主裁定（2026-10-07：「能跑得动就说明是正常的」）的产品入口。
路由层要守住：

1. **前置**：没有已通过验收的结果 → 拒绝；朝向不在结果里 → 拒绝；正在跑 → 拒绝；
2. **happy path**：run 之后发起物化 → job 完成 → `runnable=True`、包体落在
   deliverables 旁的 materialize 工作区、selftest 真实退出 0；
3. **不冒充**：报告必须带价值注记（「可运行」不等于「比父本好」）。

`last_result` 在 happy path 里直接注入（`proposed(packet)` 的契约合规结果）——
run→物化的全链已在 evaluations/materialize-* 用真实模型走过；这里只测路由语义。
"""
from __future__ import annotations

import json
import threading
import time
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .server import Workspace, make_server
from .test_fusion import proposed
from .test_server import LoopbackAppFixture


@contextmanager
def sentinel_server(plan_files):
    """按调用序回哨兵格式响应；plan_files: [(path, content)]。"""
    calls = []
    responses = ['===PATH: %s===\n%s\n===END===' % (p, c) for p, c in plan_files]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            length = int(self.headers.get('Content-Length', '0'))
            self.rfile.read(length)
            payload = responses[min(len(calls), len(responses)) - 1]
            calls.append(payload[:60])
            data = json.dumps({'choices': [{'finish_reason': 'stop',
                                            'message': {'content': payload}}]}).encode()
            try:
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:%d/v1/chat/completions' % server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class MaterializeRouteTests(LoopbackAppFixture):
    def prepare(self):
        status, _ = self.call('prepare', {'a': str(self.a), 'b': str(self.b), 'goal': '物化路由测试'})
        self.assertEqual(status, 200)
        return self.workspace.packet

    def test_rejects_before_any_result_exists(self):
        self.prepare()
        with sentinel_server([('verifier.py', 'x = 1\n')]) as endpoint:
            status, body = self.call('materialize', {
                'packet_sha256': self.workspace.packet['sha256'], 'orientation': 'C',
                'endpoint': endpoint, 'model': 'm', 'api_key': 'k'})
        self.assertEqual(status, 400)
        self.assertIn('物化', body['error'])

    def test_rejects_unknown_orientation(self):
        self.prepare()
        self.workspace.last_result = {'result': proposed(self.workspace.packet)}
        status, body = self.call('materialize', {
            'packet_sha256': self.workspace.packet['sha256'], 'orientation': 'D',
            'endpoint': 'http://127.0.0.1:9/v1/chat/completions', 'model': 'm', 'api_key': 'k'})
        self.assertEqual(status, 400)
        self.assertIn('A/B/C', body['error'])

    def test_happy_path_materializes_package_and_runs_selftest(self):
        self.prepare()
        packet = self.workspace.packet
        self.workspace.last_result = {'result': proposed(packet)}
        plan_files = [('scripts/feedback.py', 'def verify(x):\n    return x\n'),
                      ('selftest.py', 'import sys, os\n'
                                      'sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scripts"))\n'
                                      'import feedback\n'
                                      'assert feedback.verify(1) == 1\n'
                                      'print("ALL CHECKS PASSED")\n')]
        with sentinel_server(plan_files) as endpoint:
            status, job = self.call('materialize', {
                'packet_sha256': packet['sha256'], 'orientation': 'C',
                'endpoint': endpoint, 'model': 'm', 'api_key': 'k'})
            self.assertEqual(status, 200)
            self.assertEqual(job['status'], 'running')
            for _ in range(120):
                status, done = self.call('job', {'id': job['id']})
                if done['status'] != 'running':
                    break
                time.sleep(0.1)
        self.assertEqual(done['status'], 'completed', done.get('error'))
        report = done['result']
        self.assertTrue(report['runnable'])
        self.assertEqual(report['selftest']['exit_code'], 0)
        self.assertIn('scripts/feedback.py', report['files'])
        self.assertTrue(Path(report['package_dir'], 'scripts', 'feedback.py').exists())
        self.assertIn('比父本好', report['value_note'],
                      '「可运行」不得被读成「比父本好」——价值注记必须随报告走')
        # 工作区必须落在 deliverables 旁的 materialize/ 下（.fusion-runs 已 gitignore）
        self.assertIn('materialize', Path(report['package_dir']).parts[-2:] or
                      [Path(report['package_dir']).parent.name])

    def test_route_is_rejected_while_another_model_request_runs(self):
        from .test_fusion import provider
        self.prepare()
        self.workspace.last_result = {'result': proposed(self.workspace.packet)}
        with provider(result=proposed(self.workspace.packet), delay=0.3) as (endpoint, _):
            started = self.call('run', {'packet_sha256': self.workspace.packet['sha256'],
                                        'endpoint': endpoint, 'model': 'm', 'api_key': 'k'})
            self.assertEqual(started[0], 200)
            with sentinel_server([('verifier.py', 'x = 1\n')]) as materialize_endpoint:
                status, body = self.call('materialize', {
                    'packet_sha256': self.workspace.packet['sha256'], 'orientation': 'C',
                    'endpoint': materialize_endpoint, 'model': 'm', 'api_key': 'k'})
            self.assertEqual(status, 400)
            self.assertIn('不能重复发送', body['error'])


if __name__ == '__main__':
    unittest.main()
