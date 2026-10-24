"""Synthetic package and local HTTP integration tests; never call real models or paid services."""
import copy
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

from app.fusion.packages import FusionError, snapshot, MAX_FILE_BYTES
from app.fusion.contract import validate_result
from app.fusion.engine import prepare, preview, messages, synthesize, parse_json, check_endpoint, read_packet
from app.fusion.__main__ import main


def proposed(packet):
    from .context import context_gaps
    def ref(parent):
        f = next(f for f in packet['packages'][parent]['files'] if f['path'] == 'SKILL.md')
        return {'parent': parent, 'path': f['path'], 'sha256': f['sha256'],
                'start_line': 1, 'end_line': 1, 'quote': f['text'].splitlines()[0]}
    profiles = [{'parent': p, 'capabilities': [{'claim': 'Declared teaching capability', 'basis': 'declared',
                                              'evidence': [ref(p)]}],
                 'retention': {'keep': ['The feedback method that carries the claim'],
                               'discard': ['The task list, which belongs to the other domain'],
                               'reason': 'Keeping both packages whole diluted each contribution'},
                 'dependencies': ['Domain resources'], 'missing': ['Question bank'] + context_gaps(packet)[p], 'coverage': 'partial'} for p in 'AB']
    # A/B 方向的候选必须锚到**本地算出的**碰撞落点上（2026-10-02 机主裁决）。
    # 旧夹具三个候选全写 'independent'，那正是 `independent` 两义的老问题：
    # orientation 与 direction_id 是两个互不相干的字段，模型把 A/B 也挂上去，
    # 契约只能靠「恰好可满足」放过去。现在锚点由本地给，A/B 无处可挂。
    collisions = (packet.get('collision') or {}).get('candidates') or []
    ids = [c['collision_id'] for c in collisions]
    anchor = {'A': ids[0] if len(ids) > 0 else 'independent',
              'B': ids[1] if len(ids) > 1 else (ids[0] if ids else 'independent'),
              'C': 'independent'}
    candidates = [{'orientation': p, 'direction_id': anchor[p], 'disposition': 'blocked', 'title': f'{p} learning experiment',
                   'expected_outcome': {'outcome_class': 'needs_ai_fill', 'basis': 'The task cannot run until the missing question bank exists'},
                   'gaps_to_fill': ['Question bank for the target domain'],
                   'problem': 'Understand an error through an annotated diagram',
                   'contribution_a': {'kind': 'way_of_working', 'mechanism': 'Source-checked constraint list', 'evidence': [ref('A')]},
                   'contribution_b': {'kind': 'way_of_working', 'mechanism': 'Stepwise retest with records', 'evidence': [ref('B')]},
                   'new_mechanism': 'Map error type to diagram annotation and targeted retest',
                   'workflow': ['Classify sourced errors', 'Choose diagram feedback', 'Retest'],
                   'artifacts': [{'path': 'scripts/feedback.py', 'purpose': 'Map errors to a feedback strategy'}],
                   'ablation_checks': [{'removed_parent': parent, 'task': 'Explain and retest a mistake',
                                        'expected_loss': 'Lose the corresponding sourced constraint or feedback method'} for parent in 'AB'],
                   'blockers': ['Missing question bank'], 'rationale': 'Requires resources before implementation',
                   'evaluation': [{'task': 'Explain and retest a synthetic mistake',
                                   'baselines': ['A_only', 'B_only', 'concatenation', 'fusion', 'model_only'],
                                   'success_criterion': 'Correct sourced explanation and retest without unsupported claims'}]}
                  for p in 'ABC']
    return {'profiles': profiles, 'candidates': candidates,
            'direction_reviews': [{'direction_id': d['id'], 'decision': 'reject', 'reason': 'Synthetic review: prefer independent proposal'}
                                  for d in packet['rule_directions']['suggestions']]}


