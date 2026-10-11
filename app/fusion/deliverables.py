"""Authenticated handoff of explicitly staged, untrusted local Skill artifacts."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import unicodedata
import zipfile
import zlib

from .packages import FusionError, excluded, safe_path

DEFAULT_ROOT = Path(__file__).absolute().parents[1] / '.fusion-runs' / 'deliverables'
MAX_MANIFEST_BYTES = 128 * 1024
MAX_EVIDENCE_BYTES = 512 * 1024
MAX_ZIP_BYTES = 16 * 1024 * 1024
MAX_MEMBER_BYTES = 2 * 1024 * 1024
MAX_UNPACKED_BYTES = 16 * 1024 * 1024
MAX_FILES = 256
MAX_ARTIFACTS = 32
MAX_DIRECTORY_ENTRIES = 256
STATUSES = {'experimental_unverified', 'rejected', 'inconclusive', 'reviewed_smoke_pass'}
COMPARISON_ARMS = ('A_only', 'B_only', 'mechanical_concatenation', 'model_only',
                   'fusion', 'fusion_without_A', 'fusion_without_B')
ID = re.compile(r'[a-z0-9][a-z0-9-]{0,63}')
SHA256 = re.compile(r'[0-9a-f]{64}')
INVALID = '交付物未通过格式、完整性或安全校验；未提供下载，请重新准备后刷新。'


def _require(condition):
    if not condition:
        raise FusionError(INVALID)


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _text(value, limit):
    _require(type(value) is str and bool(value.strip()) and len(value) <= limit)


def _integer(value, limit):
    _require(type(value) is int and 0 <= value <= limit)


def _sha(value):
    _require(type(value) is str and SHA256.fullmatch(value) is not None)


def _artifact_id(value):
    _require(type(value) is str and ID.fullmatch(value) is not None)
    safe_path(value + '.json')
    _require(not _sensitive(value + '.json'))
    return value


def _sensitive(name):
    return excluded(name) or any(part.casefold() in {
        '.ssh', '.aws', '.azure', '.gnupg', 'credentials', 'secrets',
        'id_rsa', 'id_ed25519', 'id_ecdsa', 'id_dsa', '.netrc',
    } for part in name.split('/'))


def _member_path(name):
    _text(name, 240)
    safe_path(name)
    _require(not _sensitive(name) and not any(char in name for char in '<>"|?*')
             and not any(ord(char) == 127 for char in name)
             and name == unicodedata.normalize('NFC', name))
    return name


def _json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result)
            result[key] = value
        return result

    def constant(_value):
        raise FusionError(INVALID)

    value = json.loads(data.decode('utf-8-sig'), object_pairs_hook=pairs, parse_constant=constant)
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        _require(depth <= 32)
        if type(item) is dict:
            pending.extend((child, depth + 1) for child in item.values())
            pending.extend((key, depth + 1) for key in item)
        elif type(item) is list:
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is float:
            _require(math.isfinite(item))
        elif type(item) is str:
            item.encode('utf-8')
    return value


def _manifest(value, artifact_id):
    required = {'schema_version', 'id', 'title', 'version', 'status', 'description',
                'mechanism', 'workflow', 'limitations', 'sha256', 'size_bytes', 'files', 'evidence'}
    _require(type(value) is dict and required <= value.keys()
             and value.keys() <= required | {'value_verified'})
    _require(type(value['schema_version']) is int and value['schema_version'] == 1
             and value['id'] == artifact_id and value.get('value_verified', False) is False)
    _text(value['status'], 40)
    _require(value['status'] in STATUSES)
    for key, limit in [('title', 200), ('version', 64), ('description', 4000), ('mechanism', 4000)]:
        _text(value[key], limit)
    for key in ['workflow', 'limitations']:
        _require(type(value[key]) is list and len(value[key]) <= 32)
        for item in value[key]:
            _text(item, 2000)
    _sha(value['sha256'])
    _integer(value['size_bytes'], MAX_ZIP_BYTES)
    _require(value['size_bytes'] > 0 and type(value['files']) is list
             and 0 < len(value['files']) <= MAX_FILES)
    paths = set()
    for item in value['files']:
        _require(type(item) is dict and set(item) == {'path', 'sha256'})
        key = _member_path(item['path']).casefold()
        _require(key not in paths)
        paths.add(key)
        _sha(item['sha256'])
    evidence = value['evidence']
    fields = {'status', 'summary', 'task_count', 'arm_count', 'run_count', 'human_reviewed'}
    _require(type(evidence) is dict and fields <= evidence.keys()
             and evidence.keys() <= fields | {'report_file', 'comparison'})
    _text(evidence['status'], 80)
    _text(evidence['summary'], 4000)
    _require(evidence['human_reviewed'] is False)
    for key in ['task_count', 'arm_count', 'run_count']:
        _integer(evidence[key], 1000000)
    if 'report_file' in evidence:
        _require(evidence['report_file'] == artifact_id + '-evidence.json')
    if 'comparison' in evidence:
        rows = evidence['comparison']
        _require(type(rows) is list and len(rows) <= 7)
        arms = set()
        for row in rows:
            _require(type(row) is dict and set(row) == {'arm', 'passed', 'functional_total', 'failures'})
            _require(type(row['arm']) is str and row['arm'] in COMPARISON_ARMS and row['arm'] not in arms)
            arms.add(row['arm'])
            for key, limit in [('passed', 6), ('functional_total', 30), ('failures', 6)]:
                _integer(row[key], limit)
    return value


def _archive(data, manifest):
    _require(len(data) == manifest['size_bytes'] and _digest(data) == manifest['sha256'])
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        _require(0 < len(entries) <= MAX_FILES * 2)
        seen, actual, spellings, entrypoints = set(), {}, {}, {}
        total = 0
        for entry in entries:
            _require(entry.orig_filename == entry.filename)
            directory = entry.is_dir()
            name = _member_path(entry.filename[:-1] if directory else entry.filename)
            key = name.casefold()
            _require(key not in seen)
            seen.add(key)
            parts = name.split('/')
            for depth in range(1, len(parts) + 1):
                prefix = '/'.join(parts[:depth])
                _require(spellings.setdefault(prefix.casefold(), prefix) == prefix)
            mode = stat.S_IFMT(entry.external_attr >> 16)
            _require(mode in ({0, stat.S_IFDIR} if directory else {0, stat.S_IFREG})
                     and not entry.external_attr & 0x400
                     and (directory or not entry.external_attr & 0x10)
                     and not entry.flag_bits & 1
                     and entry.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})
            _require(0 <= entry.file_size <= MAX_MEMBER_BYTES
                     and entry.file_size <= max(2048, entry.compress_size * 200))
            total += entry.file_size
            _require(total <= MAX_UNPACKED_BYTES)
            with archive.open(entry) as stream:
                content = stream.read(MAX_MEMBER_BYTES + 1)
            _require(len(content) == entry.file_size and len(content) <= MAX_MEMBER_BYTES)
            if directory:
                _require(not content)
            else:
                actual[name] = _digest(content)
                _require(len(actual) <= MAX_FILES)
                if name == 'SKILL.md' or (name.count('/') == 1 and name.endswith('/SKILL.md')):
                    text = content.decode('utf-8-sig')
                    _require(bool(text.strip()) and '\x00' not in text)
                    entrypoints[name] = text
        files = {name.casefold() for name in actual}
        for name in seen:
            parts = name.split('/')
            _require(not any('/'.join(parts[:depth]) in files for depth in range(1, len(parts))))
        _require(actual == {item['path']: item['sha256'] for item in manifest['files']})
        if 'SKILL.md' not in entrypoints:
            _require(len(entrypoints) == 1)
            root = next(iter(entrypoints)).split('/')[0] + '/'
            _require(all(name.startswith(root) for name in actual))


def _linked(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def _identity(info):
    return info.st_dev, info.st_ino, info.st_mode, getattr(info, 'st_file_attributes', 0)


def _file_stamp(info):
    _require(stat.S_ISREG(info.st_mode) and not _linked(info) and info.st_nlink == 1)
    return _identity(info), info.st_size, info.st_mtime_ns


@dataclass(frozen=True)
class Download:
    data: bytes
    filename: str
    content_type: str

    @property
    def sha256(self):
        return _digest(self.data)


class Deliverables:
    def __init__(self, root=None):
        self.root = Path(root if root is not None else DEFAULT_ROOT).absolute()

    def _root_stamp(self, missing_ok=False):
        _require(not str(self.root).startswith(('\\\\', '//')) and '..' not in self.root.parts)
        result = []
        for parent in [*reversed(self.root.parents), self.root]:
            try:
                info = parent.lstat()
            except FileNotFoundError:
                if missing_ok:
                    return None
                raise FusionError(INVALID) from None
            _require(stat.S_ISDIR(info.st_mode) and not _linked(info))
            result.append(_identity(info))
        return result

    def _read(self, name, limit):
        root_stamp = self._root_stamp()
        path = self.root / name
        info = path.lstat()
        stamp = _file_stamp(info)
        _require(0 < info.st_size <= limit)
        flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
        with os.fdopen(os.open(path, flags), 'rb') as stream:
            _require(_file_stamp(os.fstat(stream.fileno())) == stamp and self._root_stamp() == root_stamp)
            data = stream.read(limit + 1)
            _require(0 < len(data) == info.st_size <= limit
                     and _file_stamp(os.fstat(stream.fileno())) == stamp
                     and _file_stamp(path.lstat()) == stamp and self._root_stamp() == root_stamp)
        return data

    def _load(self, artifact_id):
        try:
            artifact_id = _artifact_id(artifact_id)
            manifest = _manifest(_json(self._read(artifact_id + '.json', MAX_MANIFEST_BYTES)), artifact_id)
            data = self._read(artifact_id + '.zip', MAX_ZIP_BYTES)
            _archive(data, manifest)
            report = None
            if 'report_file' in manifest['evidence']:
                report = self._read(artifact_id + '-evidence.json', MAX_EVIDENCE_BYTES)
                _json(report)
            return manifest, data, report
        except (OSError, ValueError, TypeError, KeyError, RecursionError, RuntimeError,
                zipfile.BadZipFile, zipfile.LargeZipFile, zlib.error):
            raise FusionError(INVALID) from None

    def list(self):
        try:
            root_stamp = self._root_stamp(missing_ok=True)
            if root_stamp is None:
                return {'deliverables': [], 'invalid_count': 0}
            names = set()
            with os.scandir(self.root) as entries:
                for entry in entries:
                    _require(len(names) < MAX_DIRECTORY_ENTRIES)
                    names.add(entry.name)
            _require(self._root_stamp() == root_stamp)
            candidates = sorted(name[:-5] for name in names if name.endswith('.json')
                                and ID.fullmatch(name[:-5]) and not _sensitive(name)
                                and not (name.endswith('-evidence.json') and name[:-5] + '.zip' not in names))
            _require(len(candidates) <= MAX_ARTIFACTS)
            descriptors, invalid = [], 0
            for artifact_id in candidates:
                try:
                    manifest, data, report = self._load(artifact_id)
                except FusionError:
                    invalid += 1
                    continue
                descriptors.append({**{key: value for key, value in manifest.items() if key != 'files'},
                                    'sha256': _digest(data), 'size_bytes': len(data),
                                    'file_count': len(manifest['files']), 'integrity_checked': True,
                                    'value_verified': False, 'evidence_available': report is not None})
            return {'deliverables': descriptors, 'invalid_count': invalid}
        except (OSError, ValueError):
            raise FusionError(INVALID) from None

    def download(self, artifact_id, evidence=False):
        manifest, data, report = self._load(artifact_id)
        if evidence:
            if report is None:
                raise FusionError('此交付物没有可下载的证据报告；尚未证明融合收益。')
            return Download(report, manifest['id'] + '-evidence.json', 'application/json; charset=utf-8')
        return Download(data, manifest['id'] + '.zip', 'application/zip')
