"""语义分段（`split_segments`）的强制测试——2026-10-07 新增，默认关闭。

## 这组测试在守什么

28 号文 §三 的合取结构改造：整份结果拆成自洽块逐段生成、**逐段本地确定性
校验**、只重试失败的段。2026-10-06 那次分段实验（`seg-retry.txt`，accepted 0/3）
失败收敛到两条形状——段缺必填字段、段数组长度不合法——根因是当时段级指令
没写清要求、段级判据只看「解析成功」。本模块的守卫逐条对着这两条根因：

- `test_segment_directive_names_required_fields_and_bounds`：段级指令**必须**
  含必填字段与数组边界，且从 schema 生成（契约加了字段它自动出现）；
- `test_shape_issues_agree_with_check_shape`：累积版形状校验与
  `contract.check_shape` 在变异样本上逐一吻合（防两套实现漂移）；
- 执行型 happy path：五段各自过段级校验 → 拼装 → **真实** validate_result
  通过（成功路径必须有走通的测试，只测拒绝路径的门禁无法区分「正确」与「损坏」）；
- 重试路径：段缺 `problem` → 重试请求**必须带着具体指控** → 重试后整体 accepted；
- 不兜底：重试耗尽必须报错，**绝不编造或放行**。

## 反向验证

把 `engine._synthesize_segmented` 里的段级校验删掉（`issues = []`）、
或把重试改成不带 `retry_issues`、或把最终 `validate_result` 换成空操作——
下面各条会分别变红。
"""
from __future__ import annotations

import json
import re
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

from app.fusion import engine
from app.fusion.contract import check_shape, wire_item_schema
from app.fusion.engine import inspect_packages
from app.fusion.packages import FusionError
from app.fusion.segments import (blocks, directive, segment_issues, shape_issues,
                                 summarize, take_segment)
from app.fusion.context import context_gaps

ROOT = Path(__file__).resolve().parents[2]
PAIR = ('ast-static-scan-arch', 'device-key-attestation')
_TAG = re.compile(r'【分段模式 · ([^\】]+)】')

_PACKET = None


def _packet():
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


def _ref(packet, parent):
    """evidence 槽：引第一行原文。**不带 sha256**——那是本地填的（wire 契约）。"""
    f = next(f for f in packet['packages'][parent]['files'] if f['path'] == 'SKILL.md')
    return {'parent': parent, 'path': 'SKILL.md', 'start_line': 1, 'end_line': 1,
            'quote': f['text'].splitlines()[0]}


def _segment_fixtures(packet):
    """每个 tag 一份**能过段级校验**的段产物（从真实包 + 骨架钉死值生成）。

    数值内容沿用 test_fusion.proposed 的形状：blocked + needs_ai_fill 的自洽
    认领、五基线、双消融。它必须能走过段级校验**和**最终契约。
    """
    pre = engine.skeleton(packet)
    out = {}
    for profile in pre['profiles']:
        parent = profile['parent']
        out['profiles:%s' % parent] = {
            'parent': parent,
            'capabilities': [{'claim': 'Declared teaching capability', 'basis': 'declared',
                              'evidence': [_ref(packet, parent)]}],
            'retention': {'keep': ['The feedback method that carries the claim'],
                          'discard': ['The task list, which belongs to the other domain'],
                          'reason': 'Keeping both packages whole diluted each contribution'},
            'dependencies': ['Domain resources'],
            'missing': ['Question bank'] + context_gaps(packet)[parent],
            'coverage': 'partial'}
    for template in pre['candidates']:
        orientation = template['orientation']
        anchor = template.get('direction_id') or 'independent'
        out['candidates:%s' % orientation] = {
            'orientation': orientation, 'direction_id': anchor, 'disposition': 'blocked',
            'title': '%s sourced-retest planner' % orientation,
            'expected_outcome': {'outcome_class': 'needs_ai_fill',
                                 'basis': 'The task cannot run until the missing question bank exists'},
            'gaps_to_fill': ['Question bank for the target domain'],
            'problem': 'Understand an error through an annotated diagram',
            'contribution_a': {'kind': 'way_of_working',
                               'mechanism': 'Source-checked constraint list',
                               'evidence': [_ref(packet, 'A')]},
            'contribution_b': {'kind': 'way_of_working',
                               'mechanism': 'Stepwise retest with records',
                               'evidence': [_ref(packet, 'B')]},
            'new_mechanism': 'Map error type to diagram annotation and targeted retest',
            'workflow': ['Classify sourced errors', 'Choose diagram feedback', 'Retest'],
            'artifacts': [{'path': 'scripts/feedback_%s.py' % orientation.lower(),
                           'purpose': 'Map errors to a feedback strategy'}],
            'ablation_checks': [{'removed_parent': parent,
                                 'task': 'Explain and retest a mistake',
                                 'expected_loss': 'Lose the corresponding sourced constraint'}
                                for parent in 'AB'],
            'blockers': ['Missing question bank'],
            'rationale': 'Requires resources before implementation',
            'evaluation': [{'task': 'Explain and retest a synthetic mistake',
                            'baselines': ['A_only', 'B_only', 'concatenation',
                                          'fusion', 'model_only'],
                            'success_criterion': 'Correct sourced explanation without unsupported claims'}]}
    reviews = pre['direction_reviews']
    if reviews:
        out['direction_reviews:all'] = [
            {'direction_id': r['direction_id'], 'decision': 'reject',
             'reason': 'Synthetic review: prefer independent proposal'}
            for r in reviews]
    return out


