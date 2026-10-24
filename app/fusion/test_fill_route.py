"""补足闭环的服务端集成测试。

我写完 `fill` 路由后只验了纯函数层（拼提示词、校验返回），**没有验过服务端这条路**：
路由校验、后台线程、任务轮询、结果回填。这条测试把那段补上，用仓库现成的假模型夹具，
不产生任何真实调用。
"""
import time
import unittest

from app.fusion.packages import canonical
from app.fusion.server import Workspace, make_server
from app.fusion.test_fusion import proposed, provider
from app.fusion.test_server import LoopbackAppFixture


def fill_payload(candidate, ref, usable=True):
    return {
        'target_title': candidate['title'],
        'addressed': [{'gap': gap, 'resolution': '沿用贡献方已验证的做法',
                       'evidence': [ref]} for gap in candidate['gaps_to_fill']],
        'still_missing': [] if usable else [candidate['gaps_to_fill'][0]],
        'disposition': 'usable' if usable else 'still_blocked',
        'revised': {
            'new_mechanism': '为每条结论生成可回查的引用指针，并记录复检结果',
            'workflow': ['收集证据', '生成引用指针', '复检'],
            'artifacts': [{'path': 'scripts/ref.py', 'purpose': '生成引用指针'}],
            'retention': {'keep': ['逐条标注依据的做法'], 'discard': ['原域题库'],
                          'reason': '全量合并会稀释双方'},
        },
    }


class FillRouteTests(LoopbackAppFixture):
    """复用真实 server 夹具（`LoopbackAppFixture`），只补补足相关的用例。

    这里**只组合夹具、不继承** `AppIntegrationTests`——继承会把那套用例原样再跑一遍。
    """

    def prepare(self):
        _, inventory = self.call('inventory', {'a': str(self.a), 'b': str(self.b), 'goal': '补足缺口'})
        review = {'inventory_sha256': inventory['inventory_sha256'],
                  'selection': inventory['context_selection']}
        self.call('context', review)
        return inventory

    def evidence_ref(self, inventory, parent='A'):
        # inventory 是预览，正文已被裁掉；引用要指向本次真实发送的那份文件，
        # 所以 sha 取自清单、quote 取自磁盘首行，两者必须对得上。
        entry = next(f for f in inventory['packages'][parent]['files'] if f['status'] == 'text')
        source = (self.a if parent == 'A' else self.b) / entry['path']
        return {'parent': parent, 'path': entry['path'], 'sha256': entry['sha256'],
                'start_line': 1, 'end_line': 1,
                'quote': source.read_text(encoding='utf-8').splitlines()[0]}

    def run_first(self, inventory):
        result = proposed(self.workspace.packet)
        with provider(result) as (endpoint, _captured):
            _, job = self.call('run', {'packet_sha256': self.workspace.packet['sha256'],
                                       'endpoint': endpoint, 'model': 'synthetic', 'api_key': 'k'})
            self.wait_job(job['id'])
        return result

    def wait_job(self, job_id, tries=60):
        for _ in range(tries):
            status, state = self.call('job', {'id': job_id})
            if state.get('status') != 'running':
                return state
            time.sleep(0.05)
        self.fail('补足任务没有在预期时间内结束')

    def test_filling_a_candidate_without_declared_gaps_is_refused(self):
        inventory = self.prepare()
        result = self.run_first(inventory)
        # 造一个没有声明缺口的候选：必须被挡住，不允许白烧一次模型请求
        result['candidates'][0]['gaps_to_fill'] = []
        self.workspace.last_result = {'result': result}
        with provider() as (endpoint, captured):
            status, body = self.call('fill', {'title': result['candidates'][0]['title'],
                                              'endpoint': endpoint, 'model': 'synthetic', 'api_key': 'k'})
        self.assertEqual(status, 400)
        self.assertEqual(captured, [], '被拒绝的补足不得发出任何请求')

    def test_fill_round_trip_returns_a_reassessed_but_unverified_report(self):
        inventory = self.prepare()
        result = self.run_first(inventory)
        target = result['candidates'][0]
        self.workspace.last_result = {'result': result}
        with provider(fill_payload(target, self.evidence_ref(inventory))) as (endpoint, captured):
            status, job = self.call('fill', {'title': target['title'], 'packet_sha256': self.workspace.packet['sha256'],
                                             'endpoint': endpoint, 'model': 'synthetic', 'api_key': 'k'})
            self.assertEqual(status, 200)
            state = self.wait_job(job['id'])
        self.assertEqual(state['status'], 'completed', state.get('error'))
        report = state['result']
        self.assertTrue(report['usable_claimed'])
        self.assertFalse(report['verified'], '补完不等于验证过')
        self.assertEqual(report['still_missing'], [])
        # 第二次请求必须带上冻结材料与该候选自己声明的缺口清单
        body = canonical(captured[0]['body'])
        self.assertIn('fill_declared_gaps', body)
        for gap in target['gaps_to_fill']:
            self.assertIn(gap, body, f'缺口 {gap!r} 必须原样出现在第二轮请求里')

    def test_an_unresolved_gap_keeps_the_candidate_in_the_fifth_class(self):
        inventory = self.prepare()
        result = self.run_first(inventory)
        target = result['candidates'][0]
        self.workspace.last_result = {'result': result}
        with provider(fill_payload(target, self.evidence_ref(inventory), usable=False)) as (endpoint, _c):
            _, job = self.call('fill', {'title': target['title'], 'packet_sha256': self.workspace.packet['sha256'],
                                        'endpoint': endpoint, 'model': 'synthetic', 'api_key': 'k'})
            state = self.wait_job(job['id'])
        self.assertFalse(state['result']['usable_claimed'])
        self.assertEqual(state['result']['reassessment']['claimed_class'], 'needs_ai_fill')

    def test_filling_an_unknown_candidate_is_refused(self):
        inventory = self.prepare()
        self.run_first(inventory)
        with provider() as (endpoint, captured):
            status, _ = self.call('fill', {'title': '不存在的方案', 'packet_sha256': self.workspace.packet['sha256'],
                                           'endpoint': endpoint, 'model': 'synthetic', 'api_key': 'k'})
        self.assertEqual(status, 400)
        self.assertEqual(captured, [])


if __name__ == '__main__':
    unittest.main()
