"""快速碰撞（auto_select）的路由集成测试——真实 server + LoopbackAppFixture。

    python -m pytest app/fusion/test_quick_mode.py -q

## 守什么

简单模式的后端语义：`inventory` 带 `auto_select: true` 时，服务端按
「SKILL.md 必留 + 体积升序装满预算」自动精简并正式重签，返回的 preview
必须落在预算内、verify 过——快速模式的「自动」不允许变成「匿名」。
"""
from __future__ import annotations

import unittest

from .test_server import LoopbackAppFixture


class QuickModeRouteTests(LoopbackAppFixture):
    def test_auto_select_returns_budget_ok_and_verify_passes(self):
        status, report = self.call('inventory', {
            'a': str(self.a), 'b': str(self.b), 'goal': '快速碰撞',
            'auto_select': True, 'lean_budget': 3000})
        self.assertEqual(status, 200)
        self.assertTrue(report['budget_ok'], '自动精简后必须落在预算内')
        # preview 返回的 packet_sha256 必须是重签后的——run 将直接用它
        self.assertTrue(report['packet_sha256'])
        from .engine import verify_packet
        verify_packet(self.workspace.packet)      # select_context 正式重签的包必须过 verify

    def test_auto_select_false_keeps_expert_flow(self):
        status, report = self.call('inventory', {
            'a': str(self.a), 'b': str(self.b), 'goal': '专家流程'})
        self.assertEqual(status, 200)
        # 不带 auto_select：packet 置 None 是**设计**——专家必须经 context 层
        # 逐文件审查后 packet 才定稿；快速模式是唯一越过该层的通道。
        self.assertIsNone(self.workspace.packet)
        selection = {}
        for parent, rows in (report.get('context_selection') or {}).items():
            selection[parent] = [{'path': r['path'], 'include': r['include'],
                                  'role': r['role'],
                                  'classification_reason': r['classification_reason'],
                                  'reason': r['reason']} for r in rows]
        status, _ = self.call('context', {
            'inventory_sha256': report['inventory_sha256'], 'selection': selection})
        self.assertEqual(status, 200)
        self.assertIsNotNone(self.workspace.packet,
                             'context 审查后 packet 才定稿（专家语义不变）')

    def test_prepare_auto_select_sets_packet_for_run(self):
        status, report = self.call('prepare', {
            'a': str(self.a), 'b': str(self.b), 'goal': '快速碰撞',
            'auto_select': True, 'lean_budget': 3000})
        self.assertEqual(status, 200)
        self.assertIsNotNone(self.workspace.packet)
        status, preview_body = self.call('preview', {
            'packet_sha256': self.workspace.packet['sha256']})
        self.assertEqual(status, 200)


if __name__ == '__main__':
    unittest.main()
