"""SkillHub 在线包源：关键词搜索与逐文件下载（45 号文批次 3d）。

    from app.fusion.skillhub import search, download
    hits = search('防骗')                  # [{'slug','name','description',...}]
    report = download('anti-fraud')       # 落盘到本机下载区并逐文件验 sha256

## 边界（与仓库既有纪律一致）

- **出网白名单**：API 只允许固定主机 `api.skillhub.cn`（HTTPS）。文件内容由该
  服务 302 到腾讯 COS（实测 `*.cos.accelerate.myqcloud.com`）；重定向守卫只放行
  https + 这两类主机，其余一律拒绝——不让「跟随重定向」变成任意外联。
- **下载的是数据**：只落盘到下载区（默认
  `%LOCALAPPDATA%/flashpoint/skillhub-packs/<slug>/`，可用环境变量
  `SKILLHUB_PACKS_ROOT` 重定向；与融合读取的下载区同目录），不执行、不安装、
  不联网运行；搜索与下载不发送用户任何材料，也不调用模型。
- **完整性**：逐文件 sha256 对清单校验（实测一致），不符即整体拒绝；
  路径走 `packages.safe_path` 同款校验；文件数 / 单文件 / 总量三重上限；
  清单缺 `SKILL.md` 的包直接拒绝（融合流程要求目录内有 SKILL.md）。
- 换名落盘：先写临时目录再整体换入；失败时删临时目录，不留半个包。
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .packages import FusionError, excluded, safe_path

API_HOST = 'api.skillhub.cn'
_REDIRECT_SUFFIXES = ('.myqcloud.com',)
MAX_FILES = 200
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024
TIMEOUT = 15
_SLUG = re.compile(r'^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$')


def _guard_redirect(newurl: str) -> None:
    parts = urllib.parse.urlsplit(newurl)
    host = (parts.netloc or '').lower()
    if parts.scheme != 'https' or not (
            host == API_HOST or host.endswith(_REDIRECT_SUFFIXES)):
        raise FusionError('SkillHub 重定向目标不被允许：%s' % newurl[:120])


class _Opener(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _guard_redirect(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_Opener)


def _open(url: str):
    return _OPENER.open(url, timeout=TIMEOUT)


def _read_capped(response, limit: int, what: str) -> bytes:
    data = response.read(limit + 1)
    if len(data) > limit:
        raise FusionError('%s 超过 %d 字节上限，已整体拒绝' % (what, limit))
    return data


def _api_json(path: str, params: dict) -> dict:
    url = ('https://' + API_HOST + path + '?'
           + urllib.parse.urlencode(params))
    try:
        with _open(url) as response:
            payload = json.loads(_read_capped(
                response, 1024 * 1024, 'SkillHub 响应').decode('utf-8'))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise FusionError('SkillHub 搜索不可达：%s' % (getattr(exc, 'reason', None) or exc))
    except ValueError:
        raise FusionError('SkillHub 返回的不是 JSON')
    if not isinstance(payload, dict) or payload.get('code') not in (0, None):
        raise FusionError('SkillHub 返回异常：%s' % json.dumps(payload, ensure_ascii=False)[:160])
    return payload.get('data') if isinstance(payload.get('data'), dict) else {}


def search(query: str, page: int = 1, page_size: int = 20) -> dict:
    """关键词搜索。空关键词 = 默认榜单。返回 {skills, total}（已归一化）。"""
    if not isinstance(query, str) or len(query) > 100:
        raise FusionError('搜索关键词须为不超过 100 字的文本')
    if not isinstance(page, int) or not 1 <= page <= 10:
        raise FusionError('页码须在 1–10 之间')
    if not isinstance(page_size, int) or not 1 <= page_size <= 50:
        raise FusionError('每页数量须在 1–50 之间')
    params = {'sortBy': 'score', 'page': page, 'pageSize': page_size}
    if query.strip():
        params['keyword'] = query.strip()
    data = _api_json('/api/skills', params)
    skills = []
    for row in data.get('skills') or []:
        if not isinstance(row, dict) or not isinstance(row.get('slug'), str):
            continue
        skills.append({'slug': row['slug'],
                       'name': str(row.get('name') or row['slug'])[:120],
                       'description': str(row.get('description') or '')[:300],
                       'version': str(row.get('version') or '')[:40],
                       'downloads': row.get('downloads') if isinstance(row.get('downloads'), int) else None,
                       'category': str(row.get('category') or '')[:60],
                       'verified': row.get('verified') is True})
    return {'skills': skills, 'total': data.get('total') if isinstance(data.get('total'), int) else len(skills)}


def _slug_ok(slug) -> str:
    if not isinstance(slug, str) or not _SLUG.fullmatch(slug):
        raise FusionError('SkillHub 包标识（slug）格式不对：%r' % (slug if isinstance(slug, str) else slug))
    return slug


def pack_dir(slug: str, root: Path | None = None) -> Path:
    if root is None:
        import os
        root = (os.environ.get('SKILLHUB_PACKS_ROOT')
                or Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'flashpoint' / 'skillhub-packs')
    return Path(root) / _slug_ok(slug)


def download(slug: str, root: Path | None = None) -> dict:
    """按 slug 下载整包到下载区。返回 {slug, path, files, bytes, sha256_skipped}。"""
    slug = _slug_ok(slug)
    target = pack_dir(slug, root)
    with _open('https://' + API_HOST + '/api/v1/skills/'
               + urllib.parse.quote(slug) + '/files') as response:
        manifest = json.loads(_read_capped(
            response, 1024 * 1024, 'SkillHub 文件清单').decode('utf-8'))
    files = manifest.get('files') if isinstance(manifest, dict) else None
    if not isinstance(files, list) or not files:
        raise FusionError('SkillHub 文件清单为空或格式不对')
    if len(files) > MAX_FILES:
        raise FusionError('清单有 %d 个文件，超过 %d 上限' % (len(files), MAX_FILES))
    rows = []
    for row in files:
        if not isinstance(row, dict) or not isinstance(row.get('path'), str):
            raise FusionError('文件清单里有格式不对的条目')
        path = safe_path(row['path'])
        sha = row.get('sha256')
        if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', sha):
            raise FusionError('文件 %s 缺少可校验的 sha256' % path[:80])
        size = row.get('size')
        if isinstance(size, int) and (size < 0 or size > MAX_FILE_BYTES):
            raise FusionError('文件 %s 声明大小 %s 超出上限' % (path[:80], size))
        if excluded(path):
            raise FusionError('清单包含敏感/排除类文件 %s，整包拒绝' % path[:80])
        rows.append((path, sha.lower(), size))
    if not any(path == 'SKILL.md' for path, _, _ in rows):
        raise FusionError('清单缺少 SKILL.md——没有它融合流程无法读取该包')

    total = 0
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix='.%s.downloading-' % slug, dir=str(target.parent)))
    try:
        for path, sha, size in rows:
            data = _fetch_file(slug, path)
            if len(data) > MAX_FILE_BYTES:
                raise FusionError('文件 %s 超过单文件上限' % path[:80])
            got = hashlib.sha256(data).hexdigest()
            if got != sha:
                raise FusionError('文件 %s 的 sha256 与清单不符，整包拒绝' % path[:80])
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                raise FusionError('包总量超过 %d 字节上限，已中止' % MAX_TOTAL_BYTES)
            dest = tmp / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp.rename(target)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return {'slug': slug, 'path': str(target), 'files': len(rows), 'bytes': total,
            'source': 'skillhub'}


def _fetch_file(slug: str, path: str) -> bytes:
    url = ('https://' + API_HOST + '/api/v1/skills/'
           + urllib.parse.quote(slug) + '/file?' + urllib.parse.urlencode({'path': path}))
    with _open(url) as response:
        return _read_capped(response, MAX_FILE_BYTES, '文件 %s' % path[:80])