@contextmanager
def server_for(respond):
    """respond(tag, body) → dict 或 None（None = 返回缺字段的坏段由测试预置）。"""
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            marker = _TAG.search(body['messages'][-1]['content'])
            seen.append({'tag': marker.group(1) if marker else None, 'body': body})
            payload = respond(seen[-1]['tag'], body)
            data = json.dumps({'choices': [{'finish_reason': 'stop',
                                            'message': {'content': payload}}]}).encode()
            try:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

    srv = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield 'http://127.0.0.1:%d/v1/chat/completions' % srv.server_port, seen
    finally:
        srv.shutdown()
        srv.server_close()
        t.join()


def _payload_for(fixtures, tag, key):
    return json.dumps({key: [fixtures[tag]] if key != 'direction_reviews' else fixtures[tag]},
                      ensure_ascii=False)


class SegmentPlanTests(unittest.TestCase):
    """块划分与指令生成——全部零调用。"""

    def test_blocks_cover_every_template_in_order(self):
        packet = _packet()
        pre = engine.skeleton(packet)
        plan = blocks(packet)
        self.assertEqual([s['key'] for s in plan],
                         ['profiles'] * 2
                         + (['direction_reviews'] if pre['direction_reviews'] else [])
                         + ['candidates'] * 3)
        self.assertEqual([s['tag'] for s in plan if s['key'] == 'candidates'],
                         ['candidates:A', 'candidates:B', 'candidates:C'])
        for seg in plan:
            self.assertEqual(seg['count'], 1 if seg['key'] != 'direction_reviews'
                             else len(pre['direction_reviews']))

    def test_segment_directive_names_required_fields_and_bounds(self):
        """2026-10-06 那次分段失败的根因是「指令没写清要哪些字段」——这条守卫钉死它。"""
        packet = _packet()
        seg = next(s for s in blocks(packet) if s['tag'] == 'candidates:C')
        text = directive(packet, seg)
        for field in wire_item_schema('candidates')['required']:
            self.assertIn(field, text, '候选段指令必须点名必填字段 %s' % field)
        self.assertIn('恰好 2 条', text, 'ablation_checks 的确切条数必须出现在指令里')
        self.assertIn('model_only', text, '五基线必须逐值出现在指令里')
        self.assertIn('SKILL.md', text, '可引用清单必须给全，模型不该靠猜')
        self.assertIn('orientation 必须逐字写 "C"', text)
        profile_seg = next(s for s in blocks(packet) if s['tag'] == 'profiles:B')
        ptext = directive(packet, profile_seg)
        self.assertIn('retention.discard', ptext)
        self.assertIn('写 0 条直接拒收', ptext)

    def test_directive_tracks_schema_changes(self):
        """指令必须**从 schema 生成**：临时给候选 schema 加一个字段，指令必须跟着出现。"""
        packet = _packet()
        seg = next(s for s in blocks(packet) if s['tag'] == 'candidates:C')
        self.assertNotIn('future_field', directive(packet, seg))
        from app.fusion import contract
        original_items = contract.RESULT_SCHEMA['properties']['candidates']['items']
        item = json.loads(json.dumps(original_items))
        item['properties']['future_field'] = item['properties']['rationale']
        item['required'] = item['required'] + ['future_field']
        contract.RESULT_SCHEMA['properties']['candidates']['items'] = item
        try:
            self.assertIn('future_field', directive(packet, seg))
        finally:
            # **必须恢复被改的那个节点**——`original = RESULT_SCHEMA` 是同一个对象，
            # 自我赋值等于没恢复，会把全局契约污染给后面每一个测试。
            contract.RESULT_SCHEMA['properties']['candidates']['items'] = original_items

    def test_context_notes_flow_into_later_directives(self):
        packet = _packet()
        seg = next(s for s in blocks(packet) if s['tag'] == 'candidates:A')
        text = directive(packet, seg, context_notes=['profiles[A] 已定稿：coverage=partial'])
        self.assertIn('已定稿的段', text)
        self.assertIn('profiles[A]', text)


class ShapeAgreementTests(unittest.TestCase):
    """累积版形状校验必须与 contract.check_shape 语义一致（漂移守卫）。"""

    def test_valid_candidate_passes_both(self):
        packet = _packet()
        fixtures = _segment_fixtures(packet)
        seg = next(s for s in blocks(packet) if s['tag'] == 'candidates:C')
        item = fixtures[seg['tag']]
        schema = wire_item_schema('candidates')
        check_shape(item, schema, 'candidates')          # 不抛
        self.assertEqual(shape_issues(item, schema), [])

    def test_every_mutation_is_caught_by_both(self):
        packet = _packet()
        fixtures = _segment_fixtures(packet)
        seg = next(s for s in blocks(packet) if s['tag'] == 'candidates:C')
        schema = wire_item_schema('candidates')
        base = fixtures[seg['tag']]

        def mutated(name):
            item = json.loads(json.dumps(base))
            if name == 'missing_problem':
                del item['problem']
            elif name == 'extra_field':
                item['cleverness'] = 'high'
            elif name == 'bad_enum':
                item['disposition'] = 'certain'
            elif name == 'one_ablation':
                item['ablation_checks'] = item['ablation_checks'][:1]
            elif name == 'empty_title':
                item['title'] = '   '
            elif name == 'four_baselines':
                item['evaluation'][0]['baselines'] = item['evaluation'][0]['baselines'][:4]
            return item

        for name in ('missing_problem', 'extra_field', 'bad_enum',
                     'one_ablation', 'empty_title', 'four_baselines'):
            with self.subTest(name=name):
                item = mutated(name)
                with self.assertRaises(FusionError):
                    check_shape(item, schema, 'candidates')
                self.assertTrue(shape_issues(item, schema),
                                'check_shape 抛了而 shape_issues 没报：%s' % name)


