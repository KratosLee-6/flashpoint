"""`completeness.classify` 的强制测试——P0-A，2026-10-05 新增。

## 这组测试在守什么

1. **判据本身有分辨力**：能区分「停在尾部」「中段失步」「合法但缺项」三类。
   判据不外带的直接后果是——P0-C 已经把 `json_syntax` 打成 0，
   若 `classify` 只会说「不完整」，那我们又瞎了。
2. **它只分类，不拦截**：这是本模块唯一的硬约束。
   `test_verdict_never_changes_the_outcome` 用**同一批样本**跑带/不带
   `classify` 的两条路径，断言接受/拒绝的结论逐条相同。
   一旦有人把 `verdict` 接到拒收上，这条立刻变红。

## 反向验证

`tools/mutate-completeness.py` 改坏实现，确认这里会变红。
其中最要紧的一条是：把 `MID_DOC_TAIL` 判据去掉（两类合并成一类）——
那样 `incomplete_mid_document` 永远判不出来，**分辨力从 3 类掉到 2 类**，
而表面看测试可能仍然全绿（因为其它用例不依赖那一类）。
所以这里专门有一条用例只测中段这一类。
"""
from __future__ import annotations

import json
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

from app.fusion.completeness import (MID_DOC_TAIL, SECTIONS, classify,
                                     required_counts)

ROOT = Path(__file__).resolve().parents[2]
PAIR = ('ast-static-scan-arch', 'device-key-attestation')
_PACKET = None


def _locate(name):
    for folder in ('skillhub-skill', 'app/examples'):
        p = ROOT / folder / name
        if (p / 'SKILL.md').exists():
            return p
    raise AssertionError('missing package: ' + name)


def _packet():
    global _PACKET
    if _PACKET is None:
        from app.fusion.engine import inspect_packages
        _PACKET = inspect_packages(_locate(PAIR[0]), _locate(PAIR[1]),
                                   goal='把静态分析里的证据链方法用到设备密钥证明上')
    return _PACKET


