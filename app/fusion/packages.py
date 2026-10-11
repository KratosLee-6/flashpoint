"""Read-only Skill package snapshots. Never extracts ZIPs or executes package code."""
from __future__ import annotations

import base64
import binascii
import hashlib
from io import BytesIO
import json
import os
import posixpath
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile

MAX_FILES = 256
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
TEXT_SUFFIXES = {'.md', '.txt', '.py', '.js', '.mjs', '.cjs', '.ts', '.tsx', '.jsx',
                 '.json', '.yaml', '.yml', '.toml', '.csv', '.html', '.css', '.svg', '.sh', '.ps1'}
EXCLUDED_DIRS = {'.git', '.state', 'node_modules', '.venv', 'venv', '__pycache__', 'logs'}
SENSITIVE = {'credentials.json', 'secrets.json', 'state.json', 'pay-output.txt',
             'payment-needed.txt', 'pay-attempt.json', '.npmrc', '.pypirc'}


class FusionError(ValueError):
    """Actionable, safe-to-display failure; never contains provider response bodies."""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_path(name):
    parts = name.split('/')
    if (not name or '\\' in name or any(c in name for c in ':\x00')
            or any(ord(c) < 32 for c in name)
            or any(p in {'', '.', '..'} or p.endswith((' ', '.')) for p in parts)
            or any(re.fullmatch(r'(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', p, re.I) for p in parts)):
        # 点名是哪个路径：2026-10-01 真实采样里这一类占了 3/6，是头号阻塞，
        # 而报错不说 offender 就完全无法排查。回显的是模型自己写出来的字符串，
        # 不含凭据；截断 80 字符避免刷屏。
        raise FusionError(f'Unsafe package-relative path: {name[:80]!r}')
    return name


def excluded(name):
    parts = [p.lower() for p in name.split('/')]
    return (any(p in EXCLUDED_DIRS for p in parts)
            or any(p.startswith('.env') or p in SENSITIVE for p in parts)
            or PurePosixPath(name.lower()).suffix in {'.pem', '.key', '.p12', '.pfx', '.log', '.db', '.sqlite', '.sqlite3'})


def _entry(name, data):
    entry = {'path': name, 'bytes': len(data), 'sha256': digest(data), 'status': 'unparsed'}
    if PurePosixPath(name).suffix.lower() in TEXT_SUFFIXES:
        try:
            text = data.decode('utf-8-sig').replace('\r\n', '\n').replace('\r', '\n')
            if '\x00' in text:
                return entry
            entry.update(status='text', text=text, text_sha256=digest(text.encode('utf-8')),
                         lines=len(text.splitlines()))
        except UnicodeDecodeError:
            pass
    return entry


def document_snapshot(document):
    if type(document) is not dict or set(document) != {'body', 'sha256'}:
        raise FusionError('Each selected Skill requires body and sha256')
    text = document['body']
    if not isinstance(text, str) or not text.strip() or len(text) > 180000:
        raise FusionError('Selected Skill must contain 1–180000 characters')
    try:
        content = text.encode('utf-8')
    except UnicodeError as exc:
        raise FusionError('Selected Skill must be valid UTF-8 text') from exc
    if len(content) > 256000 or '\x00' in text or '\ufffd' in text:
        raise FusionError('Selected Skill exceeds 256 KB or contains invalid text')
    if document['sha256'] != digest(content):
        raise FusionError('Selected Skill fingerprint changed; select and review again')
    entries = [_entry('SKILL.md', content)]
    manifest = [{key: value for key, value in entry.items() if key != 'text'} for entry in entries]
    return {'sha256': digest(canonical(manifest).encode('utf-8')), 'files': entries,
            'references': _references(entries), 'executed': False}


