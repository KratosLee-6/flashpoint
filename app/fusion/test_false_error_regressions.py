"""「五个假网络错误」修复的强制回归（10-21 清单第 2 项）。

    python -m unittest app.fusion.test_false_error_regressions

## 守什么（2026-10-21 实测挖出的 5 个真 bug，共同形状：真因被通用文案盖住）

1. **P0 探测消费临时密钥**：preflight 分支清了 transientKey → 表单临时密钥的主路径
   从未通过过。守卫：preflight 分支内不得出现 `transientKey=''`（run 分支允许——
   发送后清空是设计）。
2. **P0 业务错误伪装成网络错误**：无 e.status 的业务异常（如「请填写密钥」）被引导进
   「发送回执未收到」找回路径。守卫：只有 AbortError/TypeError 才进找回路径；
   业务错误必须清 pending 并原样显示。
3. **P1 服务端盖掉异常原文**：`_run` 把非 FusionError 换成一句英文。守卫：error 里必须
   保留异常类型与原文（`_exc_label` 拼接形状）。
4. **P1 超时写死 300 秒**：慢模型（MiniMax 165K 上下文实测 367s）必然超时。
   守卫：run 与 materialize 两处的 timeout 都读 `FUSION_TIMEOUT`（默认 600）。
5. **P1 物化交付不全照报成功**：声明 8 产物只落 6 个 → CHECK 0/0 → 修复回路不启动
   → completed。守卫（行为测试）：缺产物必须触发补齐回路、补不上必须
   `integrity_ok=False` 并如实报 `artifacts_missing`。

## 反向验证

把 fusion-ui.js preflight 分支的注释删掉并恢复 `transientKey=''`、或把 catch 改回
`if(e.status)`、或把 server 的 FUSION_TIMEOUT 改回字面量 300、或删掉 materialize 的
`missing = required - set(files)` 行——下面各条分别变红。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
UI = (ROOT / 'app' / 'fusion-ui.js').read_text(encoding='utf-8')
SERVER = (ROOT / 'app' / 'fusion' / 'server.py').read_text(encoding='utf-8')


def _branch(source: str, start: str, end: str) -> str:
    """取 `start` 分支到 `end`（下一个同级分支）之间的源码段。"""
    i = source.find(start)
    assert i >= 0, start
    j = source.find(end, i)
    return source[i:j if j > 0 else len(source)]


class PreflightKeepsTransientKey(unittest.TestCase):
    def test_preflight_branch_does_not_clear_transient_key(self):
        branch = _branch(UI, "name==='preflight'", "}else if(name==='preview'")
        self.assertNotIn("transientKey=''", branch,
                         '探测不得消费临时密钥——表单临时密钥的主路径会必然失败')


class AbortOnlyEntersRecovery(unittest.TestCase):
    def test_only_abort_or_typeerror_goes_to_job_recovery(self):
        self.assertIn("e.name==='AbortError'||e.name==='TypeError'", UI,
                      '只有网络中断/超时才走「按编号找回」路径')
        # 业务错误分支必须清 pending 并原样显示（不许吞进找回路径）
        tail = UI[UI.find("e.name==='AbortError'"):][:400]
        self.assertIn('state.pending=false', tail,
                      '业务错误必须清 pending，否则表单卡死到刷新（10-12 的老坑会复发）')


class ServerKeepsRealCause(unittest.TestCase):
    def test_run_failure_keeps_exception_type_and_text(self):
        self.assertIn("_exc_label(exc)", SERVER,
                      '_run 失败必须保留异常类型+原文（_exc_label 拼接），不许换成通用英文')

    def test_timeouts_are_configurable_via_env(self):
        self.assertEqual(SERVER.count("os.environ.get('FUSION_TIMEOUT', '600')"), 2,
                         'run 与 materialize 两处超时都必须读 FUSION_TIMEOUT（默认 600）')


class DeliveryCompletenessGate(unittest.TestCase):
    """行为测试：缺产物 → 补齐回路启动 → 补不上 → integrity_ok=False 如实上报。"""

    RESULT = {'goal': 'g', 'result': {'profiles': [], 'candidates': [{
        'orientation': 'B', 'title': 't', 'problem': 'p', 'new_mechanism': 'm',
        'workflow': ['s1'], 'artifacts': [{'path': 'qc.py', 'purpose': 'u'},
                                          {'path': 'ledger.md', 'purpose': 'u'}],
        'evaluation': [{'task': 't', 'success_criterion': 'c'}],
        'gaps_to_fill': [], 'expected_outcome': None}]}}

    def test_missing_artifacts_trigger_fill_then_report_integrity_failure(self):
        from app.fusion.materialize import materialize_candidate
        import tempfile

        def fake_generate(plan, allowed, **kw):
            # 第一轮只交出 qc.py（缺 ledger.md 且无 selftest）；补齐轮仍交不出
            return {'qc.py': 'x = 1\n'}, {'calls': 1, 'retries': 0, 'blocks': [],
                                         'path_pinned': 0}

        with tempfile.TemporaryDirectory() as tmp:
            with patch('app.fusion.materialize.generate_files', fake_generate):
                report = materialize_candidate(self.RESULT, 'B', Path(tmp) / 'pkg',
                                               endpoint='http://m', model='m',
                                               api_key='k', fill_rounds=1)
        self.assertFalse(report['integrity_ok'], '交付不全不得报 integrity_ok')
        self.assertIn('ledger.md', report['artifacts_missing'])
        self.assertTrue(report['fill_rounds'], '缺产物必须触发补齐回路（至少尝试一轮）')
        self.assertFalse(report.get('runnable'), '交付不全不得判可运行')

    def test_gate_passes_when_all_declared_artifacts_land(self):
        from app.fusion.materialize import materialize_candidate
        import tempfile

        def fake_generate(plan, allowed, **kw):
            return {'qc.py': 'x = 1\n', 'ledger.md': '# l\n',
                    'selftest.py': 'print("[CHECK] PASS ok")\n'}, \
                   {'calls': 2, 'retries': 0, 'blocks': [], 'path_pinned': 0}

        with tempfile.TemporaryDirectory() as tmp:
            with patch('app.fusion.materialize.generate_files', fake_generate):
                report = materialize_candidate(self.RESULT, 'B', Path(tmp) / 'pkg',
                                               endpoint='http://m', model='m',
                                               api_key='k', fill_rounds=0)
        self.assertTrue(report['integrity_ok'])
        self.assertEqual(report['artifacts_missing'], [])
        self.assertTrue(report['runnable'], '交齐且 selftest 过 → 可运行')


if __name__ == '__main__':
    unittest.main()