@contextmanager
def provider(result=None, status=200, finish='stop', delay=0, tools=False, raw=None):
    captured = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            captured.append({'headers': dict(self.headers), 'body': json.loads(self.rfile.read(int(self.headers['Content-Length'])))})
            time.sleep(delay)
            body = raw if raw is not None else json.dumps({'choices': [{'finish_reason': finish, 'message': {
                'content': json.dumps(result), **({'tool_calls': [{}]} if tools else {})}}]}).encode()
            try:
                self.send_response(status)
                if status == 302:
                    self.send_header('Location', 'http://127.0.0.1:9/stolen')
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1/chat/completions', captured
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class FusionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.a, self.b = self.root/'a', self.root/'b'
        for p in (self.a, self.b):
            p.mkdir()
            (p/'SKILL.md').write_text('# Teaching instructions\nSee [rules](references/rules.md)\n', encoding='utf-8', newline='')
        self.packet = prepare(self.a, self.b, 'Help adult learners explain a practice error')

    def test_cli_can_prepare_an_exploration_without_a_goal(self):
        output = self.root / 'exploration.json'
        with redirect_stdout(StringIO()):
            main(['prepare', '--a', str(self.a), '--b', str(self.b), '--out', str(output)])
        packet = read_packet(output)
        self.assertEqual(packet['intent_mode'], 'explore')
        self.assertEqual(packet['goal'], '')
        self.assertEqual(preview(packet)['intent_mode'], 'explore')

    def test_zip_and_directory_preserve_full_code_and_reference_evidence(self):
        (self.a/'scripts').mkdir()
        code = 'raise RuntimeError("must never execute")\n' * 200
        (self.a/'scripts'/'work.py').write_text(code, encoding='utf-8', newline='')
        zpath = self.root/'a.zip'
        with zipfile.ZipFile(zpath, 'w') as z:
            for p in self.a.rglob('*'):
                if p.is_file():
                    z.write(p, 'wrapper/'+p.relative_to(self.a).as_posix())
        directory, archive = snapshot(self.a), snapshot(zpath)
        self.assertEqual(directory, archive)
        self.assertEqual(next(f for f in archive['files'] if f['path'].endswith('.py'))['text'], code)
        self.assertFalse(archive['executed'])
        self.assertEqual(archive['references'][0]['status'], 'unresolved')

    def test_unreadable_subdirectory_fails_instead_of_omitting_source(self):
        child = self.a / 'scripts'
        child.mkdir()
        (child / 'logic.py').write_text('important_source = True')
        real_scandir = os.scandir
        def guarded_scandir(path):
            if Path(path) == child:
                raise PermissionError('synthetic unreadable subtree')
            return real_scandir(path)
        with patch('os.scandir', side_effect=guarded_scandir):
            with self.assertRaisesRegex(FusionError, 'enumerate'):
                snapshot(self.a)

    def test_primary_paths_reject_linked_ancestors(self):
        # Simulate the lstat metadata of a Windows junction without requiring
        # symlink privileges; all leaf files are real synthetic source files.
        real_lstat = Path.lstat
        def linked_parent(path, *args, **kwargs):
            info = real_lstat(path, *args, **kwargs)
            if path == self.root:
                class Reparse:
                    st_mode = info.st_mode
                    st_file_attributes = 0x400
                return Reparse()
            return info
        with patch.object(Path, 'lstat', linked_parent):
            with self.assertRaisesRegex(FusionError, 'link|reparse'):
                snapshot(self.a)

    def test_raw_hash_distinguishes_line_endings_while_quotes_are_normalized(self):
        first = snapshot(self.a)
        data = (self.a/'SKILL.md').read_bytes().replace(b'\n', b'\r\n')
        (self.a/'SKILL.md').write_bytes(data)
        second = snapshot(self.a)
        self.assertNotEqual(first['sha256'], second['sha256'])
        self.assertEqual(first['files'][0]['text'], second['files'][0]['text'])

    def test_binary_and_sensitive_files_are_visible_but_not_sent_as_text(self):
        (self.a/'picture.png').write_bytes(b'\x89PNG\x00')
        (self.a/'.env').write_text('SECRET_SHOULD_NOT_APPEAR')
        (self.a/'.state').mkdir()
        (self.a/'.state'/'private.txt').write_text('SECRET_SHOULD_NOT_APPEAR')
        packet = prepare(self.a, self.b, 'Explore')
        status = {f['path']: f['status'] for f in packet['packages']['A']['files']}
        self.assertEqual(status['picture.png'], 'unparsed')
        self.assertEqual(status['.env'], 'excluded')
        self.assertEqual(status['.state'], 'excluded')
        self.assertNotIn('SECRET_SHOULD_NOT_APPEAR', json.dumps(messages(packet)))

    def test_non_utf8_is_not_silently_replaced(self):
        (self.a/'legacy.txt').write_bytes(b'\xff\xfe\x11')
        self.assertEqual(next(f for f in snapshot(self.a)['files'] if f['path'] == 'legacy.txt')['status'], 'unparsed')

    def test_unsafe_zip_paths_and_duplicates(self):
        for name in ['../escape.txt', '/absolute.txt', 'C:/abs.txt', 'scripts\\evil.py', 'CON.txt', 'ok./file', 'bad\x01.txt']:
            with self.subTest(name=name):
                p = self.root/'bad.zip'
                with zipfile.ZipFile(p, 'w') as z:
                    z.writestr('SKILL.md', 'ok')
                    entry = zipfile.ZipInfo('placeholder')
                    entry.filename = name
                    z.writestr(entry, 'bad')
                with self.assertRaises(FusionError):
                    snapshot(p)
        with zipfile.ZipFile(self.root/'duplicate.zip', 'w') as z:
            z.writestr('SKILL.md', 'first')
            z.writestr('skill.md', 'second')
        with self.assertRaises(FusionError):
            snapshot(self.root/'duplicate.zip')

    def test_zip_links_are_rejected_without_extracting(self):
        p = self.root/'link.zip'
        with zipfile.ZipFile(p, 'w') as z:
            z.writestr('SKILL.md', 'ok')
            entry = zipfile.ZipInfo('link')
            entry.create_system = 3
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(entry, '/private/file')
        with self.assertRaises(FusionError):
            snapshot(p)

    def test_oversized_file_and_missing_entry_fail(self):
        (self.a/'huge.txt').write_bytes(b'x' * (MAX_FILE_BYTES + 1))
        with self.assertRaises(FusionError):
            snapshot(self.a)
        empty = self.root/'empty'
        empty.mkdir()
        with self.assertRaises(FusionError):
            snapshot(empty)

    def test_context_budget_fails_instead_of_truncation(self):
        with self.assertRaisesRegex(FusionError, 'budget exceeded'):
            prepare(self.a, self.b, 'Explore', 1000)

    def test_snapshot_changes_do_not_mutate_prepared_request(self):
        before = messages(self.packet)
        (self.a/'SKILL.md').write_text('NEW SOURCE')
        self.assertEqual(before, messages(self.packet))
        self.assertNotEqual(self.packet['sha256'], prepare(self.a, self.b, 'Explore')['sha256'])
        with patch('app.fusion.engine.SYSTEM', 'changed prompt'):
            with self.assertRaisesRegex(FusionError, 'version changed'):
                messages(self.packet)
        bad = copy.deepcopy(self.packet)
        bad['packages']['A']['files'][0]['text'] += 'tampered'
        with self.assertRaises(FusionError):
            messages(bad)

    def test_valid_proposal_is_never_marked_executed_or_verified(self):
        with provider(proposed(self.packet)) as (endpoint, captured):
            result = synthesize(self.packet, endpoint=endpoint, model='test-only', api_key='test-secret')
        self.assertEqual(result['status'], 'proposal_needs_review')
        self.assertFalse(result['executed'])
        self.assertFalse(result['value_verified'])
        self.assertTrue(result['coverage_gaps'])
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]['headers']['Authorization'], 'Bearer test-secret')
        self.assertNotIn('test-secret', json.dumps(result))
        self.assertNotIn('tools', captured[0]['body'])
        self.assertEqual(captured[0]['body']['messages'], messages(self.packet))

    def test_forged_hash_path_quote_and_line_are_rejected(self):
        for key, value in [('sha256', '0'*64), ('path', 'absent.py'), ('quote', 'invented'),
                           ('start_line', 99), ('end_line', 99), ('parent', 'B')]:
            with self.subTest(key=key):
                result = proposed(self.packet)
                result['profiles'][0]['capabilities'][0]['evidence'][0][key] = value
                with self.assertRaises(FusionError):
                    validate_result(result, self.packet)

    def test_incomplete_abc_duplicate_baselines_and_success_claim_fail(self):
        for change in ('orientation', 'baseline', 'success', 'artifact', 'coverage', 'blocked'):
            with self.subTest(change=change):
                result = proposed(self.packet)
                if change == 'orientation':
                    result['candidates'][2]['orientation'] = 'A'
                elif change == 'baseline':
                    result['candidates'][0]['evaluation'][0]['baselines'] = ['A_only']*4
                elif change == 'success':
                    result['executed'] = True
                elif change == 'artifact':
                    result['candidates'][0]['artifacts'][0]['path'] = '../unsafe.py'
                elif change == 'coverage':
                    result['profiles'][0]['coverage'] = 'sufficient_for_proposal'
                else:
                    result['candidates'][0]['disposition'] = 'hypothesis'
                with self.assertRaises(FusionError):
                    validate_result(result, self.packet)

    def test_independent_usable_must_borrow_method_not_capability(self):
        """第 3 类「与 A、B 都无关但可用」只有借了做法、借不了能力时才成立。

        能力（教/审/修这类领域职能）挪到第三域不会变成「无关但可用」——那只是换了话题。
        所以认领第 3 类时，两边贡献都必须声明为 way_of_working。
        """
        result = proposed(self.packet)
        c = result['candidates'][0]
        c['expected_outcome'] = {'outcome_class': 'independent_usable',
                                  'basis': 'Only the working shape transfers, not either domain capability'}
        c['disposition'] = 'hypothesis'
        c['gaps_to_fill'] = []
        c['blockers'] = []
        validate_result(result, self.packet)          # 两边都 way_of_working → 成立
        for key in ('contribution_a', 'contribution_b'):
            with self.subTest(key=key):
                bad = proposed(self.packet)
                bc = bad['candidates'][0]
                bc['expected_outcome'] = c['expected_outcome']
                bc['disposition'] = 'hypothesis'
                bc['gaps_to_fill'] = []
                bc['blockers'] = []
                bc[key]['kind'] = 'capability'
                with self.assertRaises(FusionError) as raised:
                    validate_result(bad, self.packet)
                self.assertIn('way_of_working', str(raised.exception),
                              '错误要指出该怎么改，而不是只说不通过')

    def test_contribution_kind_must_be_a_known_value(self):
        result = proposed(self.packet)
        result['candidates'][0]['contribution_a']['kind'] = 'vibes'
        with self.assertRaises(FusionError) as raised:
            validate_result(result, self.packet)
        self.assertIn('contribution_a', str(raised.exception))

    def test_unsuitable_direction_can_be_rejected(self):
        result = proposed(self.packet)
        c = result['candidates'][2]
        c.update(disposition='not_recommended', workflow=[], artifacts=[], rationale='No useful new mechanism with current evidence')
        validate_result(result, self.packet)

    def test_truncated_and_tool_call_outputs_fail(self):
        for finish, tools in [('length', False), ('stop', True)]:
            with provider(proposed(self.packet), finish=finish, tools=tools) as (endpoint, captured):
                with self.assertRaises(FusionError):
                    synthesize(self.packet, endpoint=endpoint, model='test-only')
            self.assertEqual(len(captured), 1)

    def test_http_failures_and_redirects_never_retry_or_echo_body(self):
        for status in (302, 401, 429, 500):
            with provider(status=status, raw=b'private prompt and key') as (endpoint, captured):
                with self.assertRaises(FusionError) as caught:
                    synthesize(self.packet, endpoint=endpoint, model='test-only')
                self.assertNotIn('private', str(caught.exception))
            self.assertEqual(len(captured), 1)

    def test_timeout_keeps_packet_intact(self):
        before = copy.deepcopy(self.packet)
        with provider(proposed(self.packet), delay=1.3) as (endpoint, captured):
            with self.assertRaisesRegex(FusionError, 'timeout'):
                synthesize(self.packet, endpoint=endpoint, model='test-only', timeout=1)
        self.assertEqual(self.packet, before)
        self.assertEqual(len(captured), 1)

    def test_invalid_json_and_empty_or_invalid_envelope_fail(self):
        # **围栏 JSON 不再列在这里**——它原来必须失败，而 2026-10-02 的实测推翻了这条：
        # MiniMax M3 即使收到了 `response_format: json_object`，正文仍然是
        # ```json … ``` 包着的，六次完整流程里 4 次死在这一步、只有 2 次走到契约。
        # 判据与实测冲突时以实测为准，但**只剥包住整个响应的那一对围栏**，
        # 重复键与非有限数字依旧拒绝（见 FencedJsonTests）。
        for text in ['{"a":1,"a":2}', '{"value":NaN}']:
            with self.assertRaises(FusionError):
                parse_json(text)
        for raw in [b'{}', b'not-json', b'{"choices":[]}', b'{"choices":[{"finish_reason":"stop","message":{"content":""}}]}']:
            with provider(raw=raw) as (endpoint, _):
                with self.assertRaises(FusionError):
                    synthesize(self.packet, endpoint=endpoint, model='test-only')

    def test_audited_missing_resources_cannot_be_silently_dropped(self):
        packet = prepare(self.a, self.b, 'Explore', known_gaps={'A': ['Remote library is not bundled'], 'B': []})
        result = proposed(packet)
        with self.assertRaisesRegex(FusionError, 'omitted'):
            validate_result(result, packet)
        result['profiles'][0]['missing'].append('Remote library is not bundled')
        validate_result(result, packet)
        self.assertIn('Remote library is not bundled', json.dumps(messages(packet)))

    def test_cancelled_run_creates_no_result_and_preserves_source(self):
        packet_path, out = self.root/'prepared.json', self.root/'cancelled.json'
        packet_path.write_text(json.dumps(self.packet), encoding='utf-8')
        with patch('app.fusion.engine.request_model', side_effect=KeyboardInterrupt), redirect_stderr(StringIO()):
            self.assertEqual(main(['run', '--packet', str(packet_path), '--endpoint', 'http://127.0.0.1:9/v1/chat/completions',
                                   '--model', 'test-only', '--out', str(out)]), 130)
        self.assertFalse(out.exists())
        self.assertEqual(read_packet(packet_path), self.packet)

    def test_malformed_snapshot_fails_even_with_recomputed_packet_hash(self):
        from app.fusion.packages import canonical, digest
        for invalid in [None, {}, {'files': None}]:
            packet = copy.deepcopy(self.packet)
            packet['packages']['A'] = invalid
            packet['sha256'] = digest(canonical({k: v for k, v in packet.items() if k != 'sha256'}).encode('utf-8'))
            with self.assertRaises(FusionError):
                messages(packet)

    def test_response_byte_limit_is_enforced(self):
        with provider(raw=b'x' * 1025) as (endpoint, _), patch('app.fusion.engine.MAX_RESPONSE_BYTES', 1024):
            with self.assertRaisesRegex(FusionError, 'byte limit'):
                synthesize(self.packet, endpoint=endpoint, model='test-only')

    def test_package_file_count_and_total_size_limits(self):
        (self.a/'extra.txt').write_text('data')
        with patch('app.fusion.packages.MAX_FILES', 1):
            with self.assertRaises(FusionError):
                snapshot(self.a)
        with patch('app.fusion.packages.MAX_TOTAL_BYTES', 4):
            with self.assertRaises(FusionError):
                snapshot(self.a)


    def test_endpoint_does_not_accept_remote_http_or_embedded_credentials(self):
        for url in ['http://example.com/v1/chat/completions', 'https://user:secret@example.com/chat/completions',
                    'https://example.com/chat/completions?api_key=bad', 'file:///chat/completions',
                    'https://example.com/wrong']:
            with self.assertRaises(FusionError):
                check_endpoint(url)
        check_endpoint('https://example.com/v1/chat/completions')
        check_endpoint('http://127.0.0.1:11434/v1/chat/completions')

    def test_cli_prepare_preview_and_run_and_preserve_existing_output(self):
        packet_path, preview_path, out = [self.root / f'{n}.json' for n in ('packet', 'preview', 'result')]
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(main(['prepare', '--a', str(self.a), '--b', str(self.b), '--goal', 'Explore', '--out', str(packet_path)]), 0)
            self.assertEqual(main(['preview', '--packet', str(packet_path), '--out', str(preview_path)]), 0)
            packet = read_packet(packet_path)
            self.assertEqual(json.loads(preview_path.read_text(encoding='utf-8'))['messages'], messages(packet))
            with provider(proposed(packet)) as (endpoint, captured):
                args = ['run', '--packet', str(packet_path), '--endpoint', endpoint, '--model', 'test-only', '--out', str(out)]
                self.assertEqual(main(args), 0)
                first = out.read_bytes()
                self.assertEqual(main(args), 2)
                self.assertEqual(out.read_bytes(), first)
                self.assertEqual(len(captured), 1)