class SegmentContentTests(unittest.TestCase):
    """段级内容判据：证据、锁、reviews 对应——全部零调用。"""

    def test_fabricated_evidence_is_flagged(self):
        packet = _packet()
        fixtures = _segment_fixtures(packet)
        seg = next(s for s in blocks(packet) if s['tag'] == 'profiles:A')
        item = json.loads(json.dumps(fixtures[seg['tag']]))
        item['capabilities'][0]['evidence'][0]['path'] = 'auth_audit_rules.md'
        issues = segment_issues(item, packet, seg)
        self.assertTrue(any('可引用清单' in i for i in issues))
        # 引文判据只在 path 本身可引用时才判——先把 path 修回来再验逐字归位。
        item['capabilities'][0]['evidence'][0]['path'] = 'SKILL.md'
        item['capabilities'][0]['evidence'][0]['quote'] = 'invented quotation'
        self.assertTrue(any('逐字原文' in i for i in segment_issues(item, packet, seg)))

    def test_known_gap_must_be_copied_into_missing(self):
        packet = _packet()
        gaps = context_gaps(packet)
        if not gaps['A']:
            self.skipTest('该包没有 context gaps，判据不会触发')
        fixtures = _segment_fixtures(packet)
        seg = next(s for s in blocks(packet) if s['tag'] == 'profiles:A')
        item = json.loads(json.dumps(fixtures[seg['tag']]))
        item['missing'] = [g for g in item['missing'] if g not in gaps['A']]
        issues = segment_issues(item, packet, seg)
        self.assertTrue(any('已知缺口' in i for i in issues))

    def test_candidate_locks_are_checked_per_segment(self):
        packet = _packet()
        fixtures = _segment_fixtures(packet)
        seg = next(s for s in blocks(packet) if s['tag'] == 'candidates:B')
        item = json.loads(json.dumps(fixtures[seg['tag']]))
        item['gaps_to_fill'] = []                       # needs_ai_fill 却不列缺口
        self.assertTrue(any('gaps_to_fill' in i for i in segment_issues(item, packet, seg)))
        item['disposition'] = 'hypothesis'              # 有 blockers 却自称 hypothesis
        item['gaps_to_fill'] = fixtures[seg['tag']]['gaps_to_fill']
        issues = segment_issues(item, packet, seg)
        self.assertTrue(any('hypothesis' in i for i in issues))
        item['artifacts'][0]['path'] = 'audit_report_schema.'   # 扩展名被写断
        self.assertTrue(any('dot' in i or 'extension' in i
                            for i in segment_issues(item, packet, seg)))

    def test_title_echo_is_caught_by_segment_layer(self):
        """复述材料 → 段级就抓（2026-10-06 实测 9/9 复述而旧核对器零触发）。"""
        packet = {'capability': {
            'A': {'items': {'constraints': [{'text': '每个检查器必须在三百字以内说明判定依据才允许上架'}]}},
            'B': {'items': {}}},
            'collision': {'candidates': [{'collision_id': 'c1',
                                          'gap': '画语包在脚本语法教学场景下没有可援引的规则'}]},
            'rule_directions': {'suggestions': []}}
        seg = {'key': 'candidates', 'tag': 'candidates:A', 'index': 0, 'count': 1,
               'template': {'orientation': 'A'}}
        item = {'orientation': 'A', 'direction_id': 'independent', 'disposition': 'blocked',
                'title': '画语包在脚本语法教学场景下没有可援引的规则',
                'expected_outcome': {'outcome_class': 'needs_ai_fill', 'basis': 'x'},
                'gaps_to_fill': ['g'], 'title_short': None,
                'problem': 'p', 'contribution_a': {'kind': 'way_of_working', 'mechanism': 'm', 'evidence': []},
                'contribution_b': {'kind': 'way_of_working', 'mechanism': 'm', 'evidence': []},
                'new_mechanism': 'n', 'workflow': ['w'], 'artifacts': [],
                'blockers': ['b'], 'rationale': 'r',
                'ablation_checks': [{'removed_parent': 'A', 'task': 't', 'expected_loss': 'l'},
                                    {'removed_parent': 'B', 'task': 't', 'expected_loss': 'l'}],
                'evaluation': [{'task': 't', 'baselines': ['A_only', 'B_only', 'concatenation',
                                                           'fusion', 'model_only'],
                                'success_criterion': 's'}]}
        issues = segment_issues(item, packet, seg)
        self.assertTrue(any('回声' in i for i in issues))

    def test_reviews_block_must_mirror_suggestions(self):
        packet = _packet()
        seg = next((s for s in blocks(packet) if s['key'] == 'direction_reviews'), None)
        if seg is None:
            self.skipTest('该包没有 rule_directions suggestions')
        fixtures = _segment_fixtures(packet)
        good = fixtures[seg['tag']]
        self.assertEqual(segment_issues(good, packet, seg), [])
        short = good[:-1]
        self.assertTrue(segment_issues(short, packet, seg))
        wrong = json.loads(json.dumps(good))
        wrong[0]['direction_id'] = 'invented-id'
        self.assertTrue(any('一一对应' in i for i in segment_issues(wrong, packet, seg)))