def _is_link(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def _local_source(location):
    if str(location).startswith(('\\\\', '//')):
        raise FusionError('Use a local path, not a network share')
    path = Path(location)
    if any(_is_link(parent.lstat()) for parent in [path, *path.absolute().parents]):
        raise FusionError('Source paths cannot contain links/reparse points')
    return path


def _directory_files(root):
    def unreadable_subtree(error):
        raise FusionError('Cannot enumerate package directory; check read permissions and prepare again') from error

    seen = 0
    for base, dirs, files in os.walk(root, followlinks=False, onerror=unreadable_subtree):
        for name in sorted(dirs + files):
            seen += 1
            if seen > MAX_FILES * 4:
                raise FusionError('Directory entry limit exceeded')
            path = Path(base) / name
            rel = safe_path(path.relative_to(root).as_posix())
            info = path.lstat()
            if _is_link(info):
                raise FusionError('Links/reparse points are not accepted')
            if excluded(rel):
                if name in dirs:
                    dirs.remove(name)
                yield rel, None
                continue
            if stat.S_ISDIR(info.st_mode):
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
                raise FusionError('Unsupported or oversized package file')
            with path.open('rb') as handle:
                opened = os.fstat(handle.fileno())
                if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                    raise FusionError('Package changed while reading; prepare again')
                data = handle.read(MAX_FILE_BYTES + 1)
            after = path.lstat()
            if _is_link(after) or (info.st_size, info.st_mtime_ns, info.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                raise FusionError('Package changed while reading; prepare again')
            yield rel, data


def _zip_files(path):
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_FILES * 4:
            raise FusionError('ZIP entry limit exceeded')
        files, names = [], set()
        for info in infos:
            original = info.orig_filename
            name = safe_path(original[:-1] if original.endswith('/') else original)
            if name.casefold() in names:
                raise FusionError('Duplicate or case-colliding ZIP entry')
            names.add(name.casefold())
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}):
                raise FusionError('ZIP links and special files are not accepted')
            if not info.is_dir():
                files.append((name, info))
        # Accept either SKILL.md at root or one common wrapper directory.
        prefix = ''
        if not any(n == 'SKILL.md' for n, _ in files):
            first = {n.split('/')[0] for n, _ in files}
            if len(first) == 1:
                prefix = next(iter(first)) + '/'
        for name, info in files:
            name = safe_path(name[len(prefix):])
            if excluded(name):
                yield name, None
                continue
            if info.flag_bits & 1 or info.file_size > MAX_FILE_BYTES:
                raise FusionError('Encrypted or oversized ZIP entry')
            with archive.open(info) as handle:
                data = handle.read(MAX_FILE_BYTES + 1)
            yield name, data


def _references(files):
    """Best-effort literal references, not a semantic completeness claim."""
    paths = {f['path'] for f in files}
    refs = []
    for f in files:
        if f['status'] != 'text':
            continue
        for line_number, line in enumerate(f['text'].splitlines(), 1):
            targets = []
            if PurePosixPath(f['path']).suffix.lower() in {'.md', '.txt'}:
                targets += re.findall(r'\[[^\]]*\]\(([^\s)]+)\)', line)
                targets += re.findall(r'(?<![\w./-])((?:scripts|references|assets|lib)/[^\s`\"\'<>|()\[\]{}]+\.[\w]+)', line)
            if f['path'].endswith(('.js', '.ts', '.mjs', '.cjs')):
                targets += re.findall(r"(?:from\s*|require\(\s*|import\s*)['\"](\.[^'\"]+)['\"]", line)
            for target in sorted(set(targets)):
                if re.match(r'^[a-zA-Z][\w+.-]*:', target) or target.startswith('#'):
                    continue
                target = target.split('#')[0]
                rel = posixpath.normpath(str(PurePosixPath(f['path']).parent / target))
                refs.append({'file': f['path'], 'line': line_number, 'target': target,
                             'status': 'present' if rel in paths else 'unresolved'})
    return refs