class FencedJsonTests(unittest.TestCase):
    """**2026-10-02 实测逼出来的一条**：有些家把 JSON 包在 markdown 围栏里。

    MiniMax M3（`thinking: disabled`、`max_completion_tokens=16384`）：
      `finish_reason = stop`、`usage.completion_tokens = 12,784`、正文 34,254 字，
      `response_format: json_object` **确实发了**——正文却是：

          ```json
          { … }
          ```

    `json.loads` 在第 0 个字符就失败，六次完整流程里 **4 次死在这一步**。
    换句话说这不是模型能力问题，是本产品不认围栏。

    ## 这条守卫真正的重点在「必须继续失败」的那几条

    放宽解析很容易，放宽成「从一堆文本里猜哪段是答案」就等于把提示词注入
    的口子重新打开——模型一次吐两个 JSON 也能过。所以只剥**包住整个响应**的
    那一对围栏，两对以上一律继续失败。**这些负向断言才是它有价值的部分。**
    """

    def test_a_whole_response_fence_is_removed(self):
        for text in ('```json\n{"a":1}\n```', '```\n{"a":1}\n```',
                     '```JSON\n{"a":1}\n```', '\n```json\r\n{"a":1}\r\n```\n'):
            with self.subTest(text=text):
                self.assertEqual(parse_json(text), {'a': 1})

    def test_unfenced_json_still_works(self):
        self.assertEqual(parse_json('{"a":1}'), {'a': 1})

    def test_two_answers_are_still_rejected(self):
        """**两对围栏 = 两个答案，不是「一个被包起来的答案」。**
        这种情况必须继续失败——替模型挑一个，就等于把注入的口子打开。"""
        with self.assertRaises(FusionError):
            parse_json('```json\n{"a":1}\n```\n```json\n{"b":2}\n```')

    def test_a_fence_with_prose_around_it_is_rejected(self):
        """**围栏必须包住整个响应。**前后有解释文字说明模型没按格式答，
        这时要让它继续失败，而不是「聪明地」把中间那段抠出来。"""
        with self.assertRaises(FusionError):
            parse_json('好的，这是结果：\n```json\n{"a":1}\n```\n希望有帮助。')

    def test_fencing_does_not_relax_the_json_checks(self):
        """剥围栏**不是**放松校验：重复键与非有限数字照样拒绝。
        「能解析出来」和「通过契约」是两件事，这里守的是前者没被放宽。"""
        for body in ('{"a":1,"a":2}', '{"value":NaN}'):
            with self.subTest(body=body):
                with self.assertRaises(FusionError):
                    parse_json('```json\n%s\n```' % body)

    def test_a_quote_containing_fences_is_still_accepted(self):
        """**引文里带 ``` 不能被误判成「两个答案」。**

        这是把检查写成「body 里出现 ``` 就拒绝」时踩到的假阴性：
        证据引文完全可能包含 markdown 围栏。判据必须是**行首**的围栏——
        JSON 字符串里的换行只能写成 `\\n` 两个字符，所以字符串值内的 ``` 永远不会在行首。"""
        payload = json.dumps({'quote': '示例：\n```python\nprint(1)\n```', 'a': 1})
        self.assertEqual(parse_json('```json\n%s\n```' % payload)['a'], 1)

    def test_unfence_returns_the_text_untouched_when_it_is_not_a_whole_fence(self):
        from app.fusion.engine import unfence
        for text in ('{"a":1}', 'prefix ```json\n{}\n```', 42, None, ''):
            with self.subTest(text=text):
                self.assertEqual(unfence(text), text)


if __name__ == '__main__':
    unittest.main()