class SegmentedSynthesizeTests(unittest.TestCase):
    """执行型：走真实 HTTP mock + 真实包 + 真实验收链。"""

    def test_happy_path_five_segments_pass_full_contract(self):
        packet = _packet()
        fixtures = _segment_fixtures(packet)
        pre = engine.skeleton(packet)
        expected_calls = 2 + (1 if pre['direction_reviews'] else 0) + 3

        def respond(tag, body):
            key = tag.split(':')[0]
            return _payload_for(fixtures, tag, key)

        with server_for(respond) as (url, seen):
            result = engine.synthesize(packet, endpoint=url, model='m', max_tokens=2048,
                                       split_segments=True)
        self.assertEqual(result['status'], 'proposal_needs_review')
        self.assertFalse(result['executed'])
        self.assertFalse(result['value_verified'])
        self.assertEqual(len(seen), expected_calls,
                         '每段恰好一次调用（reviews 为 0 条时不开那个请求）')
        diag = result['segments']
        self.assertEqual(diag['calls'], expected_calls)
        self.assertEqual(diag['retries'], 0)
        self.assertTrue(all(b['first_pass'] for b in diag['blocks']))
        self.assertIn('spark', result)
        # 完整性观测附在拼装结果上（P0-A 只分类不拦截的纪律）
        self.assertIn('completeness', result)

    def test_defective_segment_is_retried_with_concrete_complaint(self):
        """缺 problem 的段必须被段级校验当场抓住；重试请求必须带着具体指控。"""
        packet = _packet()
        fixtures = _segment_fixtures(packet)
        bad = json.loads(json.dumps(fixtures['candidates:C']))
        del bad['problem']

        def respond(tag, body):
            key = tag.split(':')[0]
            if tag == 'candidates:C' and '未通过本地确定性校验' not in body['messages'][-1]['content']:
                return json.dumps({key: [bad]}, ensure_ascii=False)
            return _payload_for(fixtures, tag, key)

        with server_for(respond) as (url, seen):
            result = engine.synthesize(packet, endpoint=url, model='m', max_tokens=2048,
                                       split_segments=True)
        cand_calls = [s for s in seen if s['tag'] == 'candidates:C']
        self.assertEqual(len(cand_calls), 2, '坏段恰好重试一次')
        self.assertIn('problem', cand_calls[1]['body']['messages'][-1]['content'],
                      '重试请求必须带着校验器的具体指控')
        block = next(b for b in result['segments']['blocks'] if b['tag'] == 'candidates:C')
        self.assertFalse(block['first_pass'])
        self.assertEqual(result['segments']['retries'], 1)

    def test_retries_exhausted_rejects_instead_of_inventing(self):
        packet = _packet()
        fixtures = _segment_fixtures(packet)
        bad = json.loads(json.dumps(fixtures['candidates:A']))
        del bad['problem']

        def respond(tag, body):
            key = tag.split(':')[0]
            if tag == 'candidates:A':
                return json.dumps({key: [bad]}, ensure_ascii=False)
            return _payload_for(fixtures, tag, key)

        with server_for(respond) as (url, seen):
            with self.assertRaises(FusionError) as ctx:
                engine.synthesize(packet, endpoint=url, model='m', max_tokens=2048,
                                  split_segments=True)
        self.assertIn('candidates:A', str(ctx.exception))
        self.assertIn('problem', str(ctx.exception))
        self.assertEqual(len([s for s in seen if s['tag'] == 'candidates:A']), 2)

        with server_for(respond) as (url, _):
            with self.assertRaises(FusionError):
                engine.synthesize(packet, endpoint=url, model='m', max_tokens=2048,
                                  split_segments=True, segment_retries=0)

    def test_unparseable_segment_is_retried_then_rejected(self):
        packet = _packet()
        fixtures = _segment_fixtures(packet)

        def respond(tag, body):
            key = tag.split(':')[0]
            if tag == 'profiles:A' and '未通过本地确定性校验' not in body['messages'][-1]['content']:
                return '<<< 这压根不是 JSON >>>'
            return _payload_for(fixtures, tag, key)

        with server_for(respond) as (url, seen):
            result = engine.synthesize(packet, endpoint=url, model='m', max_tokens=2048,
                                       split_segments=True)
        self.assertEqual(len([s for s in seen if s['tag'] == 'profiles:A']), 2)
        self.assertEqual(result['status'], 'proposal_needs_review')

    def test_truncated_segment_reply_never_passes(self):
        """闸门与整份模式同一条：截断/请求工具的段不接受。"""
        packet = _packet()
        fixtures = _segment_fixtures(packet)

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                data = json.dumps({'choices': [{'finish_reason': 'length',
                                                'message': {'content': '{"profiles": []}'}}]}).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(data)

        srv = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            url = 'http://127.0.0.1:%d/v1/chat/completions' % srv.server_port
            with self.assertRaises(FusionError) as ctx:
                engine.synthesize(packet, endpoint=url, model='m', max_tokens=2048,
                                  split_segments=True, segment_retries=0)
        finally:
            srv.shutdown()
            srv.server_close()
            t.join()
        self.assertIn('不完整', str(ctx.exception))

    def test_split_segments_rejects_tool_carrier(self):
        with self.assertRaises(FusionError) as ctx:
            engine.synthesize(_packet(), endpoint='http://127.0.0.1:9/v1/chat/completions',
                              model='m', carrier='tool', split_segments=True)
        self.assertIn('不组合', str(ctx.exception))

    def test_segment_retries_bounds(self):
        for bad in (-1, 4, '1', 1.5):
            with self.subTest(bad=bad):
                with self.assertRaises(FusionError):
                    engine.synthesize(_packet(), endpoint='http://127.0.0.1:9/v1/chat/completions',
                                      model='m', split_segments=True, segment_retries=bad)