def _decode_upload(content, limit):
    if not isinstance(content, str) or len(content) > ((limit + 2) // 3) * 4:
        raise FusionError('上传内容缺失或超出包大小限制；未截断材料')
    try:
        data = base64.b64decode(content, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise FusionError('上传文件编码无效；请重新选择完整材料') from exc
    if len(data) > limit:
        raise FusionError('上传内容超出包大小限制；未截断材料')
    return data


def _uploaded_files(files):
    if type(files) is not list or not 0 < len(files) <= MAX_FILES * 4:
        raise FusionError('上传目录为空或文件数量超限；可改用本机路径清点大目录')
    for file in files:
        if type(file) is not dict or set(file) != {'path', 'content'} or not isinstance(file['path'], str):
            raise FusionError('上传目录需要完整的相对路径与文件内容')
        name = safe_path(file['path'])
        yield name, None if excluded(name) else _decode_upload(file['content'], MAX_FILE_BYTES)


def uploaded_snapshot(source, resources=None):
    if type(source) is not dict:
        raise FusionError('请选择完整文件夹或 ZIP 包')
    if source.get('kind') == 'directory' and set(source) == {'kind', 'files'}:
        iterator = _uploaded_files(source['files'])
    elif source.get('kind') == 'zip' and set(source) == {'kind', 'content'}:
        iterator = _zip_files(BytesIO(_decode_upload(source['content'], MAX_TOTAL_BYTES)))
    else:
        raise FusionError('仅支持文件夹或 ZIP 格式的完整材料')
    return _snapshot(iterator, resources)


def snapshot(source, resources=None):
    source = _local_source(source)
    iterator = _directory_files(source) if source.is_dir() else _zip_files(source)
    return _snapshot(iterator, resources)


def _snapshot(iterator, resources=None):
    entries, names, total = [], set(), 0
    try:
        for name, data in iterator:
            folded = name.casefold()
            if len(entries) >= MAX_FILES or any(folded == existing or folded.startswith(existing + '/') or existing.startswith(folded + '/') for existing in names):
                raise FusionError('Too many files or duplicate package paths')
            names.add(folded)
            if data is None:
                entries.append({'path': name, 'status': 'excluded'})
                continue
            total += len(data)
            if len(data) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                raise FusionError('Package byte limit exceeded')
            entries.append(_entry(name, data))
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise FusionError('Unreadable ZIP package') from exc
    # Explicit mounts only: never discover or walk a parent directory implicitly.
    if resources is None:
        resources = []
    if type(resources) is not list or len(resources) > 16:
        raise FusionError('At most 16 explicit resource mounts are supported')
    for resource in resources:
        if type(resource) is not dict or set(resource) != {'mount', 'path'}:
            raise FusionError('Each resource needs mount and path')
        mount, location = resource['mount'], resource['path']
        if not isinstance(mount, str) or not isinstance(location, str) or not location.strip():
            raise FusionError('Resource mount and path must be nonempty strings')
        safe_path(mount)
        extra = _local_source(location)
        if excluded(mount) or excluded(extra.name):
            raise FusionError('Excluded resources cannot be mounted')
        if extra.is_dir():
            iterator = ((mount + '/' + name, content) for name, content in _directory_files(extra))
        else:
            if not extra.is_file() or extra.stat().st_size > MAX_FILE_BYTES:
                raise FusionError('Unsupported or oversized resource')
            # Use the same checked reader as package files, without enumerating siblings.
            info = extra.lstat()
            with extra.open('rb') as handle:
                opened = os.fstat(handle.fileno())
                if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                    raise FusionError('Resource changed while reading')
                content = handle.read(MAX_FILE_BYTES + 1)
            after = extra.lstat()
            if _is_link(after) or (info.st_size, info.st_mtime_ns, info.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                raise FusionError('Resource changed while reading')
            iterator = [(mount, content)]
        for name, content in iterator:
            safe_path(name)
            folded = name.casefold()
            if len(entries) >= MAX_FILES or any(folded == n or folded.startswith(n + '/') or n.startswith(folded + '/') for n in names):
                raise FusionError('Resource mount collides with an existing file or exceeds file limit')
            names.add(folded)
            if content is None or excluded(name):
                entries.append({'path': name, 'status': 'excluded'})
                continue
            total += len(content)
            if len(content) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                raise FusionError('Combined package byte limit exceeded')
            entries.append(_entry(name, content))
    entries.sort(key=lambda f: f['path'])
    if not any(f['path'] == 'SKILL.md' and f['status'] == 'text' and f['text'].strip() for f in entries):
        raise FusionError('A readable, nonempty root SKILL.md is required')
    manifest = [{k: v for k, v in f.items() if k != 'text'} for f in entries]
    return {'sha256': digest(canonical(manifest).encode('utf-8')), 'files': entries,
            'references': _references(entries), 'executed': False}
