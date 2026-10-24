"""Integration tests for mounted sources and the real local HTTP API, using synthetic data."""
import json
import io
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib import request, error
from unittest.mock import patch

from .packages import snapshot, FusionError
from .engine import prepare, messages, verify_packet
from .server import make_server, Workspace, MAX_BODY
from .test_fusion import provider, proposed


class LoopbackAppFixture(unittest.TestCase):
    """真实 server + 真实 Workspace 的夹具，只有 setUp/call/stop，没有用例。

    抽出它是因为 `test_fill_route.FillRouteTests` 原本**继承** `AppIntegrationTests`
    来复用这套夹具——那会把父类整套用例原样再跑一遍（CI 里凭空多几十秒），
    而且失败会被报在错误的模块名下，极易归错因。要复用夹具就组合，不要继承用例。
    """
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.a, self.b = self.root/'a', self.root/'b'
        for path in (self.a, self.b):
            path.mkdir()
            (path/'SKILL.md').write_text('# Teaching practice\nSee [shared](lib/guide.md)\n', encoding='utf-8')
        self.shared = self.root/'shared'
        self.shared.mkdir()
        (self.shared/'guide.md').write_text('# Feedback\nKnowledge for targeted practice.', encoding='utf-8')
        (self.shared/'.env').write_text('SECRET=do-not-read', encoding='utf-8')
        self.mounts = {'A': [], 'B': [{'mount': 'lib', 'path': str(self.shared)}]}
        self.workspace = Workspace()
        self.server = make_server(0, self.workspace)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.origin = f'http://127.0.0.1:{self.server.server_port}'

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def call(self, name, body=None, **headers):
        defaults = {'Origin': self.origin, 'X-Spark-Token': self.workspace.token, 'Content-Type': 'application/json'}
        defaults.update(headers)
        req = request.Request(self.origin+'/api/'+name, data=json.dumps(body).encode() if body is not None else None, headers=defaults)
        try:
            with request.urlopen(req, timeout=5) as response:
                return response.status, json.load(response)
        except error.HTTPError as exc:
            return exc.code, json.load(exc)
        except (ConnectionAbortedError, ConnectionResetError):
            # Windows：服务端对超限请求是「先回 413、再断开」，客户端还在写大 body 时
            # 就会收到 10053/10054，读不到那个 413。这里用 413 代替**仅限请求体本来就超限**的
            # 情形——超限正是这条路径要模拟的原因。
            # 小请求被重置是真故障，**不能**伪造 413：那会把基础设施抖动伪装成产品行为，
            # 表现为某条状态码断言在某次运行里莫名失败、换个顺序又好了。
            payload = json.dumps(body).encode() if body is not None else b''
            if len(payload) > MAX_BODY:
                return 413, {}
            raise