class DefaultPathUntouchedTests(unittest.TestCase):
    """开关关着的时候，默认路径必须与从前完全一致（单次调用、不发分段指令）。"""

    def test_default_sends_exactly_one_request_without_segment_directive(self):
        packet = _packet()
        fixtures = _segment_fixtures(packet)

        def respond(tag, body):
            # 默认路径没有分段标记；返回整份结果，靠既有链路拒绝/接受都行，
            # 这里只断言请求形状。
            return json.dumps(fixtures['candidates:A'], ensure_ascii=False)

        with server_for(respond) as (url, seen):
            with self.assertRaises(FusionError):
                engine.synthesize(packet, endpoint=url, model='m', max_tokens=2048)
            self.assertEqual(len(seen), 1)
            self.assertIsNone(seen[0]['tag'], '默认路径不得携带分段指令')
            self.assertNotIn('【分段模式', seen[0]['body']['messages'][-1]['content'])


class UtilTests(unittest.TestCase):
    def test_take_segment_prefers_pinned_match_over_position(self):
        seg = {'key': 'candidates', 'tag': 'candidates:B', 'index': 0, 'count': 1,
               'template': {'orientation': 'B', 'direction_id': 'x'}}
        first = {'orientation': 'A', 'direction_id': 'x'}
        second = {'orientation': 'B', 'direction_id': 'x'}
        self.assertEqual(take_segment({'candidates': [first, second]}, seg), second)
        self.assertIsNone(take_segment({'candidates': []}, seg))
        reviews_seg = {'key': 'direction_reviews', 'tag': 'direction_reviews:all',
                       'index': 0, 'count': 2, 'template': []}
        self.assertEqual(take_segment({'direction_reviews': [{'decision': 'adopt'}]},
                                      reviews_seg), [{'decision': 'adopt'}])

    def test_summarize_is_compact(self):
        self.assertIn('coverage=partial',
                      summarize({'key': 'profiles', 'tag': 'profiles:A', 'index': 0, 'count': 1,
                                 'template': {}},
                                {'parent': 'A', 'coverage': 'partial',
                                 'retention': {'keep': ['a'], 'discard': []}}))
        self.assertIn('candidates[A]',
                      summarize({'key': 'candidates', 'tag': 'candidates:A', 'index': 0,
                                 'count': 1, 'template': {}},
                                {'orientation': 'A', 'title': 't', 'disposition': 'blocked',
                                 'expected_outcome': {'outcome_class': 'needs_ai_fill'}}))


