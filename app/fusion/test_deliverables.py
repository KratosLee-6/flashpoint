"""Synthetic-only integrity, filesystem and authenticated-download regressions."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib import error, request
import warnings
import zipfile

from . import deliverables
from .packages import FusionError
from .server import Workspace, make_server


def write_synthetic_deliverable(root, artifact_id='synthetic-skill-v0-1-0', report=True, comparison=None, **changes):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    files = {'synthetic-skill/SKILL.md': b'# Synthetic Skill\nUse the supplied synthetic example only.\n',
             'synthetic-skill/references/example.txt': b'Synthetic source, not customer material.\n'}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('synthetic-skill/', b'')
        for name, content in files.items():
            archive.writestr(name, content)
    data = buffer.getvalue()
    manifest = {
        'schema_version': 1, 'id': artifact_id, 'title': 'Synthetic Skill',
        'version': '0.1.0-experimental', 'status': 'experimental_unverified',
        'description': 'Synthetic downloadable package, not a candidate-plan export.',
        'mechanism': 'Compare a supplied synthetic example with a synthetic rubric.',
        'workflow': ['Read the synthetic example', 'Return a sourced observation'],
        'limitations': ['Text only; no human verification or fusion-value claim.'],
        'sha256': hashlib.sha256(data).hexdigest(), 'size_bytes': len(data),
        'files': [{'path': name, 'sha256': hashlib.sha256(content).hexdigest()} for name, content in files.items()],
        'evidence': {'status': 'ai_independent_review', 'summary': 'Synthetic AI review is not human verification.',
                     'task_count': 2, 'arm_count': 5, 'run_count': 10, 'human_reviewed': False},
    }
    if comparison is not None:
        manifest['evidence']['comparison'] = comparison
    if report:
        manifest['evidence']['report_file'] = artifact_id + '-evidence.json'
        (root / manifest['evidence']['report_file']).write_text(json.dumps({
            'marker': 'SYNTHETIC_EVIDENCE_BODY_NOT_A_DESCRIPTOR',
            'note': '<img src=x onerror="window.deliverableXss=9">',
            'human_reviewed': False, 'value_verified': False,
        }), encoding='utf-8')
    manifest.update(changes)
    (root / (artifact_id + '.zip')).write_bytes(data)
    (root / (artifact_id + '.json')).write_text(json.dumps(manifest), encoding='utf-8')
    return manifest


class DeliverableTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'deliverables'
        self.manifest = write_synthetic_deliverable(self.root)
        self.artifact_id = self.manifest['id']
        self.store = deliverables.Deliverables(self.root)

    def save(self, manifest):
        (self.root / (self.artifact_id + '.json')).write_text(json.dumps(manifest), encoding='utf-8')

    def package(self, entries, compression=zipfile.ZIP_STORED):
        buffer = io.BytesIO()
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(buffer, 'w', compression=compression) as archive:
                for name, content in entries:
                    archive.writestr(name, content)
        data = buffer.getvalue()
        manifest = copy.deepcopy(self.manifest)
        manifest['sha256'], manifest['size_bytes'] = hashlib.sha256(data).hexdigest(), len(data)
        manifest['files'] = [{'path': name.filename if isinstance(name, zipfile.ZipInfo) else name,
                              'sha256': hashlib.sha256(content).hexdigest()}
                             for name, content in entries
                             if not (name.filename if isinstance(name, zipfile.ZipInfo) else name).endswith('/')]
        self.save(manifest)
        (self.root / (self.artifact_id + '.zip')).write_bytes(data)

    def assert_invalid(self):
        self.assertEqual(self.store.list(), {'deliverables': [], 'invalid_count': 1})
        for evidence in [False, True]:
            with self.assertRaises(FusionError) as raised:
                self.store.download(self.artifact_id, evidence=evidence)
            self.assertNotIn(str(self.root), str(raised.exception))

    def test_descriptors_contain_only_checked_metadata_and_no_value_claim(self):
        result = self.store.list()
        self.assertEqual(result['invalid_count'], 0)
        item, = result['deliverables']
        self.assertTrue(item['integrity_checked'])
        self.assertTrue(item['evidence_available'])
        self.assertFalse(item['value_verified'])
        self.assertFalse(item['evidence']['human_reviewed'])
        self.assertEqual(item['sha256'], self.manifest['sha256'])
        self.assertEqual(item['size_bytes'], self.manifest['size_bytes'])
        self.assertEqual(item['file_count'], 2)
        self.assertNotIn('files', item)
        self.assertNotIn(str(self.root), json.dumps(item))
        self.assertNotIn('SYNTHETIC_EVIDENCE_BODY_NOT_A_DESCRIPTOR', json.dumps(item))

    def test_exact_outer_folder_names_and_separate_opaque_report(self):
        downloaded = self.store.download(self.artifact_id)
        self.assertEqual(downloaded.data, (self.root / (self.artifact_id + '.zip')).read_bytes())
        with zipfile.ZipFile(io.BytesIO(downloaded.data)) as archive:
            self.assertEqual([name for name in archive.namelist() if not name.endswith('/')],
                             [item['path'] for item in self.manifest['files']])
            self.assertNotIn(self.artifact_id + '-evidence.json', archive.namelist())
        report = self.store.download(self.artifact_id, evidence=True)
        self.assertIn('<img', json.loads(report.data)['note'])
        self.assertEqual(report.filename, self.artifact_id + '-evidence.json')

    def test_a_zipped_proposal_is_not_a_skill_package(self):
        for entries in [ [('proposal.json', b'{"plan":"not a Skill"}')],
                         [('skill/SKILL.md', b' ')],
                         [('skill/SKILL.md', b'\xff')],
                         [('skill/SKILL.md', b'# Skill'), ('other/notes.md', b'Outside root')] ]:
            with self.subTest(entries=entries):
                self.package(entries)
                self.assert_invalid()

    def test_no_evidence_report_and_permitted_statuses_do_not_imply_verified(self):
        for status in deliverables.STATUSES:
            with self.subTest(status=status):
                manifest = copy.deepcopy(self.manifest)
                manifest['status'] = status
                manifest['value_verified'] = False
                manifest['evidence'].pop('report_file')
                self.save(manifest)
                item, = self.store.list()['deliverables']
                self.assertFalse(item['value_verified'])
                self.assertFalse(item['evidence_available'])
                self.store.download(self.artifact_id)
                with self.assertRaises(FusionError):
                    self.store.download(self.artifact_id, evidence=True)

    def test_default_is_private_explicit_and_init_does_not_read_disk(self):
        with patch.object(deliverables.os, 'scandir', side_effect=AssertionError('No discovery')):
            workspace = Workspace()
        self.assertEqual(workspace.deliverables.root,
                         Path(__file__).absolute().parents[1] / '.fusion-runs' / 'deliverables')
        absent = self.root / 'not-created'
        self.assertEqual(deliverables.Deliverables(absent).list(), {'deliverables': [], 'invalid_count': 0})
        self.assertFalse(absent.exists())

    def test_only_direct_explicit_manifests_are_read_not_secrets_or_nested_files(self):
        write_synthetic_deliverable(self.root / 'nested', artifact_id='nested-skill')
        for name in ['secrets.json', 'credentials.json', '.env', 'unrelated.txt']:
            (self.root / name).write_text('SYNTHETIC_SECRET_DO_NOT_LOAD', encoding='utf-8')
        with patch.object(self.store, '_read', wraps=self.store._read) as read:
            self.assertEqual(len(self.store.list()['deliverables']), 1)
        self.assertEqual({call.args[0] for call in read.call_args_list},
                         {self.artifact_id + suffix for suffix in ['.json', '.zip', '-evidence.json']})

    def test_id_with_evidence_suffix_is_still_a_valid_manifest(self):
        write_synthetic_deliverable(self.root, artifact_id='synthetic-evidence')
        self.assertEqual(len(self.store.list()['deliverables']), 2)

    def test_invalid_ids_and_arbitrary_request_paths_never_read_files(self):
        workspace = Workspace(deliverables_root=self.root)
        with patch.object(workspace.deliverables, '_read', side_effect=AssertionError('Unexpected read')):
            for artifact_id in ['', '../private', '/private', 'C:\\private', 'UPPER', 'a/b', 'a%2fb',
                                '-slug', 'a' * 65, 'nul', 'credentials', 'secrets', 'with\nheader', None, 1, {}, []]:
                with self.subTest(artifact_id=artifact_id), self.assertRaises(FusionError):
                    workspace.call('deliverables/download', {'id': artifact_id})
            for route, body in [('deliverables', {'path': str(self.root)}),
                                ('deliverables/download', {'id': self.artifact_id, 'path': 'secrets.json'}),
                                ('deliverables/evidence', {'id': self.artifact_id, 'report_file': 'secrets.json'}),
                                ('deliverables/download', {}), ('deliverables', [])]:
                with self.subTest(route=route, body=body), self.assertRaises(FusionError):
                    workspace.call(route, body)

    def test_invalid_manifest_fields_cannot_become_trusted(self):
        changes = [{'schema_version': True}, {'schema_version': 2}, {'id': 'other-id'},
                   {'status': 'value_verified'}, {'status': []}, {'value_verified': True},
                   {'value_verified': 0}, {'trusted': True}, {'size_bytes': True}, {'size_bytes': -1},
                   {'sha256': '0' * 64}, {'title': ''}, {'title': 'x' * 201},
                   {'workflow': 'not an array'}, {'workflow': [3]}, {'limitations': ['x' * 2001]},
                   {'files': []}, {'files': [self.manifest['files'][0]]}]
        for change in changes:
            with self.subTest(change=change):
                manifest = copy.deepcopy(self.manifest)
                manifest.update(change)
                self.save(manifest)
                self.assert_invalid()
        manifest = copy.deepcopy(self.manifest)
        del manifest['mechanism']
        self.save(manifest)
        self.assert_invalid()

    def test_invalid_evidence_claims_and_paths_invalidate_manifest(self):
        for change in [{'human_reviewed': True}, {'human_reviewed': 0}, {'value_verified': True},
                       {'task_count': True}, {'arm_count': -1}, {'run_count': 1000001},
                       {'report_file': '../secret.json'}, {'report_file': 'another-evidence.json'},
                       {'report_file': self.artifact_id + '.json'}, {'report_file': None}]:
            with self.subTest(change=change):
                manifest = copy.deepcopy(self.manifest)
                manifest['evidence'].update(change)
                self.save(manifest)
                self.assert_invalid()

    def test_optional_comparison_is_bounded_and_never_verifies_value(self):
        rows = [{'arm': arm, 'passed': index, 'functional_total': index * 5, 'failures': 6 - index}
                for index, arm in enumerate(deliverables.COMPARISON_ARMS)]
        for comparison in [[], rows[:1], rows]:
            with self.subTest(comparison=comparison):
                manifest = copy.deepcopy(self.manifest)
                manifest['evidence']['status'] = 'model_review_complete_human_pending'
                manifest['evidence']['comparison'] = comparison
                self.save(manifest)
                item, = self.store.list()['deliverables']
                self.assertEqual(item['evidence']['comparison'], comparison)
                self.assertFalse(item['value_verified'])
                self.assertFalse(item['evidence']['human_reviewed'])
        bad_rows = [None, {}, rows + [rows[0]], [rows[0], rows[0]],
                    [{**rows[0], 'arm': 'unknown'}], [{**rows[0], 'arm': []}],
                    [{**rows[0], 'passed': -1}], [{**rows[0], 'passed': 7}],
                    [{**rows[0], 'passed': True}], [{**rows[0], 'functional_total': 31}],
                    [{**rows[0], 'functional_total': 1.5}], [{**rows[0], 'failures': 7}],
                    [{**rows[0], 'failures': -1}], [{**rows[0], 'value_verified': True}]]
        for comparison in bad_rows:
            with self.subTest(comparison=comparison):
                manifest = copy.deepcopy(self.manifest)
                manifest['evidence']['comparison'] = comparison
                self.save(manifest)
                self.assert_invalid()

    def test_opaque_report_never_promotes_its_own_claims_to_descriptor(self):
        (self.root / (self.artifact_id + '-evidence.json')).write_text(
            '{"value_verified":true,"human_reviewed":true,"html":"<script>alert(1)</script>"}', encoding='utf-8')
        item, = self.store.list()['deliverables']
        self.assertFalse(item['value_verified'])
        self.assertFalse(item['evidence']['human_reviewed'])

    def test_manifest_and_reports_are_bounded_strict_json(self):
        for suffix in ['.json', '-evidence.json']:
            path = self.root / (self.artifact_id + suffix)
            original = path.read_bytes()
            for content in [b'not json', b'{"same":1,"same":2}', b'NaN', b'Infinity', b'1e999',
                            b'"\\ud800"', b'\xff', b'[' * 1000 + b'0' + b']' * 1000,
                            b' ' * (deliverables.MAX_EVIDENCE_BYTES + 1)]:
                with self.subTest(suffix=suffix, content=content[:30]):
                    path.write_bytes(content)
                    self.assert_invalid()
            path.write_bytes(original)

    def test_zip_and_member_digests_are_checked_again_on_every_download(self):
        self.store.list()
        zip_path = self.root / (self.artifact_id + '.zip')
        original = zip_path.read_bytes()
        zip_path.write_bytes(original + b'changed')
        self.assert_invalid()
        zip_path.write_bytes(original)
        manifest = copy.deepcopy(self.manifest)
        manifest['files'][0]['sha256'] = '0' * 64
        self.save(manifest)
        self.assert_invalid()
        manifest['files'][0]['path'] = 'SKILL.md'
        self.save(manifest)
        self.assert_invalid()

    def test_corrupt_zip_crc_cannot_hide_behind_updated_archive_digest(self):
        self.package([('synthetic/SKILL.md', b'SYNTHETIC_CRC_CHECK')])
        path = self.root / (self.artifact_id + '.zip')
        data = path.read_bytes().replace(b'SYNTHETIC_CRC_CHECK', b'SYNTHETIC_CRC_BREAK')
        manifest = json.loads((self.root / (self.artifact_id + '.json')).read_text())
        manifest['sha256'] = hashlib.sha256(data).hexdigest()
        self.save(manifest)
        path.write_bytes(data)
        self.assert_invalid()

    def test_unsafe_secret_and_ambiguous_zip_entries_are_rejected(self):
        for name in ['../SKILL.md', '/SKILL.md', 'C:/SKILL.md', 'skill\\SKILL.md',
                     'skill/./SKILL.md', 'skill//SKILL.md', 'skill/nul.txt', 'skill/file:stream',
                     'skill/trailing. /SKILL.md', 'skill/bad?.md', 'skill/.env',
                     'skill/credentials.json', 'skill/.ssh/id_rsa', 'skill/private.key',
                     'skill/.git/config', 'skill/runtime.sqlite', 'skill/bad\x00name']:
            with self.subTest(name=name):
                self.package([(name, b'Synthetic content')])
                self.assert_invalid()
        for entries in [[('skill/SKILL.md', b'a'), ('skill/SKILL.md', b'b')],
                        [('skill/SKILL.md', b'a'), ('skill/skill.md', b'b')],
                        [('skill/SKILL.md', b'a'), ('Skill/notes.md', b'b')],
                        [('skill', b'a'), ('skill/SKILL.md', b'b')]]:
            with self.subTest(entries=entries):
                self.package(entries)
                self.assert_invalid()

    def test_archive_only_extra_entries_are_rejected_even_if_not_in_manifest(self):
        self.package([('synthetic/SKILL.md', b'visible'), ('synthetic/.env', b'SYNTHETIC_ONLY')])
        manifest = json.loads((self.root / (self.artifact_id + '.json')).read_text())
        manifest['files'] = manifest['files'][:1]
        self.save(manifest)
        self.assert_invalid()

    def test_symlink_reparse_device_and_encrypted_zip_entries_are_rejected(self):
        for mode, attributes in [(stat.S_IFLNK, 0), (stat.S_IFIFO, 0), (stat.S_IFCHR, 0),
                                 (stat.S_IFREG, 0x400), (stat.S_IFREG, 0x10)]:
            entry = zipfile.ZipInfo('synthetic/SKILL.md')
            entry.create_system = 3
            entry.external_attr = ((mode | 0o600) << 16) | attributes
            with self.subTest(mode=mode, attributes=attributes):
                self.package([(entry, b'Synthetic content')])
                self.assert_invalid()
        self.package([('synthetic/SKILL.md', b'Synthetic content')])
        path = self.root / (self.artifact_id + '.zip')
        data = bytearray(path.read_bytes())
        central = data.index(b'PK\x01\x02')
        data[6] |= 1
        data[central + 8] |= 1
        manifest = json.loads((self.root / (self.artifact_id + '.json')).read_text())
        manifest['sha256'] = hashlib.sha256(data).hexdigest()
        self.save(manifest)
        path.write_bytes(data)
        self.assert_invalid()

    def test_individual_aggregate_count_and_expansion_limits(self):
        for setting, limit in [('MAX_MANIFEST_BYTES', 8), ('MAX_EVIDENCE_BYTES', 8), ('MAX_ZIP_BYTES', 8),
                               ('MAX_MEMBER_BYTES', 8), ('MAX_UNPACKED_BYTES', 8), ('MAX_FILES', 1)]:
            with self.subTest(setting=setting), patch.object(deliverables, setting, limit):
                self.assert_invalid()
        with patch.object(deliverables, 'MAX_DIRECTORY_ENTRIES', 1), self.assertRaises(FusionError):
            self.store.list()
        with patch.object(deliverables, 'MAX_ARTIFACTS', 0), self.assertRaises(FusionError):
            self.store.list()
        self.package([('synthetic/SKILL.md', b'a' * 100000)], compression=zipfile.ZIP_DEFLATED)
        self.assert_invalid()

    def test_reparse_attributes_on_any_parent_or_file_are_rejected(self):
        original = Path.lstat
        for target in [self.root.parent, self.root, self.root / (self.artifact_id + '.json'),
                       self.root / (self.artifact_id + '.zip'), self.root / (self.artifact_id + '-evidence.json')]:
            def marked(path):
                info = original(path)
                if path == target:
                    return SimpleNamespace(**{key: getattr(info, key) for key in ['st_mode', 'st_size', 'st_nlink',
                                           'st_dev', 'st_ino', 'st_mtime_ns', 'st_ctime_ns']}, st_file_attributes=0x400)
                return info
            with self.subTest(target=target.name), patch.object(Path, 'lstat', marked), self.assertRaises(FusionError):
                self.store.download(self.artifact_id)

    def test_real_symlinks_are_rejected_when_supported(self):
        alias = self.root.parent / 'alias'
        try:
            alias.symlink_to(self.root, target_is_directory=True)
        except OSError:
            self.skipTest('Creating symlinks requires privileges on this host; reparse checks tested separately')
        with self.assertRaises(FusionError):
            deliverables.Deliverables(alias).list()

    def test_hardlinked_files_and_open_time_replacements_are_rejected(self):
        original = os.fstat
        def changed(descriptor):
            info = original(descriptor)
            return SimpleNamespace(**{key: getattr(info, key) for key in ['st_mode', 'st_size', 'st_nlink',
                                   'st_dev', 'st_mtime_ns', 'st_ctime_ns']}, st_ino=info.st_ino + 1)
        with patch.object(deliverables.os, 'fstat', changed), self.assertRaises(FusionError):
            self.store.download(self.artifact_id)
        source = self.root / (self.artifact_id + '.zip')
        try:
            os.link(source, self.root.parent / 'synthetic-hardlink.zip')
        except OSError:
            self.skipTest('Hardlinks unavailable on this filesystem')
        self.assert_invalid()


class DeliverableHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest = write_synthetic_deliverable(self.root)
        self.workspace = Workspace(deliverables_root=self.root)
        self.server = make_server(0, self.workspace)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.origin = f'http://127.0.0.1:{self.server.server_port}'

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def call(self, route, body, *, raw_body=None, **headers):
        defaults = {'Origin': self.origin, 'X-Spark-Token': self.workspace.token, 'Content-Type': 'application/json'}
        defaults.update(headers)
        defaults = {key: value for key, value in defaults.items() if value is not None}
        payload = json.dumps(body).encode() if raw_body is None else raw_body
        message = request.Request(self.origin + '/api/' + route, data=payload, headers=defaults)
        try:
            response = request.urlopen(message, timeout=5)
        except error.HTTPError as failure:
            response = failure
        with response:
            return response.status, response.headers, response.read()

    def test_authenticated_list_and_binary_attachments_never_call_model(self):
        with patch('app.fusion.server.synthesize', side_effect=AssertionError('No paid API')):
            status, headers, data = self.call('deliverables', {})
            self.assertEqual(status, 200)
            self.assertEqual(len(json.loads(data)['deliverables']), 1)
            self.assertIsNone(headers.get('Content-Disposition'))
            for route, suffix, mime in [('download', '.zip', 'application/zip'),
                                        ('evidence', '-evidence.json', 'application/json; charset=utf-8')]:
                status, headers, data = self.call('deliverables/' + route, {'id': self.manifest['id']})
                self.assertEqual(status, 200)
                self.assertEqual(headers['Content-Type'], mime)
                self.assertEqual(headers['Content-Disposition'], f'attachment; filename="{self.manifest["id"]}{suffix}"')
                self.assertEqual(headers['Content-Length'], str(len(data)))
                self.assertEqual(headers['X-Spark-SHA256'], hashlib.sha256(data).hexdigest())
                self.assertEqual(headers['Cache-Control'], 'no-store')
                self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
                self.assertIsNone(headers.get('Access-Control-Allow-Origin'))
                self.assertEqual(data, (self.root / (self.manifest['id'] + suffix)).read_bytes())

    def test_all_routes_require_token_same_origin_and_loopback_host(self):
        # 「无 Origin 头」不在此列：它只可能来自本机进程，由下一个测试单独约定。
        for route in ['deliverables', 'deliverables/download', 'deliverables/evidence']:
            for headers in [{'X-Spark-Token': None}, {'X-Spark-Token': 'wrong'},
                            {'Origin': 'https://example.invalid'}, {'Sec-Fetch-Site': 'cross-site'},
                            {'Host': 'example.invalid'}]:
                with self.subTest(route=route, headers=headers):
                    with patch.object(self.workspace, 'call', side_effect=AssertionError('Denied request reached workspace')):
                        status, _, data = self.call(route, None, raw_body=b'', **headers)
                    self.assertEqual(status, 403)
                    self.assertNotIn(self.manifest['sha256'], data.decode())

    def test_loopback_call_without_origin_header_is_allowed_with_token(self):
        # 0c3dcca 起：无 Origin 头视为本机进程调用（curl / 冒烟脚本），不是跨站请求——
        # 浏览器的 fetch/XHR/表单必定带 Origin 或 Sec-Fetch-Site，那两类已在上一测试逐项拒绝。
        # 令牌仍是硬闸门：没有令牌或令牌不对一律 403。
        status, _, _ = self.call('deliverables', {}, **{'Origin': None})
        self.assertEqual(status, 200)
        for headers in [{'X-Spark-Token': 'wrong'}, {'X-Spark-Token': None}]:
            with self.subTest(headers=headers):
                status, _, _ = self.call('deliverables', {}, **{'Origin': None, **headers})
                self.assertEqual(status, 403)

    def test_get_and_static_paths_never_serve_private_artifacts(self):
        for path in ['/api/deliverables', '/api/deliverables/download', '/api/deliverables/evidence',
                     '/app/.fusion-runs/deliverables/' + self.manifest['id'] + '.zip',
                     '/app/../.fusion-runs/deliverables/' + self.manifest['id'] + '.json']:
            with self.subTest(path=path), self.assertRaises(error.HTTPError) as raised:
                request.urlopen(self.origin + path, timeout=5)
            self.assertEqual(raised.exception.code, 404)

    def test_bad_artifact_and_path_bodies_return_safe_errors_without_attachment(self):
        for route, body in [('deliverables', {'root': str(self.root)}),
                            ('deliverables/download', {'id': '../secrets'}),
                            ('deliverables/evidence', {'id': self.manifest['id'], 'path': 'secrets.json'})]:
            status, headers, data = self.call(route, body)
            self.assertEqual(status, 400)
            self.assertIsNone(headers.get('Content-Disposition'))
            self.assertNotIn(str(self.root), data.decode())
        (self.root / (self.manifest['id'] + '.zip')).write_bytes(b'not a zip')
        status, _, data = self.call('deliverables', {})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data), {'deliverables': [], 'invalid_count': 1})
        for route in ['download', 'evidence']:
            status, headers, _ = self.call('deliverables/' + route, {'id': self.manifest['id']})
            self.assertEqual(status, 400)
            self.assertIsNone(headers.get('Content-Disposition'))


if __name__ == '__main__':
    unittest.main()
