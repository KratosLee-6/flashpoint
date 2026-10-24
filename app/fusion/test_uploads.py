"""Synthetic folder/ZIP imports stay local, bounded and unexecuted."""
import base64
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from . import packages as packages_mod
from .packages import FusionError, snapshot, uploaded_snapshot
from .server import Workspace


def encoded(data):
    return base64.b64encode(data).decode('ascii')


def directory_upload(files):
    return {'kind': 'directory', 'files': [{'path': name, 'content': encoded(data) if data is not None else None}
                                          for name, data in files.items()]}


def zip_upload(files):
    archive = BytesIO()
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as output:
        for name, data in files.items():
            output.writestr(name, data)
    return {'kind': 'zip', 'content': encoded(archive.getvalue())}


class PackageUploadTests(unittest.TestCase):
    def setUp(self):
        self.files = {'SKILL.md': b'# Synthetic skill\nSee [implementation](scripts/run.py) and [missing](references/missing.md).\n',
                      'scripts/run.py': b'raise RuntimeError("must never execute")\n',
                      'templates/card.html': b'<p>Synthetic feedback card</p>',
                      'image.png': b'\x89PNG\x00\xff', '.env': b'SYNTHETIC_SECRET'}

    def test_directory_and_wrapped_zip_match_the_existing_disk_snapshot(self):
        with tempfile.TemporaryDirectory() as location:
            root = Path(location)
            for name, content in self.files.items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            expected = snapshot(root)
        for upload in [directory_upload(self.files), zip_upload({'wrapper/' + name: data for name, data in self.files.items()})]:
            with self.subTest(kind=upload['kind']):
                result = uploaded_snapshot(upload)
                self.assertEqual(result, expected)
                self.assertFalse(result['executed'])
                self.assertNotIn('SYNTHETIC_SECRET', json.dumps(result))
                self.assertTrue(any(item['status'] == 'unresolved' for item in result['references']))

    def test_excluded_files_need_no_body_but_business_files_cannot_be_omitted(self):
        files = {**self.files, '.env': None}
        self.assertEqual(uploaded_snapshot(directory_upload(files)), uploaded_snapshot(directory_upload(self.files)))
        files['scripts/run.py'] = None
        with self.assertRaises(FusionError):
            uploaded_snapshot(directory_upload(files))

    def test_invalid_encodings_paths_collisions_and_archive_entries_fail_closed(self):
        invalid = [None, {}, {'kind': 'zip', 'content': '!bad!'}, {'kind': 'zip', 'content': encoded(b'not zip')},
                   directory_upload({'../SKILL.md': b'# source'}), directory_upload({'SKILL.md': b'# source', 'skill.md': b'# duplicate'}),
                   directory_upload({'SKILL.md': b'# source', 'scripts': b'file', 'scripts/run.py': b'code'}),
                   zip_upload({'../SKILL.md': b'# source'}), directory_upload({'README.md': b'# not a skill package'})]
        for upload in invalid:
            with self.subTest(upload=str(upload)[:70]), self.assertRaises(FusionError):
                uploaded_snapshot(upload)

    def test_file_count_per_file_and_combined_limits_are_not_silently_truncated(self):
        # patch 必须打在**被测代码所在的模块对象**上（2026-09-24 审查：
        # 同一份 packages.py 会以 app.fusion.packages / fusion.packages 两个名字
        # 各导入一次，patch 字符串打错名字就静默打空 —— 断言变永真）。
        with patch.object(packages_mod, 'MAX_FILES', 1), self.assertRaises(FusionError):
            uploaded_snapshot(directory_upload(self.files))
        with patch.object(packages_mod, 'MAX_FILE_BYTES', 4), self.assertRaises(FusionError):
            uploaded_snapshot(directory_upload(self.files))
        with patch.object(packages_mod, 'MAX_TOTAL_BYTES', 32), self.assertRaises(FusionError):
            uploaded_snapshot(directory_upload(self.files))

    def test_uploads_enter_existing_review_without_disk_or_model_calls(self):
        workspace = Workspace()
        with patch('app.fusion.engine.snapshot') as disk, patch('app.fusion.engine.request_model') as model:
            report = workspace.call('packages', {'uploads': {'A': directory_upload(self.files), 'B': zip_upload(self.files)}})
        self.assertEqual(report['intent_mode'], 'explore')
        self.assertIsNone(workspace.packet)
        self.assertEqual(len(report['packages']['A']['files']), len(self.files))
        self.assertNotIn('SYNTHETIC_SECRET', json.dumps(report))
        with self.assertRaises(FusionError):
            workspace.call('preview', {'packet_sha256': report['packet_sha256']})
        disk.assert_not_called()
        model.assert_not_called()

    def test_mixed_paths_explicit_resources_and_uploads_keep_full_references(self):
        with tempfile.TemporaryDirectory() as location:
            root = Path(location)
            (root / 'SKILL.md').write_text('# Synthetic second skill', encoding='utf-8')
            (root / 'extra.md').write_text('Synthetic extra resource', encoding='utf-8')
            report = Workspace().call('packages', {'b': str(root), 'uploads': {'A': directory_upload(self.files)},
                'resources': {'A': [{'mount': 'references/missing.md', 'path': str(root / 'extra.md')}], 'B': []}})
        self.assertIn('references/missing.md', [item['path'] for item in report['packages']['A']['files']])

    def test_malformed_uploads_and_running_jobs_cannot_replace_the_inventory(self):
        workspace = Workspace()
        workspace.call('packages', {'uploads': {'A': directory_upload(self.files), 'B': zip_upload(self.files)}})
        original = workspace.inventory
        for uploads in [{}, [], {'C': directory_upload(self.files)}, {'A': None}]:
            with self.subTest(uploads=uploads), self.assertRaises(FusionError):
                workspace.call('packages', {'uploads': uploads})
            self.assertIs(workspace.inventory, original)
        workspace.running = True
        with self.assertRaises(FusionError):
            workspace.call('packages', {'uploads': {'A': directory_upload(self.files), 'B': zip_upload(self.files)}})
        self.assertIs(workspace.inventory, original)