class SegmentReanchorTests(unittest.TestCase):
    """段级重锚定（先修后判）——2026-10-07 探针抓出的段级校验真缺陷。

    段级校验若在重锚定之前判引文，会把「能修的转写误差」当成段级失败；
    整份模式的验收链故意在契约前跑 `_reanchor`（0.95/0.90 双阈值、唯一命中
    才归位），段级必须同序。反向验证：把 `_segment_reanchor` 从
    `_synthesize_segmented` 的循环里删掉，下面各条会变红。
    """

    def _item(self, tag):
        packet = _packet()
        fixtures = _segment_fixtures(packet)
        seg = next(s for s in blocks(packet) if s['tag'] == tag)
        return packet, seg, json.loads(json.dumps(fixtures[seg['tag']]))

    def test_transcription_error_quote_is_fixed_not_retried(self):
        """围栏残留类转写误差（实测形态）由段级重锚定修复，段级校验放行。"""
        packet, seg, item = self._item('candidates:A')
        ref = item['contribution_a']['evidence'][0]
        f = next(f for f in packet['packages']['A']['files'] if f['path'] == ref['path'])
        true_text = '\n'.join(f['text'].splitlines()[ref['start_line'] - 1:ref['end_line']])
        ref['quote'] = true_text + '\n```python'          # 典型转写残留：多抄了一行围栏
        report = engine._segment_reanchor(item, seg, packet)
        self.assertGreaterEqual(report['fixed'], 1)
        self.assertEqual(segment_issues(item, packet, seg), [])

    def test_wrong_line_range_is_relocated_when_unique(self):
        """区间指错但内容真实存在且唯一命中 → 归位；不唯一 → 照旧拒绝。"""
        from app.fusion.evidence_reanchor import _unique_whole_file
        packet, seg, item = self._item('candidates:C')
        ref = item['contribution_b']['evidence'][0]
        f = next(f for f in packet['packages']['B']['files'] if f['path'] == ref['path'])
        lines = f['text'].splitlines()
        if len(lines) < 6:
            self.skipTest('文件太短，构造不了错区间')
        # 用**真实判据**选窗口：`_unique_whole_file` 恰好唯一命中的两行——
        # 手写「前 40 字符唯一」挡不住相似窗口（markdown 重复结构会把命中变不唯一）。
        target = None
        for a in range(3, len(lines) - 1):        # 跳过前两行：声明区间就是 1..2，
            text = '\n'.join(lines[a - 1:a + 1])  # 选到它就等于「没指错」，构造不出场景
            if not text.strip():
                continue
            hit = _unique_whole_file(lines, text, 0.90)
            if hit and hit[0] == a and hit[1] == a + 1:
                target = (a, a + 1, text)
                break
        if target is None:
            self.skipTest('构造不出唯一两行窗口')
        a, b, text = target
        ref['start_line'], ref['end_line'] = 1, 2
        ref['quote'] = text
        report = engine._segment_reanchor(item, seg, packet)
        self.assertGreaterEqual(report['fixed'], 1)
        self.assertEqual((ref['start_line'], ref['end_line']), (a, b))
        self.assertEqual(segment_issues(item, packet, seg), [])

    def test_fabricated_quote_still_rejected_after_segment_reanchor(self):
        """**修 ≠ 猜**：全文都找不到的引文，重锚定修不动，段级照旧判死。"""
        packet, seg, item = self._item('candidates:B')
        item['contribution_a']['evidence'][0]['quote'] = \
            'This quotation is fabricated wholesale and appears nowhere in the package.'
        report = engine._segment_reanchor(item, seg, packet)
        self.assertGreaterEqual(len(report['rejected']), 1)
        issues = segment_issues(item, packet, seg)
        self.assertTrue(any('逐字原文' in i for i in issues))

    def test_happy_path_with_transcription_errors_still_accepted(self):
        """执行型：带转写误差的段走完 分段→重锚定→真实契约 全链仍 accepted。"""
        packet = _packet()
        fixtures = _segment_fixtures(packet)
        item = json.loads(json.dumps(fixtures['candidates:B']))
        ref = item['contribution_b']['evidence'][0]
        f = next(f for f in packet['packages']['B']['files'] if f['path'] == ref['path'])
        true_text = '\n'.join(f['text'].splitlines()[ref['start_line'] - 1:ref['end_line']])
        item['contribution_b']['evidence'][0]['quote'] = '```\n' + true_text   # 整段围栏包裹
        fixtures['candidates:B'] = item

        def respond(tag, body):
            key = tag.split(':')[0]
            return _payload_for(fixtures, tag, key)

        with server_for(respond) as (url, seen):
            result = engine.synthesize(packet, endpoint=url, model='m', max_tokens=2048,
                                       split_segments=True)
        self.assertEqual(result['status'], 'proposal_needs_review')
        block = next(b for b in result['segments']['blocks'] if b['tag'] == 'candidates:B')
        self.assertGreaterEqual(block.get('reanchor_fixed', 0), 1)

    def test_no_landing_pair_pins_independent(self):
        """无落点、无建议的包对：A/B 候选的 direction_id 必须被钉成 "independent"。

        2026-10-07 fuse-iter2 实测抓出：这种包对上骨架留空串、契约只认
        "independent"，模型没被告知就自由发挥，candidates:A 连挂 3 次。
        """
        import copy
        packet = copy.deepcopy(_packet())
        packet['collision']['candidates'] = []
        packet['rule_directions']['suggestions'] = []
        seg = next(s for s in blocks(packet) if s['tag'] == 'candidates:A')
        text = directive(packet, seg)
        self.assertIn('"independent"', text)
        item = {'orientation': 'A', 'direction_id': 'made-up-id', 'disposition': 'blocked',
                'title': 'unique new title here', 'problem': 'p',
                'expected_outcome': {'outcome_class': 'needs_ai_fill', 'basis': 'b'},
                'gaps_to_fill': ['g'],
                'contribution_a': {'kind': 'way_of_working', 'mechanism': 'm', 'evidence': []},
                'contribution_b': {'kind': 'way_of_working', 'mechanism': 'm', 'evidence': []},
                'new_mechanism': 'n', 'workflow': ['w'],
                'artifacts': [{'path': 'a.py', 'purpose': 'p'}],
                'blockers': ['b'], 'rationale': 'r',
                'ablation_checks': [{'removed_parent': 'A', 'task': 't', 'expected_loss': 'l'},
                                    {'removed_parent': 'B', 'task': 't', 'expected_loss': 'l'}],
                'evaluation': [{'task': 't', 'baselines': ['A_only', 'B_only', 'concatenation',
                                                           'fusion', 'model_only'],
                                'success_criterion': 's'}]}
        issues = segment_issues(item, packet, seg)
        self.assertTrue(any('direction_id' in i for i in issues))
        item['direction_id'] = 'independent'
        self.assertFalse([i for i in segment_issues(item, packet, seg) if 'direction_id' in i])

    def test_reviews_block_extends_not_appends(self):
        """reviews 块（count>1）的 item 是整张列表——定稿必须 extend。
        套一层列表会让 assemble 把 2 条 review 数成 1 条（glm-4.5-air 实测）。"""
        from app.fusion.segments import add_finalized
        finalized = {'profiles': [], 'candidates': [], 'direction_reviews': []}
        seg = {'key': 'direction_reviews', 'tag': 'direction_reviews:all',
               'index': 0, 'count': 2, 'template': []}
        item = [{'direction_id': 'd1', 'decision': 'adopt', 'reason': 'r'},
                {'direction_id': 'd2', 'decision': 'reject', 'reason': 'r'}]
        add_finalized(finalized, seg, item)
        self.assertEqual(len(finalized['direction_reviews']), 2)
        self.assertIsInstance(finalized['direction_reviews'][0], dict)
        seg1 = {'key': 'candidates', 'tag': 'candidates:A', 'index': 0, 'count': 1,
                'template': {}}
        add_finalized(finalized, seg1, {'orientation': 'A'})
        self.assertEqual(len(finalized['candidates']), 1)

    def test_profile_directive_lists_known_gaps(self):
        """profiles 段指令必须点名已知缺口（known_gaps + context_gaps）。"""
        packet = _packet()
        from app.fusion.context import effective_gaps
        gaps = effective_gaps(packet)['A']
        seg = next(s for s in blocks(packet) if s['tag'] == 'profiles:A')
        text = directive(packet, seg)
        if not gaps:
            self.assertIn('missing', text)
            return
        for gap in gaps:
            self.assertIn(gap[:50], text, '已知缺口必须逐字出现在 profiles 段指令里')