class AppIntegrationTests(LoopbackAppFixture):
    def test_a_reset_on_a_small_request_is_not_disguised_as_a_413(self):
        """这个 helper 曾经对**每一次**连接重置都伪造 413。

        大 body 超限时服务端确实是「先回 413 再断开」，客户端写侧只看到 10053/10054，
        所以需要一个 413 的替身。但小请求被重置是真故障，伪造 413 会把基础设施抖动
        伪装成产品行为——表现就是某条状态码断言偶发失败、换个运行顺序又变绿。
        """
        with patch.object(request, 'urlopen', side_effect=ConnectionResetError(10054, 'reset')):
            with self.assertRaises(ConnectionResetError):
                self.call('session')

    def test_the_oversize_simulation_still_covers_the_windows_write_side_reset(self):
        # 大 body 才是那条 413 替身存在的理由：inventory 的前置 Content-Length 检查
        # 在 Windows 上表现为「回 413 后立刻断开」，客户端读不到状态行。
        with patch.object(request, 'urlopen', side_effect=ConnectionResetError(10054, 'reset')):
            status, _ = self.call('inventory', {'a': 'x' * (MAX_BODY + 1), 'b': 'y'})
        self.assertEqual(status, 413)

    def test_http_exploration_is_explicit_and_does_not_skip_material_review(self):
        status, session = self.call('session')
        self.assertEqual(status, 200)
        self.assertTrue(session['capabilities']['exploration_without_goal'])
        with patch('app.fusion.engine.request_model') as model:
            for preference in [{}, {'goal': ''}, {'goal': ' \n\t'}, {'goal': None}, {'goal': '  Local tools only  '}]:
                with self.subTest(preference=preference):
                    status, report = self.call('inventory', {'a': str(self.a), 'b': str(self.b), **preference})
                    self.assertEqual(status, 200)
                    goal = (preference.get('goal') or '').strip()
                    self.assertEqual(report['goal'], goal)
                    self.assertEqual(report['intent_mode'], 'focused' if goal else 'explore')
                    self.assertIsNone(self.workspace.packet)
                    self.assertEqual(self.call('preview', {'packet_sha256': report['packet_sha256']})[0], 400)
        model.assert_not_called()

    def test_collision_documents_http_limits_and_review_gate(self):
        from .test_collision import selected_documents
        documents = selected_documents(first='# Large selected Skill\n' + '文' * 70000)
        status, report = self.call('documents', {'documents': documents, 'goal': 'Review selected sources'})
        self.assertEqual(status, 200)
        self.assertEqual(len(report['packages']['A']['files']), 1)
        self.assertEqual(self.call('preview', {'packet_sha256': report['packet_sha256']})[0], 400)
        self.assertEqual(self.call('documents', {'documents': selected_documents(), 'goal': 'task'}, **{'X-Spark-Token': 'wrong'})[0], 403)
        documents['A']['sha256'] = '0' * 64
        self.assertEqual(self.call('documents', {'documents': documents, 'goal': 'task'})[0], 400)

    def test_package_upload_http_is_bounded_authenticated_and_separate_from_model_sending(self):
        from .test_uploads import directory_upload, zip_upload
        body = {'uploads': {'A': directory_upload({'SKILL.md': b'# Synthetic A', 'notes.txt': b'x' * 140000}),
                            'B': zip_upload({'SKILL.md': b'# Synthetic B'})}}
        with patch('app.fusion.engine.request_model') as model:
            status, report = self.call('packages', body)
        self.assertEqual(status, 200)
        self.assertEqual(report['intent_mode'], 'explore')
        self.assertIsNone(self.workspace.packet)
        model.assert_not_called()
        self.assertTrue(self.call('session')[1]['capabilities']['package_uploads'])
        self.assertEqual(self.call('packages', {}, **{'X-Spark-Token': 'wrong'})[0], 403)
        self.assertEqual(self.call('inventory', body)[0], 413)

    def test_inventory_selection_and_source_review_revoke_old_packets(self):
        (self.a/'vendor.min.js').write_text('synthetic-library;'*15000, encoding='utf-8')
        status, inventory = self.call('inventory', {'a': str(self.a), 'b': str(self.b), 'goal': 'Explain a synthetic mistake'})
        self.assertEqual(status, 200)
        self.assertFalse(inventory['budget_ok'])
        self.assertEqual(self.call('preview', {'packet_sha256': inventory['packet_sha256']})[0], 400)
        selection = inventory['context_selection']
        selection['A'][1].update(include=False, reason='Keep dependency inventory without source body')
        status, selected = self.call('context', {'inventory_sha256': inventory['inventory_sha256'], 'selection': selection})
        self.assertEqual(status, 200)
        self.assertTrue(selected['budget_ok'])
        status, excerpt = self.call('source', {'inventory_sha256': inventory['inventory_sha256'], 'parent': 'A', 'path': 'vendor.min.js'})
        self.assertEqual(status, 200)
        self.assertEqual(excerpt['next_offset'], 12000)
        self.assertEqual(self.call('source', {'inventory_sha256': 'stale', 'parent': 'A', 'path': 'SKILL.md'})[0], 400)
        selection['A'][1]['include'] = True
        self.assertFalse(self.call('context', {'inventory_sha256': inventory['inventory_sha256'], 'selection': selection})[1]['budget_ok'])
        self.assertEqual(self.call('preview', {'packet_sha256': selected['packet_sha256']})[0], 400)

    def test_connection_probe_needs_cost_consent_and_never_sends_source(self):
        with provider({'ok': True}) as (endpoint, captured):
            body = {'endpoint': endpoint, 'model': 'synthetic', 'api_key': 'test-only'}
            self.assertEqual(self.call('preflight', body)[0], 400)
            self.assertEqual(captured, [])
            status, result = self.call('preflight', {**body, 'allow_billable_probe': True})
            self.assertEqual(status, 200)
            self.assertTrue(result['basic_json_ok'])
            self.assertFalse(result['source_sent'])
            self.assertEqual(len(captured), 1)
            # 2026-09-30：原本是 32，真实本机小模型连 {"ok":true} 都写不完就被截断，
            # 连接检查因此假失败。给足余量，且不发送任何材料。
            self.assertGreaterEqual(captured[0]['body']['max_tokens'], 256)
            self.assertNotIn('SKILL.md', json.dumps(captured[0]['body']))

    def test_failed_context_review_invalidates_previous_prepared_packet(self):
        body = {'a': str(self.a), 'b': str(self.b), 'goal': 'Review whole-file context'}
        _, inventory = self.call('inventory', body)
        review = {'inventory_sha256': inventory['inventory_sha256'], 'selection': inventory['context_selection']}
        _, prepared = self.call('context', review)
        self.assertIsNotNone(self.workspace.packet)
        review['selection']['A'][0]['reason'] = ''
        self.assertEqual(self.call('context', review)[0], 400)
        self.assertIsNone(self.workspace.packet)
        self.assertEqual(self.call('preview', {'packet_sha256': prepared['packet_sha256']})[0], 400)
    def prepared(self):
        status, data = self.call('prepare', {'a': str(self.a), 'b': str(self.b), 'goal': 'Explain practice with feedback', 'resources': self.mounts})
        self.assertEqual(status, 200, data)
        return data

    def await_job(self, job):
        deadline = time.monotonic()+5
        while time.monotonic()<deadline:
            status, data = self.call('job', {'id': job['id']})
            self.assertEqual(status, 200)
            if not data['transport_pending']:
                return data
            time.sleep(.02)
        self.fail('Job did not settle')

    def test_mounted_resources_have_full_text_fingerprints_and_resolved_links(self):
        packet = prepare(self.a, self.b, 'Teach practice', resources=self.mounts)
        verify_packet(packet)
        b = packet['packages']['B']
        self.assertEqual(b['references'][0]['status'], 'present')
        self.assertIn('Knowledge for targeted practice', messages(packet)[1]['content'])
        self.assertNotIn('do-not-read', messages(packet)[1]['content'])
        self.assertNotIn(str(self.shared), messages(packet)[1]['content'])
        self.assertEqual(next(f for f in b['files'] if f['path']=='lib/.env')['status'], 'excluded')

    def test_resource_collision_traversal_and_sensitive_mount_rejected(self):
        for mount, path in [('remote', '//server/share'), ('SKILL.md',self.shared/'guide.md'), ('../outside',self.shared), ('.env',self.shared/'guide.md'), ('guide.md',self.shared/'.env')]:
            with self.subTest(mount=mount), self.assertRaises(FusionError):
                snapshot(self.b, [{'mount':mount,'path':str(path)}])

    def test_relative_reference_in_mounted_directory_resolves_without_parent_walk(self):
        (self.shared/'guide.md').write_text('[entry](../SKILL.md)\n[absent](../../private.txt)', encoding='utf-8')
        package=snapshot(self.b,self.mounts['B'])
        statuses={r['target']:r['status'] for r in package['references']}
        self.assertEqual(statuses['../SKILL.md'],'present')
        self.assertEqual(statuses['../../private.txt'],'unresolved')

    def test_linked_resource_rejected(self):
        link=self.root/'link'
        try:
            link.symlink_to(self.shared,target_is_directory=True)
        except OSError:
            self.skipTest('OS does not permit symlink creation')
        with self.assertRaises(FusionError):
            snapshot(self.b,[{'mount':'lib','path':str(link)}])

    def test_session_and_same_origin_token_protection(self):
        self.assertEqual(self.call('session')[1]['service'],'spark-fusion')
        body={'a':str(self.a),'b':str(self.b),'goal':'Teach'}
        self.assertEqual(self.call('prepare',body,**{'Origin':'https://evil.invalid'})[0],403)
        self.assertEqual(self.call('prepare',body,**{'X-Spark-Token':'wrong'})[0],403)
        self.assertEqual(self.call('session',**{'Host':'evil.invalid'})[0],403)
        self.assertEqual(self.call('session',**{'Sec-Fetch-Site':'cross-site'})[0],403)

    def desktop(self, method, path, origin, **headers):
        """按桌面壳 WebView 的真实请求形态发一次（跨源 + cross-site 标记 + 预检）。"""
        req = request.Request(self.origin+path, method=method, headers={'Origin': origin, **headers})
        try:
            with request.urlopen(req, timeout=5) as response:
                return response.status, response.headers
        except error.HTTPError as exc:
            return exc.code, exc.headers

    def test_desktop_shell_origin_is_first_party_though_browser_calls_it_cross_site(self):
        # 壳内页面（tauri://localhost）取本机服务必然被浏览器判为 cross-site；
        # 它是壳自身界面而不是陌生站点，因此放行并回写 CORS 头。
        for origin in ['tauri://localhost','http://tauri.localhost']:
            with self.subTest(origin=origin):
                status, headers = self.desktop('GET','/api/session',origin,**{'Sec-Fetch-Site':'cross-site'})
                self.assertEqual(status, 200)
                self.assertEqual(headers['Access-Control-Allow-Origin'], origin)
        # 同样的 cross-site 标记换陌生来源仍然拒绝。
        self.assertEqual(self.desktop('GET','/api/session','https://evil.invalid',**{'Sec-Fetch-Site':'cross-site'})[0], 403)

    def test_desktop_preflight_allows_token_header_but_still_needs_a_shell_origin(self):
        preflight = {'Access-Control-Request-Method':'POST','Access-Control-Request-Headers':'content-type,x-spark-token',
                     'Sec-Fetch-Site':'cross-site'}
        status, headers = self.desktop('OPTIONS','/api/prepare','tauri://localhost',**preflight)
        self.assertEqual(status, 204)
        self.assertIn('X-Spark-Token', headers['Access-Control-Allow-Headers'])
        self.assertIn('POST', headers['Access-Control-Allow-Methods'])
        self.assertEqual(self.desktop('OPTIONS','/api/prepare','https://evil.invalid',**preflight)[0], 403)
        # 启动脚本的浏览器走同源，不需要也不应拿到 CORS 放行头。
        with request.urlopen(request.Request(self.origin+'/api/session', headers={'Origin': self.origin}), timeout=5) as response:
            self.assertIsNone(response.headers.get('Access-Control-Allow-Origin'))

    def test_private_files_are_not_served(self):
        for path in ['/app/fusion/server.py','/app/.fusion-runs/packet.json','/README.md','/../README.md']:
            with self.subTest(path=path),self.assertRaises(error.HTTPError) as raised:
                request.urlopen(self.origin+path)
            self.assertEqual(raised.exception.code,404)

    def test_preview_and_run_reject_stale_snapshot(self):
        self.prepared()
        for action in ['preview','run']:
            self.assertEqual(self.call(action,{'packet_sha256':'stale'})[0],400)

    def test_real_http_prepare_preview_model_and_result(self):
        p=self.prepared()
        status,wire=self.call('preview',{'packet_sha256':p['packet_sha256']})
        self.assertEqual(status,200)
        with provider(result=proposed(self.workspace.packet)) as (endpoint,captured):
            status,job=self.call('run',{'packet_sha256':p['packet_sha256'],'endpoint':endpoint,'model':'synthetic','api_key':'local-test-secret'})
            self.assertEqual(status,200)
            done=self.await_job(job)
        self.assertEqual(done['status'],'completed')
        self.assertFalse(done['result']['value_verified'])
        # **分段转正后**（2026-10-07）：每次模型请求仍以 preview wire 为底座，
        # 差别只是最后一条 user 消息追加了段指令——底座必须逐字不变。
        # 2026-10-12 起默认 FUSION_SPLIT=auto：第一次调用是分段探测（同样是
        # preview wire + 追加段指令），探测失败回退整份——调用仍 >1 次。
        self.assertGreater(len(captured),1,'auto 默认下一次运行至少有探测 + 正式调用')
        for captured_message,wire_message in zip(captured[0]['body']['messages'],wire['messages']):
            self.assertTrue(str(captured_message['content']).startswith(wire_message['content']),
                            '段请求必须以 preview wire 为底座，只许追加不许改写')
        self.assertNotIn('local-test-secret',json.dumps(done))

    def test_inventory_carries_the_capability_layer_that_the_ui_renders(self):
        # 能力层曾经只存在于 packet 里，没进 HTTP 投影——界面因此永远看不到结构落点。
        # 这类「契约对、投影漏」的接线问题，纯函数测试看不见。
        status,inventory=self.call('inventory',{'a':str(self.a),'b':str(self.b),'goal':'结构落点'})
        self.assertEqual(status,200)
        capability=inventory['capability']
        for side in ('A','B'):
            self.assertIn(side,capability)
            self.assertFalse(capability[side]['understood'],'结构层不得声称理解了语义')
        self.assertFalse(capability['bridge']['semantic_verified'])
        self.assertIn(capability['bridge']['status'],
                      ('no_structural_overlap','cross_domain_surface_found'))

    def test_run_result_carries_the_spark_reading_and_never_claims_verified(self):
        p=self.prepared()
        result=proposed(self.workspace.packet)
        with provider(result=result) as (endpoint,_):
            job=self.call('run',{'packet_sha256':p['packet_sha256'],'endpoint':endpoint,
                                 'model':'synthetic','api_key':'k'})[1]
            done=self.await_job(job)
        self.assertEqual(done['status'],'completed')
        spark=done['result']['spark']
        self.assertIn('spark',done['result'],'结果必须附带火花读数，否则界面无从呈现五类口径')
        self.assertFalse(spark['verified'],'未跑对照实验就不许声称已验证')
        self.assertIn(spark['candidates'][0]['claimed_class'], spark['classes'])
        for candidate in spark['candidates']:
            self.assertEqual(len(candidate['title']),len(candidate['title'].strip()))

    def test_degenerate_result_is_flagged_rather_than_passed_silently(self):
        # 新机制照抄父方贡献时，核对器必须报出来——这就是它存在的意义。
        # 注意：对照臂**不能**在这里做手脚，`RESULT_SCHEMA` 强制恰好 5 个基线，
        # 少给会被契约直接拒收；`missing_baselines` 是为契约将来放宽而留的防御。
        p=self.prepared()
        result=proposed(self.workspace.packet)
        for candidate in result['candidates']:
            candidate['new_mechanism']=candidate['contribution_a']['mechanism']
        with provider(result=result) as (endpoint,_):
            job=self.call('run',{'packet_sha256':p['packet_sha256'],'endpoint':endpoint,
                                 'model':'synthetic','api_key':'k'})[1]
            done=self.await_job(job)
        self.assertEqual(done['status'],'completed')
        codes={r['code'] for c in done['result']['spark']['candidates'] for r in c['risks']}
        self.assertIn('new_mechanism_near_a',codes)
        self.assertGreater(done['result']['spark']['candidates_with_risks'],0)

    def test_a_result_missing_a_required_baseline_is_rejected_not_downgraded(self):
        p=self.prepared()
        result=proposed(self.workspace.packet)
        for candidate in result['candidates']:
            candidate['evaluation']=[{'task':'t','baselines':['fusion'],'success_criterion':'s'}]
        with provider(result=result) as (endpoint,_):
            job=self.call('run',{'packet_sha256':p['packet_sha256'],'endpoint':endpoint,
                                 'model':'synthetic','api_key':'k'})[1]
            done=self.await_job(job)
        self.assertEqual(done['status'],'failed')
        self.assertIsNone(done['result'],'不合格的输出不得被收下')

    def test_cancellation_discards_late_result_and_duplicate_run_is_rejected(self):
        p=self.prepared()
        with provider(result=proposed(self.workspace.packet),delay=.3) as (endpoint,_):
            body={'packet_sha256':p['packet_sha256'],'endpoint':endpoint,'model':'synthetic'}
            _,job=self.call('run',body)
            self.assertEqual(self.call('run',body)[0],400)
            self.assertEqual(self.call('cancel',{'id':job['id']})[1]['status'],'cancelled')
            self.assertEqual(self.call('prepare',{'a':str(self.a),'b':str(self.b),'goal':'Other goal'})[0],400)
            done=self.await_job(job)
        self.assertEqual(done['status'],'cancelled')
        self.assertIsNone(done['result'])

    def test_client_request_id_recovers_lost_ack_without_second_model_call(self):
        p = self.prepared()
        request_id = 'a' * 32
        with provider(result=proposed(self.workspace.packet), delay=.1) as (endpoint, captured):
            body = {'packet_sha256': p['packet_sha256'], 'endpoint': endpoint,
                    'model': 'synthetic', 'request_id': request_id}
            self.assertEqual(self.call('run', body)[0], 200)  # Discard the ack.
            status, job = self.call('job', {'id': request_id})
            self.assertEqual(status, 200)
            self.assertEqual(self.call('run', body)[1]['id'], request_id)
            done = self.await_job(job)
            self.assertEqual(done['status'], 'completed')
            after_first_run = len(captured)
            status, recovered = self.call('run', body)
            self.assertEqual(status, 200)
            self.assertEqual(recovered['id'], request_id)
            self.assertEqual(recovered['status'], 'completed')
            # **分段转正后**一次运行是多段调用；「不重打模型」的语义不变：
            # 丢确认后的恢复调用不得新增任何模型流量。
            self.assertEqual(len(captured), after_first_run)
            self.assertEqual(self.call('run', {**body, 'model': 'other'})[0], 400)
            self.assertEqual(self.call('run', {**body, 'request_id': 'invalid'})[0], 400)

    def test_provider_failure_is_explicit_and_not_a_fallback_proposal(self):
        p=self.prepared()
        # 整份模式的显性失败语义用 FUSION_SPLIT=on 钉死（恰好一次调用）；
        # auto 模式的探测回退语义由下一条测试单独守住。
        with patch.dict('os.environ', {'FUSION_SPLIT': 'on'}):
            with provider(status=500) as (endpoint,captured):
                _,job=self.call('run',{'packet_sha256':p['packet_sha256'],'endpoint':endpoint,'model':'synthetic'})
                done=self.await_job(job)
        self.assertEqual(done['status'],'failed')
        self.assertIn('HTTP 500',done['error'])
        self.assertIsNone(done['result'])
        self.assertEqual(len(captured),1)

    def test_auto_mode_probes_then_falls_back_to_whole_on_probe_failure(self):
        # FUSION_SPLIT=auto（2026-10-12 默认）：探测失败 → 回退整份；
        # 显性失败语义不变——绝不因回退伪造结果。
        p=self.prepared()
        with provider(status=500) as (endpoint,captured):
            _,job=self.call('run',{'packet_sha256':p['packet_sha256'],'endpoint':endpoint,'model':'synthetic'})
            done=self.await_job(job)
        self.assertEqual(done['status'],'failed')
        self.assertIn('HTTP 500',done['error'])
        self.assertIsNone(done['result'],'探测失败回退整份也不能伪造结果')
        self.assertEqual(len(captured),2,'auto：先探测一段，失败后回退整份，恰好两次调用')
        self.assertIn('【分段模式',captured[0]['body']['messages'][-1]['content'],
                      '第一次调用是分段探测')
        self.assertNotIn('【分段模式',captured[1]['body']['messages'][-1]['content'],
                         '回退后走整份模式')

    def test_cli_explicit_resource_mount_round_trip(self):
        from .__main__ import main
        output=self.root/'packet.json'
        with redirect_stdout(io.StringIO()):
            code=main(['prepare','--a',str(self.a),'--b',str(self.b),'--goal','Teach practice',
                       '--resource-b','lib='+str(self.shared),'--out',str(output)])
        self.assertEqual(code,0)
        packet=verify_packet(json.loads(output.read_text(encoding='utf-8')))
        self.assertIn('lib/guide.md',[f['path'] for f in packet['packages']['B']['files']])

    def test_budget_is_enforced_before_any_model_request(self):
        body={'a':str(self.a),'b':str(self.b),'goal':'Teach','max_context_chars':1000}
        status, result=self.call('prepare',body)
        self.assertEqual(status,400)
        self.assertIn('Context budget exceeded',result['error'])
        self.assertIsNone(self.workspace.packet)
        body['max_context_chars']=160000
        self.assertEqual(self.call('prepare',body)[0],200)

    def test_bad_input_does_not_leak_filesystem_error(self):
        status,body=self.call('prepare',{'a':'private-missing-path','b':str(self.b),'goal':'Test'})
        self.assertEqual(status,400)
        self.assertNotIn('private-missing-path',body['error'])
        self.assertEqual(self.call('prepare',[])[0],400)


if __name__=='__main__':
    unittest.main()