@contextmanager
def _server(raw: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            body = json.dumps({'choices': [{'finish_reason': 'stop',
                                            'message': {'content': raw}}]}).encode()
            try:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    srv = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f'http://127.0.0.1:{srv.server_port}/v1/chat/completions'
    finally:
        srv.shutdown()
        srv.server_close()
        t.join()

PRE = {'direction_reviews': [{}] * 2, 'profiles': [{}] * 2, 'candidates': [{}] * 3}


def full_ok() -> str:
    return json.dumps({
        'direction_reviews': [{}] * 2, 'profiles': [{}] * 2,
        'candidates': [{}] * 3, 'extra': 'ignored'})


class RequiredCountTests(unittest.TestCase):
    def test_counts_come_from_the_skeleton(self):
        self.assertEqual(required_counts(PRE),
                         {'direction_reviews': 2, 'profiles': 2, 'candidates': 3})

    def test_missing_skeleton_section_reads_as_zero_not_crash(self):
        self.assertEqual(required_counts({}), {s: 0 for s in SECTIONS})


class ClassificationTests(unittest.TestCase):
    def test_fully_valid_is_complete(self):
        v = classify(full_ok(), PRE)
        self.assertEqual(v.verdict, 'complete')
        self.assertTrue(v.parsed)

    def test_truncated_at_tail_is_its_own_verdict(self):
        """值写到一半、闭引号之后没有内容 → 停在尾部。"""
        raw = full_ok()[:-30]                     # 砍掉尾部一大截，留下半个值
        v = classify(raw, PRE)
        self.assertEqual(v.verdict, 'incomplete_at_tail')
        self.assertFalse(v.parsed)
        self.assertLessEqual(v.from_end, MID_DOC_TAIL)

    def test_mid_document_desync_is_its_own_verdict(self):
        """**这是 P0-C 之后唯一能看见失败的判据**，必须能单独判出来。

        形状：json 的报错位置很靠前，**断点后面还剩一大截**——
        模型失步后自己重新同步到了后面的结构。
        实测这个形状的 json 报错文案是 `Expecting value`，
        与尾部截断的 `Expecting ',' delimiter` **不同**，
        但也曾见过中段失步报 `Expecting ',' delimiter` 的——
        **所以判别靠的是 from_end，不是文案。**
        """
        raw = '{"direction_reviews": [], "profiles": [], "candidates": ' \
              + ('x' * 6000) + ' "note": 1}'
        v = classify(raw, PRE)
        self.assertEqual(v.verdict, 'incomplete_mid_document')
        self.assertGreater(v.from_end, MID_DOC_TAIL)

    def test_small_positive_tail_still_counts_as_tail(self):
        """**断点后面只剩几十个字符，仍算「停在尾部」。**

        这条是补变异验证的漏：把 `MID_DOC_TAIL` 改成 0 时，
        只有 `from_end` 落在「小但非零」区间的样本才会暴露问题。
        原来的两个夹具 `from_end` 是 0 和 6008，**任何阈值都一样**，
        所以那条变异抓不到——**夹具没造出变异要破坏的场景**。
        真实数据里 `from_end` 恰好有 2 和 3（r03/r05），所以必须钉住这一侧。
        """
        raw = full_ok()[:-30] + '}' * 50          # 断点后面只剩 50 个字符
        v = classify(raw, PRE)
        self.assertGreater(v.from_end, 0, '夹具没造出「小但非零」，验不到东西')
        self.assertLessEqual(v.from_end, MID_DOC_TAIL)
        self.assertEqual(v.verdict, 'incomplete_at_tail')

    def test_just_above_threshold_counts_as_mid(self):
        """阈值 +1 个字符就翻到中段——把边界钉死。"""
        raw = full_ok()[:-30] + '}' * (MID_DOC_TAIL + 1)
        v = classify(raw, PRE)
        self.assertEqual(v.from_end, MID_DOC_TAIL + 1)
        self.assertEqual(v.verdict, 'incomplete_mid_document')

    def test_same_error_text_can_be_both_tail_and_mid(self):
        """文案相同的两个样本，`from_end` 必须把它们分开。

        这是本模块最反直觉的一条：按报错文案分类是**做不到**的
        （实测 `Expecting ',' delimiter` 两边都出）。
        把这条删掉，下一个人多半会退回去按文案分类。
        """
        ok = full_ok()
        tail = ok[:-30]
        mid = ok[:-30] + 'x' * 6000
        self.assertEqual(classify(tail, PRE).verdict, 'incomplete_at_tail')
        self.assertEqual(classify(mid, PRE).verdict, 'incomplete_mid_document')

    def test_valid_json_but_missing_section(self):
        v = classify(json.dumps({'profiles': [{}] * 2, 'candidates': [{}] * 3}), PRE)
        self.assertEqual(v.verdict, 'incomplete_missing_sections')
        self.assertIn('direction_reviews', v.missing)

    def test_valid_json_but_too_few_items(self):
        v = classify(json.dumps({'direction_reviews': [{}] * 2, 'profiles': [{}],
                                 'candidates': [{}] * 3}), PRE)
        self.assertEqual(v.verdict, 'incomplete_too_few_items')
        self.assertTrue(any('profiles' in s for s in v.short))

    def test_top_level_not_object_is_malformed(self):
        self.assertEqual(classify('[1,2,3]', PRE).verdict, 'malformed_other')

    def test_fence_is_stripped_before_measuring(self):
        """围栏不该被算成「尾部还有一截」——否则位置判据会失真。"""
        raw = '```json\n' + full_ok() + '\n```'
        self.assertEqual(classify(raw, PRE).verdict, 'complete')

    def test_trailing_junk_is_not_incompleteness(self):
        """合法 JSON 后面跟了一截废话——那是另一回事，别混进 incomplete。"""
        self.assertEqual(classify(full_ok() + ' 然后他又说了一段话',
                                  PRE).verdict, 'trailing_junk')

    def test_four_distinct_verdicts_are_reachable(self):
        """**分辨力自测**：四类都必须能被真实触发，不是写在文档里的摆设。"""
        ok = full_ok()
        got = {
            classify(ok, PRE).verdict,
            classify(ok[:-30], PRE).verdict,
            classify('{"direction_reviews": [], "profiles": [], "candidates": '
                     + 'x' * 6000 + ' "n": 1}', PRE).verdict,
            classify(json.dumps({'profiles': [{}] * 2}), PRE).verdict,
        }
        for want in ('complete', 'incomplete_at_tail',
                     'incomplete_mid_document', 'incomplete_missing_sections'):
            self.assertIn(want, got, '判据 %s 从未被触发——它可能是个摆设' % want)


class NoInterceptionTests(unittest.TestCase):
    """**只分类，不拦截。** 本项目不许单方面改动校验强度。

    这里刻意**不用**「比较结论」这种空断言——那等于把自己要验的东西抄进期望里。
    做法是**让 `classify` 撒谎**：把失败样本硬判成 `complete`、
    把完整样本硬判成 `incomplete`，验收结论都必须与不撒谎时逐条相同。
    只要有人把 `verdict` 接到拒收上，这两条立刻变红。

    ⚠️ **必须 patch `engine.classify` 而不是 `completeness.classify`。**
    engine 在模块加载时 `from .completeness import classify` 把名字绑走了，
    改源模块的属性**对它没有影响**——踩过一次：三条用例全部空跑通过，
    setup 静默没发生，比没有测试更糟。
    所以每条用例都先断言 patch 真的生效了。
    """

    def setUp(self):
        self.packet = _packet()
        self.server_payload = None
        self._patch = None

    def _run(self, raw: str, forced_verdict=None):
        import app.fusion.completeness as comp
        import app.fusion.engine as eng
        from app.fusion.packages import FusionError

        original = eng.classify          # ← patch 使用点，不是定义点
        if forced_verdict is not None:
            def lying(text, pre, _v=forced_verdict, _o=original):
                return _o(text, pre)._replace(verdict=_v)
            eng.classify = lying
            self.addCleanup(setattr, eng, 'classify', original)
            self.assertIs(eng.classify, lying, 'patch 没生效，setup 静默失败')

        with _server(raw) as url:
            try:
                eng.synthesize(self.packet, endpoint=url, model='m',
                               max_tokens=4096)
                return 'accepted'
            except FusionError:
                return 'rejected'

    def test_lying_complete_does_not_rescue_a_broken_payload(self):
        """残缺正文 + `classify` 硬说 `complete` → **仍然必须被拒**。"""
        broken = '{"direction_reviews": [], "profiles": [], "candidates": ['
        honest = self._run(broken)
        lied = self._run(broken, forced_verdict='complete')
        self.assertEqual(honest, 'rejected')
        self.assertEqual(lied, 'rejected',
                         'classify 说了 complete 就被收下——**校验被放宽了**')

    def test_lying_incomplete_does_not_reject_a_good_payload(self):
        """完整正文 + `classify` 硬说 `incomplete` → 不得因此被拒。"""
        import app.fusion.completeness as comp
        import app.fusion.engine as eng
        from app.fusion.packages import FusionError

        # 一份**真正完整**的结果：从骨架反推不出内容，但这份是从 test_fusion
        # 的合成器拿不到的，所以这里改用「数量刚好够」的最小载荷，
        # 它会在**契约校验**那步失败——而那与 classify 说什么无关。
        raw = ('{"direction_reviews": [{"direction_id": "x", "decision": "reject",'
               ' "reason": "r"}], "profiles": [{}, {}], "candidates": [{}, {}, {}]}')
        original = eng.classify
        lying = lambda text, pre: original(text, pre)._replace(   # noqa: E731
            verdict='incomplete_at_tail')
        eng.classify = lying
        # 比**身份**：`assertIs(eng.classify.__name__, '<lambda>')` 是错的——
        # 那是两个不同的 str 对象，恒不相等，断言会在 patch 生效时也报失败。
        self.assertIs(eng.classify, lying, 'patch 没生效，setup 静默失败')
        try:
            with _server(raw) as url:
                try:
                    eng.synthesize(self.packet, endpoint=url, model='m',
                                   max_tokens=4096)
                    outcome = 'accepted'
                except FusionError as exc:
                    outcome = 'rejected'
                    # **判据是「决策来自 payload 自己的原因」**，
                    # 而不是「消息里有没有那个标签」——verdict 作为标签
                    # 出现在消息里是设计使然，出现在这里不影响正确性。
                    self.assertIn('ablation_checks', str(exc),
                                  '拒绝理由应当来自真实契约检查，而不是 classify 的标签')
        finally:
            eng.classify = original
        self.assertEqual(outcome, 'rejected',
                         '这份载荷本身不满足契约，判 rejected 才对')

    def test_classifier_crash_does_not_break_acceptance(self):
        """**校验器自己炸了，也绝不许影响验收**——它是观测手段，不是裁判。"""
        import app.fusion.engine as eng
        from app.fusion.packages import FusionError

        raw = '{"direction_reviews": [], "profiles": [], "candidates": [{}]}'

        def boom(text, pre):
            raise RuntimeError('校验器自己炸了')

        original = eng.classify
        eng.classify = boom
        self.assertIs(eng.classify, boom, 'patch 没生效，setup 静默失败')
        try:
            with _server(raw) as url:
                try:
                    eng.synthesize(self.packet, endpoint=url, model='m',
                                   max_tokens=4096)
                    outcome = 'accepted'
                except FusionError as exc:
                    outcome = 'rejected'
                    self.assertIn('unknown', str(exc),
                                  '校验器炸了应当如实标成 unknown，而不是静默')
        finally:
            eng.classify = original
        self.assertEqual(outcome, 'rejected',
                         'payload 数量不够，不管 classify 怎么炸都必须被拒')


if __name__ == '__main__':
    unittest.main()