class SelectContextCollisionTests(unittest.TestCase):
    """`select_context` 必须随 capability 重算碰撞候选（2026-10-07 dev-expert 实测）。

    2026-10-02 起碰撞候选是契约锚点的唯一可信来源；select_context 此前只重算
    capability 与 rule_directions，返回包里带着旧选择的碰撞——任何「改上下文
    选择」的流程只要落点变了，verify 必报「Collision candidates changed」。
    反向验证：删掉 select_context 里那行 `result['collision'] = collide(...)`，
    本测试当场变红。
    """

    def test_select_context_recomputes_collision(self):
        import copy
        packet = copy.deepcopy(_packet())
        # 精简选择（只留 SKILL.md）——大包上落点集合几乎必然变化
        from app.fusion.context import classification
        selection = {}
        for parent, package in packet['packages'].items():
            rows = []
            for entry in package['files']:
                if entry['status'] != 'text':
                    continue
                role, why = classification(entry)
                is_entry = entry['path'] == 'SKILL.md'
                rows.append({'path': entry['path'], 'include': is_entry,
                             'role': role, 'classification_reason': why,
                             'reason': '入口必留' if is_entry else '精简省略'})
            selection[parent] = rows
        selected_packet = engine.select_context(packet, selection)
        engine.verify_packet(selected_packet)          # 修复前在这里报 Collision candidates changed
        recomputed = engine.collide(selected_packet['capability']['A'],
                                    selected_packet['capability']['B'])
        self.assertEqual(selected_packet['collision'], recomputed,
                         '返回包的碰撞必须对应新选择重算，不得携带旧选择的落点')


class AutoSelectionTests(unittest.TestCase):
    """快速碰撞的自动精简选择（context.auto_selection）。

    守住两条：产物必须通过正式 `select_context` 重签（verify 过）；
    SKILL.md 必留、预算外文件逐条带 reason——自动化不等于匿名化。
    反向验证：删掉 auto_selection 里的 reason 分支或 SKILL.md 必留，
    下面的断言分别变红。
    """

    def test_auto_selection_passes_verify_and_keeps_entry(self):
        import copy
        from app.fusion.context import auto_selection
        packet = copy.deepcopy(_packet())
        selected = auto_selection(packet, budget=3000)
        engine.verify_packet(selected)                  # 正式重签后必须过 verify
        for parent in ('A', 'B'):
            rows = selected['context_selection'][parent]
            entry = next(r for r in rows if r['path'] == 'SKILL.md')
            self.assertTrue(entry['include'], 'SKILL.md 必留')
            self.assertTrue(all(r['reason'].strip() for r in rows),
                            '每个文件的去留都必须有 reason')
            omitted = [r for r in rows if not r['include']]
            self.assertTrue(all('快速模式' in r['reason'] for r in omitted),
                            '省略理由必须标明快速模式')


