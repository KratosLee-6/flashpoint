"""`app.fusion.skillhub` 与 server 路由的强制测试——零真实网络。

    python -m unittest app.fusion.test_skillhub

## 守什么（45 号文批次 3d：SkillHub 在线搜索 + 下载）

- **出网白名单**：`_guard_redirect` 只放行 https + api.skillhub.cn / *.myqcloud.com
  ——「跟随重定向」不能变成任意外联；
- **完整性**：逐文件 sha256 对清单校验，不符即整体拒绝，不留半个包；
- **路径安全**：清单路径过 `safe_path`；敏感文件（.env 等）整包拒绝；
- **上限**：文件数、单文件字节、总量三重封顶；缺 SKILL.md 直接拒绝；
- **路由**：`skillhub/download` 是后台 job（不阻塞工作区），异常如实上报。

## 反向验证

把 `download` 的 sha256 比对改掉、或删掉 `_guard_redirect` 的主机检查，
下面的用例会分别变红。
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.fusion.packages import FusionError
from app.fusion.server import make_server, Workspace
from app.fusion.skillhub import (API_HOST, MAX_FILES, MAX_FILE_BYTES, _guard_redirect,
                                  download, pack_dir, search)


def _fake_open(routes):
    """按 URL 前缀分发的假响应。routes: [(prefix, bytes)]，未命中即失败。"""
    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload
        def read(self, n=-1):
            return self.payload if n == -1 else self.payload[:n]
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    def opener(url, *args, **kwargs):
        for prefix, payload in routes:
            if url.startswith(prefix):
                return FakeResponse(payload)
        raise AssertionError('unexpected URL: %s' % url)
    return opener


def _manifest(rows):
    return json.dumps({'count': len(rows),
                       'files': [{'path': p, 'sha256': hashlib.sha256(c).hexdigest(),
                                  'size': len(c)} for p, c in rows]}).encode()


class SearchTests(unittest.TestCase):
    def test_results_are_normalized(self):
        payload = json.dumps({'code': 0, 'data': {'total': 1, 'skills': [{
            'slug': 'anti-fraud', 'name': '防骗大师.Skill', 'description': 'x' * 400,
            'version': '2.1.0', 'downloads': 1027044, 'category': 'safety',
            'verified': True, 'score': 999}]}}).encode()
        with patch('app.fusion.skillhub._open', _fake_open(
                [('https://%s/api/skills' % API_HOST, payload)])):
            report = search('防骗')
        self.assertEqual(report['total'], 1)
        row = report['skills'][0]
        self.assertEqual(row['slug'], 'anti-fraud')
        self.assertEqual(len(row['description']), 300)
        self.assertTrue(row['verified'])
        self.assertEqual(row['downloads'], 1027044)

    def test_malformed_rows_and_bad_code_are_refused(self):
        bad_code = json.dumps({'code': 403, 'message': 'denied'}).encode()
        with patch('app.fusion.skillhub._open', _fake_open(
                [('https://%s/api/skills' % API_HOST, bad_code)])):
            with self.assertRaises(FusionError):
                search('x')
        junk = json.dumps({'code': 0, 'data': {'skills': [{'no_slug': True}, 'x', None]}}).encode()
        with patch('app.fusion.skillhub._open', _fake_open(
                [('https://%s/api/skills' % API_HOST, junk)])):
            self.assertEqual(search('x')['skills'], [])

    def test_input_bounds(self):
        for kwargs in ({'query': 'x' * 101}, {'query': 'x', 'page': 0}, {'query': 'x', 'page': 11},
                       {'query': 'x', 'page_size': 0}, {'query': 'x', 'page_size': 51}, {'query': 5}):
            with self.assertRaises(FusionError):
                search(**kwargs)


class GuardTests(unittest.TestCase):
    def test_allowed_redirects(self):
        _guard_redirect('https://%s/api/v1/skills/x/files' % API_HOST)
        _guard_redirect('https://skillhub-1.cos.accelerate.myqcloud.com/skills/1/files/SKILL.md')

    def test_forbidden_redirects(self):
        for url in ('http://%s/api' % API_HOST,                     # 明文
                    'https://evil.example.com/files',                # 外部主机
                    'https://api.skillhub.cn.evil.com/files',        # 后缀伪装
                    'file:///etc/passwd'):                           # 非 http(s)
            with self.assertRaises(FusionError, msg=url):
                _guard_redirect(url)


class DownloadTests(unittest.TestCase):
    FILES = {'SKILL.md': b'---\nname: t\n---\n# T\n', 'lib/core.py': b'VERSION = 1\n'}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def opener_for(self, rows):
        files_prefix = 'https://%s/api/v1/skills/t/file?path=' % API_HOST
        manifest = _manifest(rows)
        by_path = {p: c for p, c in rows}
        import urllib.parse
        def opener(url, *args, **kwargs):
            if url.startswith('https://%s/api/v1/skills/t/files' % API_HOST):
                return _fake_open([(url, manifest)])(url)
            assert url.startswith(files_prefix), url
            path = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)['path'][0]
            return _fake_open([(url, by_path[path])])(url)
        return opener

    def test_happy_path_writes_verified_pack(self):
        rows = list(self.FILES.items())
        with patch('app.fusion.skillhub._open', self.opener_for(rows)):
            report = download('t', root=self.root)
        self.assertEqual(report['files'], 2)
        self.assertEqual(report['slug'], 't')
        target = self.root / 't'
        self.assertTrue((target / 'SKILL.md').is_file())
        self.assertEqual((target / 'lib' / 'core.py').read_bytes(), b'VERSION = 1\n')
        self.assertEqual(report['path'], str(target))

    def test_sha_mismatch_rejects_whole_pack(self):
        rows = [('SKILL.md', b'---\nname: t\n---\n# T\n'),
                ('lib/core.py', b'VERSION = 1\n')]
        manifest = json.dumps({'count': 2, 'files': [
            {'path': 'SKILL.md', 'sha256': hashlib.sha256(rows[0][1]).hexdigest(), 'size': len(rows[0][1])},
            {'path': 'lib/core.py', 'sha256': '0' * 64, 'size': len(rows[1][1])}]}).encode()
        def opener(url, *args, **kwargs):
            if '/files' in url:
                return _fake_open([(url, manifest)])(url)
            body = b'VERSION = 1\n' if 'core' in url else rows[0][1]
            return _fake_open([(url, body)])(url)
        with patch('app.fusion.skillhub._open', opener):
            with self.assertRaises(FusionError):
                download('t', root=self.root)
        self.assertFalse((self.root / 't').exists(), '失败的下载不允许留下半个包')
        leftovers = [p for p in self.root.iterdir()]
        self.assertEqual(leftovers, [], '临时目录必须清干净')

    def test_traversal_and_sensitive_files_refused(self):
        for rows in ([('../evil.py', b'x')], [('SKILL.md', b'x'), ('.env', b'SECRET=1')],
                     [('SKILL.md', b'x'), ('certs/key.pem', b'x')]):
            with patch('app.fusion.skillhub._open', self.opener_for([rows[0]] if len(rows) == 1 else rows)):
                with self.assertRaises(FusionError):
                    download('t', root=self.root)

    def test_missing_skill_md_is_refused(self):
        with patch('app.fusion.skillhub._open', self.opener_for([('README.md', b'x')])):
            with self.assertRaises(FusionError):
                download('t', root=self.root)

    def test_limits_are_enforced(self):
        many = [('SKILL.md', b'x')] + [('f%03d.py' % i, b'x') for i in range(MAX_FILES)]
        with patch('app.fusion.skillhub._open', self.opener_for(many)):
            with self.assertRaises(FusionError):
                download('t', root=self.root)
        with patch('app.fusion.skillhub._open', self.opener_for(
                [('SKILL.md', b'x'), ('big.bin', b'x' * 8)])):
            with patch('app.fusion.skillhub.MAX_FILE_BYTES', 4):
                with self.assertRaises(FusionError):
                    download('t', root=self.root)

    def test_slug_validation(self):
        for slug in ('Bad_Slug', '../t', '', 'a b', None, 5, 'x' * 80):
            with self.assertRaises(FusionError, msg=repr(slug)):
                pack_dir(slug, root=self.root)


class SkillHubRouteTests(unittest.TestCase):
    """真实 server + 真实 Workspace 的路由级测试（组合夹具，零真实网络）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        import os
        self._old_root = os.environ.get('SKILLHUB_PACKS_ROOT')
        os.environ['SKILLHUB_PACKS_ROOT'] = str(self.root / 'packs')
        self.addCleanup(self._restore_root)
        from .test_server import LoopbackAppFixture
        self.app = LoopbackAppFixture('setUp')
        self.app.setUp()
        self.addCleanup(self.app.doCleanups)

    def _restore_root(self):
        import os
        if self._old_root is None:
            os.environ.pop('SKILLHUB_PACKS_ROOT', None)
        else:
            os.environ['SKILLHUB_PACKS_ROOT'] = self._old_root

    def test_search_route_returns_normalized_list(self):
        payload = json.dumps({'code': 0, 'data': {'total': 1, 'skills': [
            {'slug': 'anti-fraud', 'name': '防骗大师.Skill', 'description': 'd',
             'version': '1.0.0', 'downloads': 5, 'verified': False}]}}).encode()
        with patch('app.fusion.skillhub._open', _fake_open(
                [('https://%s/api/skills' % API_HOST, payload)])):
            status, body = self.app.call('skillhub/search', {'query': '防骗'})
        self.assertEqual(status, 200)
        self.assertEqual(body['skills'][0]['slug'], 'anti-fraud')

    def test_download_route_is_a_pollable_job(self):
        rows = [('SKILL.md', b'---\nname: t\n---\n# T\n')]
        manifest = _manifest(rows)
        files_prefix = 'https://%s/api/v1/skills/t/file?path=' % API_HOST

        def opener(url, *args, **kwargs):
            if url.startswith('https://%s/api/v1/skills/t/files' % API_HOST):
                return _fake_open([(url, manifest)])(url)
            assert url.startswith(files_prefix), url
            return _fake_open([(url, rows[0][1])])(url)

        with patch('app.fusion.skillhub._open', opener):
            status, ack = self.app.call('skillhub/download', {'slug': 't'})
            self.assertEqual(status, 200)
            self.assertEqual(ack['status'], 'running')
            job_id = ack['id']
            # 下载跑在**后台线程**里。断言必须留在 patch 上下文**内部**——
            # 一旦 with 退出，后台线程才真正发出的请求就落到真实网络上了
            # （这个坑真踩过：patch 打空 → 真实请求 → 「清单缺少 SKILL.md」）。
            job = {'status': 'running'}
            for _ in range(200):
                time.sleep(0.05)
                _, job = self.app.call('job', {'id': job_id})
                if job['status'] != 'running':
                    break
            self.assertEqual(job['status'], 'completed', job.get('error'))
            self.assertEqual(job['result']['slug'], 't')
            self.assertEqual(job['result']['files'], 1)
            self.assertTrue((self.root / 'packs' / 't' / 'SKILL.md').is_file())

    def test_download_route_rejects_blank_slug_without_job(self):
        status, body = self.app.call('skillhub/download', {'slug': '   '})
        self.assertEqual(status, 400)
        self.assertIn('slug', body['error'])