class TwoPhaseCandidateTests(unittest.TestCase):
    """候选块二段化（candidate_two_phase，默认关）——叙述与证据分开生成。

    守住：① phase1 的 evidence 空数组在段级放行（wire 放宽仅此一处）；
    ② phase2 的证据走**同一条**段级证据校验（编造照样被抓）；
    ③ 合并后过真实契约；④ 开关关闭时行为不变。
    反向验证：删掉引擎里的 two_phase 分支或把 merge 去掉，下面会红。
    """

    def _packet(self):
        import copy
        return copy.deepcopy(_packet())

    def _narrative(self, packet, orientation):
        fixtures = _segment_fixtures(packet)
        item = json.loads(json.dumps(fixtures['candidates:%s' % orientation]))
        item['contribution_a']['evidence'] = []
        item['contribution_b']['evidence'] = []
        return item

    def test_two_phase_happy_path_passes_full_contract(self):
        packet = self._packet()
        fixtures = _segment_fixtures(packet)
        pre = engine.skeleton(packet)
        expected_calls = 2 + (1 if pre['direction_reviews'] else 0) + 3 * 2  # 候选 ×2 段

        def respond(i, body):
            content = body['messages'][-1]['content']
            if '第二段：证据' in content:
                m = re.search(r'candidates:([ABC])', content)
                side = m.group(1)
                item = fixtures['candidates:%s' % side]
                ea = dict(item['contribution_a']['evidence'][0])
                eb = dict(item['contribution_b']['evidence'][0])
                return json.dumps({'evidence_a': [ea], 'evidence_b': [eb]},
                                  ensure_ascii=False)
            tag_m = _TAG.search(content)
            # 二段化的标记带「· 第一段/第二段」后缀，先归一化再分发
            tag = tag_m.group(1).split(' · ')[0]
            key = tag.split(':')[0]
            if tag.startswith('candidates:'):
                orient = tag.split(':')[1]
                return json.dumps({'candidates': [fixtures['candidates:%s' % orient]]},
                                  ensure_ascii=False)
            return _payload_for(fixtures, tag, key)

        with server_for(respond) as (url, seen):
            result = engine.synthesize(packet, endpoint=url, model='m', max_tokens=2048,
                                       split_segments=True, candidate_two_phase=True)
        self.assertEqual(result['status'], 'proposal_needs_review')
        cand_blocks = [b for b in result['segments']['blocks'] if b['tag'].startswith('candidates')]
        self.assertEqual(len(cand_blocks), 3)
        self.assertTrue(all(not b['failed'] for b in cand_blocks))
        self.assertEqual(len(seen), expected_calls)

    def test_two_phase_fabricated_evidence_retried_with_complaint(self):
        packet = self._packet()
        fixtures = _segment_fixtures(packet)
        narrative = self._narrative(packet, 'C')
        bad_ea = dict(fixtures['candidates:C']['contribution_a']['evidence'][0])
        bad_ea['quote'] = '凭空编造的引文，包里根本没有这句话'
        calls = {'ev': 0}

        def respond(i, body):
            content = body['messages'][-1]['content']
            if '第二段：证据' in content:
                calls['ev'] += 1
                if calls['ev'] == 1:
                    return json.dumps({'evidence_a': [bad_ea],
                                       'evidence_b': [fixtures['candidates:C']['contribution_b']['evidence'][0]]},
                                      ensure_ascii=False)
                return json.dumps({'evidence_a': [fixtures['candidates:C']['contribution_a']['evidence'][0]],
                                   'evidence_b': [fixtures['candidates:C']['contribution_b']['evidence'][0]]},
                                  ensure_ascii=False)
            tag_m = _TAG.search(content)
            tag = tag_m.group(1).split(' · ')[0]
            key = tag.split(':')[0]
            if tag.startswith('candidates:'):
                orient = tag.split(':')[1]
                return json.dumps({'candidates': [self._narrative(packet, orient)]},
                                  ensure_ascii=False)
            return _payload_for(fixtures, tag, key)

        with server_for(respond) as (url, seen):
            result = engine.synthesize(packet, endpoint=url, model='m', max_tokens=2048,
                                       split_segments=True, candidate_two_phase=True)
        self.assertEqual(result['status'], 'proposal_needs_review')
        ev_retry = [s for s in seen if '第二段：证据' in s['body']['messages'][-1]['content']]
        self.assertGreaterEqual(len(ev_retry), 2, '编造证据必须触发重试')
        self.assertIn('逐字原文', ev_retry[1]['body']['messages'][-1]['content'],
                      '重试指控必须带出证据校验的具体失败')

    def test_two_phase_off_by_default_uses_single_call(self):
        packet = self._packet()
        fixtures = _segment_fixtures(packet)
        seen_tags = []

        def respond(tag, body):
            seen_tags.append(tag)
            key = tag.split(':')[0]
            return _payload_for(fixtures, tag, key)

        with server_for(respond) as (url, seen):
            engine.synthesize(packet, endpoint=url, model='m', max_tokens=2048,
                              split_segments=True)
        cand_calls = [s for s in seen if s['tag'] and s['tag'].startswith('candidates')]
        self.assertEqual(len(cand_calls), 3, '开关关闭：每候选恰好一次调用（不含证据段）')
        self.assertFalse(any('第二段：证据' in s['body']['messages'][-1]['content']
                             for s in seen), '开关关闭时不得发出证据段指令')


class SegmentProbeTests(unittest.TestCase):
    """分段适配探测（2026-10-12，机主裁定「分段按自适应 + 前置探测门控」）。

    M3 导航批实测分段 0/4（引文保真度不过段级重锚定）而整份 1/4——分段收益是
    **模型条件性的**，产品默认 `FUSION_SPLIT=auto` 先探测再选模式。
    探测的铁律：**一次调用、无重试**——带重试就不是便宜的门了。
    """

    def test_probe_passes_on_valid_first_segment_with_single_call(self):
        packet = _packet()
        fixtures = _segment_fixtures(packet)

        def respond(tag, body):
            return _payload_for(fixtures, 'profiles:A', 'profiles')

        with server_for(respond) as (url, seen):
            report = engine.segment_probe(packet, endpoint=url, model='m', api_key='k')
        self.assertTrue(report['ok'], report['issues'])
        self.assertEqual(report['segment'], 'profiles:A', '探测只跑第一段')
        self.assertEqual(len(seen), 1, '探测是一次调用，不带重试')

    def test_probe_fails_without_retry_on_invalid_segment(self):
        packet = _packet()
        bad = json.dumps({'profiles': [{'parent': 'A'}]}, ensure_ascii=False)

        def respond(tag, body):
            return bad

        with server_for(respond) as (url, seen):
            report = engine.segment_probe(packet, endpoint=url, model='m', api_key='k')
        self.assertFalse(report['ok'])
        self.assertTrue(report['issues'], '失败必须带具体指控（供留档与人读）')
        self.assertEqual(len(seen), 1, '探测失败也不重试——它只是门，不是产物')


if __name__ == '__main__':
    unittest.main()
