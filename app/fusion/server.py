"""Loopback App host. Explicit source mounts; no package execution or arbitrary file serving."""
from __future__ import annotations
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import secrets
import re
import threading
import webbrowser
from urllib.parse import urlsplit

from .engine import (prepare, preview, messages, synthesize, synthesize_fill, parse_json, check_endpoint,
                     inspect_packages, inspect_documents, select_context, probe_model, _exc_label)
from .materialize import materialize_candidate, materialize_verdict
from .engine import segment_probe
from .skillhub import download as skillhub_download
from .skillhub import search as skillhub_search
from .context import auto_selection, source_excerpt
from .deliverables import Deliverables, Download
from .fill import build_messages
from .reviews import EvidenceReviews
from .packages import FusionError

# 前端静态资源根：默认 = 仓库 app/ 目录（源码运行）。
# PyInstaller sidecar 场景（__file__ 指向冻结临时目录）可用 FLASHPOINT_APP_ASSETS
# 指向随包分发的资源目录——Tauri 壳启动 sidecar 时注入该变量。
import os as _os
APP = Path(_os.environ.get('FLASHPOINT_APP_ASSETS') or Path(__file__).resolve().parents[1])
ASSETS = {'index.html', 'styles.css', 'seed.js', 'skills.js', 'core.js', 'storage.js',
          'runtime.js', 'spark-fx.js', 'app.js', 'fusion-ui.js', 'sw.js', 'manifest.webmanifest',
          'icon.svg', 'icon-192.png', 'icon-512.png', 'icon.ico'}
MAX_BODY = 128 * 1024


def source_packs():
    """包源清单（GUI A/B 路径下拉的数据源）：
    curated = 仓库内 skillhub-skill/ 与 app/examples/；
    skillhub = 本机 SkillHub 下载区（%LOCALAPPDATA%\\flashpoint\\skillhub-packs/，
    第三方上架包不入库）。只读 SKILL.md 头部 frontmatter 的 name/description，
    不读取正文、不执行任何内容。
    """
    import os as _os2
    roots = [
        ('curated', APP.parent / 'skillhub-skill'),
        ('curated', APP.parent / 'app' / 'examples'),
        ('skillhub', Path(_os2.environ.get('LOCALAPPDATA', str(Path.home())))
         / 'flashpoint' / 'skillhub-packs'),
    ]
    packs = []
    seen = set()
    for source, root in roots:
        if not root.is_dir():
            continue
        for entry in sorted(root.iterdir()):
            skill = entry / 'SKILL.md'
            if not entry.is_dir() or not skill.is_file() or entry.name in seen:
                continue
            seen.add(entry.name)
            name, description = entry.name, ''
            try:
                for line in skill.read_text(encoding='utf-8', errors='replace').splitlines()[:30]:
                    low = line.lower()
                    if low.startswith('name:') and name == entry.name:
                        name = line.split(':', 1)[1].strip() or name
                    elif low.startswith('description:'):
                        description = line.split(':', 1)[1].strip()
                    if name != entry.name and description:
                        break
            except OSError:
                pass
            packs.append({'name': name, 'description': description[:200],
                          'source': source, 'path': str(entry)})
    return packs
# 桌面壳（Tauri）页面的来源。WebView2 两种加载方式分别报这两个 Origin。
# 这两个来源是壳自身界面而非第三方站点，因此按第一方处理（仍需 Host 校验与令牌）。
SHELL_ORIGINS = ('tauri://localhost', 'http://tauri.localhost')


class Workspace:
    def __init__(self, deliverables_root=None, review_experiments=None):
        self.deliverables = Deliverables(deliverables_root)
        self.reviews = EvidenceReviews(self.deliverables, review_experiments)
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self.packet = None
        self.inventory = None
        self.job = None
        self.running = False
        self.request_identity = None
        self.last_result = None

    def call(self, route, body):
        if type(body) is not dict:
            raise FusionError('Expected a JSON object')
        with self.lock:
            if route == 'sources':
                # 包源清单：本地 curated 目录 + SkillHub 下载区（仓库外）。
                # 只列名称与描述，供 A/B 路径输入的下拉选择；不读取正文。
                return {'packs': source_packs()}
            if route == 'skillhub/search':
                # 45 号文批次 3d：在线搜索（只访问 api.skillhub.cn，同步一次
                # GET；不出站用户材料，不调用模型）。
                if self.running:
                    raise FusionError('已有模型请求进行中，请等待结束后再搜索包源')
                return skillhub_search(body.get('query', ''), body.get('page', 1),
                                       body.get('page_size', 20))
            if route == 'skillhub/download':
                # 在线下载整包到本机下载区（%LOCALAPPDATA%/flashpoint/skillhub-packs）。
                # 逐文件 sha256 校验；下载在后台线程跑（大包可能要几十秒），
                # 走与物化相同的 job 轮询。
                slug = body.get('slug')
                if not isinstance(slug, str) or not slug.strip():
                    raise FusionError('请指明要下载的 SkillHub 包（slug）')
                if self.running:
                    raise FusionError('已有模型请求进行中，不能同时下载包源')
                self.request_identity = ('skillhub-download', slug.strip())
                self.job = {'id': secrets.token_hex(12), 'status': 'running',
                            'result': None, 'error': None, 'kind': 'skillhub-download'}
                self.running = True
                threading.Thread(target=self._skillhub_download,
                                 args=(slug.strip(), self.job), daemon=True).start()
                return {'id': self.job['id'], 'status': 'running'}
            if route == 'reviews':
                if body:
                    raise FusionError('证据列表不接受路径参数')
                return self.reviews.list()
            if route == 'reviews/task':
                if set(body) != {'id', 'task', 'evidence_sha256'}:
                    raise FusionError('请从已核对的证据列表选择任务')
                return self.reviews.task(body['id'], body['task'], body['evidence_sha256'])
            if route == 'reviews/note':
                return self.reviews.note(body)
            if route == 'deliverables':
                if body:
                    raise FusionError('下载列表不接受路径或筛选参数')
                return self.deliverables.list()
            if route in {'deliverables/download', 'deliverables/evidence'}:
                if set(body) != {'id'}:
                    raise FusionError('下载请求仅接受交付物 id')
                return self.deliverables.download(body['id'], evidence=route.endswith('/evidence'))
            if route == 'documents':
                if self.running:
                    raise FusionError('A model request is still finishing; wait before replacing materials')
                packet = inspect_documents(body.get('documents'), body.get('goal'), body.get('max_context_chars', 160000))
                report = preview(packet, enforce_budget=False)
                self.inventory, self.packet, self.job = packet, None, None
                return {**report, 'inventory_sha256': packet['sha256']}
            if route in {'prepare', 'inventory', 'packages'}:
                if self.running:
                    raise FusionError('A model request is still finishing; wait before replacing materials')
                a, b = body.get('a'), body.get('b')
                uploads = body.get('uploads') if route == 'packages' else {}
                if not isinstance(uploads, dict) or set(uploads) - {'A', 'B'} or (route == 'packages' and not uploads):
                    raise FusionError('请选择 A/B 文件夹或 ZIP 包，未选择的一方可使用本机路径')
                if not all(parent in uploads or (isinstance(location, str) and location.strip()) for parent, location in [('A', a), ('B', b)]):
                    raise FusionError('请填写 A/B 的本机 Skill 目录或 ZIP 路径')
                reader = prepare if route == 'prepare' else inspect_packages
                options = {'uploads': uploads} if route == 'packages' else {}
                packet = reader(a, b, body.get('goal'), body.get('max_context_chars', 160000), known_gaps=body.get('known_gaps'), resources=body.get('resources'), **options)
                # **快速碰撞**（2026-10-11）：auto_select 让服务端按「SKILL.md 必留 +
                # 体积升序装满预算」自动生成精简选择并正式重签——每个文件的去留仍
                # 逐条带 reason，02 层审查在专家模式保留。
                if route in {'prepare', 'inventory'} and body.get('auto_select'):
                    budget = body.get('lean_budget')
                    packet = auto_selection(packet, budget=budget if isinstance(budget, int) and 1000 <= budget <= 500000 else 30000)
                if route in {'inventory', 'packages'}:
                    report = preview(packet, enforce_budget=False)
                    # **快速碰撞**：auto_select 时选择已定稿，packet 留给 run 直接使用
                    #（专家流程仍置 None，须经 context 层逐文件审查后定稿）。
                    self.inventory = packet
                    self.packet = packet if (route == 'inventory' and body.get('auto_select')) else None
                    self.job = None
                    return {**report, 'inventory_sha256': packet['sha256']}
                self.packet, self.job, self.inventory = packet, None, None
                return preview(packet)
            if route in {'context', 'source'}:
                if not self.inventory or body.get('inventory_sha256') != self.inventory['sha256']:
                    raise FusionError('材料清单已变化，请重新读取并审查')
                if route == 'source':
                    return source_excerpt(self.inventory, body.get('parent'), body.get('path'), body.get('offset', 0))
                if self.running:
                    raise FusionError('模型请求结束前不能替换上下文')
                self.packet, self.job = None, None
                packet = select_context(self.inventory, body.get('selection'))
                report = preview(packet, enforce_budget=False)
                self.packet, self.job = (packet if report['budget_ok'] else None), None
                return {**report, 'inventory_sha256': self.inventory['sha256']}
            if route == 'preflight':
                if self.running:
                    raise FusionError('已有模型请求，不能同时发起连接检查')
                return probe_model(body.get('endpoint'), body.get('model'), body.get('api_key', ''),
                                   body.get('allow_billable_probe'), body.get('thinking'))
            if route == 'preview':
                self._packet(body)
                return {'packet_sha256': self.packet['sha256'], 'messages': messages(self.packet)}
            if route == 'run':
                self._packet(body)
                endpoint, model, key = body.get('endpoint'), body.get('model'), body.get('api_key', '')
                thinking = body.get('thinking')
                if not isinstance(endpoint, str) or not isinstance(model, str) or not model.strip():
                    raise FusionError('请填写端点与模型名')
                check_endpoint(endpoint)
                if not isinstance(key, str) or len(key) > 8192 or '\n' in key or '\r' in key:
                    raise FusionError('Invalid API key')
                request_id = body.get('request_id')
                if request_id is not None and (not isinstance(request_id, str) or not re.fullmatch(r'[0-9a-f]{32}', request_id)):
                    raise FusionError('Invalid client request ID')
                identity = (self.packet['sha256'], endpoint, model)
                if request_id and self.job and self.job['id'] == request_id:
                    if self.request_identity != identity:
                        raise FusionError('Request ID belongs to different model input')
                    return {'id': self.job['id'], 'status': self.job['status']}
                if self.running:
                    raise FusionError('已有模型请求，不能重复发送')
                self.request_identity = identity
                self.job = {'id': request_id or secrets.token_hex(12), 'status': 'running', 'result': None, 'error': None}
                self.running = True
                threading.Thread(target=self._run, args=(self.packet, self.job, endpoint, model, key, thinking), daemon=True).start()
                return {'id': self.job['id'], 'status': 'running'}
            if route == 'fill':
                # 第 5 类的补足：带缺口清单再问模型一次，补完由同一个核对器复评。
                self._packet(body)
                endpoint, model, key = body.get('endpoint'), body.get('model'), body.get('api_key', '')
                thinking = body.get('thinking')
                if not isinstance(endpoint, str) or not isinstance(model, str) or not model.strip():
                    raise FusionError('请填写端点与模型名')
                check_endpoint(endpoint)
                if not isinstance(key, str) or len(key) > 8192 or '\n' in key or '\r' in key:
                    raise FusionError('Invalid API key')
                result = self.last_result
                if not isinstance(result, dict) or not isinstance(result.get('result'), dict):
                    raise FusionError('还没有可供补足的方案结果，请先发送一次融合请求')
                title = body.get('title')
                if not isinstance(title, str) or not title.strip():
                    raise FusionError('请指明要补足哪一个方案')
                target = next((c for c in result['result']['candidates'] if c['title'] == title), None)
                if target is None:
                    raise FusionError('该方案不在本次结果中')
                build_messages(self.packet, target, target.get('gaps_to_fill') or [])  # 构造即校验，提前挡住空缺口
                if self.running:
                    raise FusionError('已有模型请求，不能重复发送')
                request_id = body.get('request_id')
                if request_id is not None and (not isinstance(request_id, str) or not re.fullmatch(r'[0-9a-f]{32}', request_id)):
                    raise FusionError('Invalid client request ID')
                self.request_identity = (self.packet['sha256'], endpoint, model, title)
                self.job = {'id': request_id or secrets.token_hex(12), 'status': 'running', 'result': None, 'error': None}
                self.running = True
                threading.Thread(target=self._fill, args=(self.packet, target, self.job, endpoint, model, key, thinking), daemon=True).start()
                return {'id': self.job['id'], 'status': 'running'}
            if route == 'materialize':
                # 「物化并自测」：把已通过验收的方案变成可运行包体并跑 selftest。
                # 机主裁定（2026-10-07）：「能跑得动就说明是正常的」——
                # 本路由回答「跑不跑得动」，不声称「比父本好」。
                self._packet(body)
                endpoint, model, key = body.get('endpoint'), body.get('model'), body.get('api_key', '')
                thinking = body.get('thinking')
                if not isinstance(endpoint, str) or not isinstance(model, str) or not model.strip():
                    raise FusionError('请填写端点与模型名')
                check_endpoint(endpoint)
                if not isinstance(key, str) or len(key) > 8192 or '\n' in key or '\r' in key:
                    raise FusionError('Invalid API key')
                result = self.last_result
                if not isinstance(result, dict) or not isinstance(result.get('result'), dict):
                    raise FusionError('还没有可供物化的方案结果，请先发送一次融合请求')
                orientation = body.get('orientation')
                if orientation not in {'A', 'B', 'C'}:
                    raise FusionError('请指明要物化的候选朝向（A/B/C）')
                if not next((c for c in result['result']['candidates']
                             if c['orientation'] == orientation), None):
                    raise FusionError('该候选不在本次结果中')
                if self.running:
                    raise FusionError('已有模型请求，不能重复发送')
                self.request_identity = (self.packet['sha256'], endpoint, model, 'materialize', orientation)
                self.job = {'id': secrets.token_hex(12), 'status': 'running',
                            'result': None, 'error': None, 'kind': 'materialize'}
                self.running = True
                threading.Thread(target=self._materialize,
                                 args=(result, orientation, self.job, endpoint, model, key,
                                       thinking),
                                 daemon=True).start()
                return {'id': self.job['id'], 'status': 'running'}
            if route in {'job', 'cancel'}:
                if not self.job or body.get('id') != self.job['id']:
                    raise FusionError('该任务已失效，请重新准备材料')
                if route == 'cancel' and self.job['status'] == 'running':
                    self.job['status'] = 'cancelled'
                return {**self.job, 'transport_pending': self.running}
            raise FusionError('Unknown operation')

    def _packet(self, body):
        if not self.packet or body.get('packet_sha256') != self.packet['sha256']:
            raise FusionError('材料快照已变化，请重新准备并检查请求')

    def _fill(self, packet, target, job, endpoint, model, key, thinking=None):
        try:
            report = synthesize_fill(packet, target, endpoint=endpoint, model=model, api_key=key, timeout=180, thinking=thinking)
            with self.lock:
                if job['status'] == 'running':
                    job.update(status='completed', result=report)
        except Exception as exc:
            with self.lock:
                if job['status'] == 'running':
                    job.update(status='failed', error=str(exc) if isinstance(exc, FusionError) else 'Fill request failed; nothing was changed')
        finally:
            with self.lock:
                self.running = False

    def _materialize(self, result, orientation, job, endpoint, model, key, thinking=None):
        # 「物化并自测」worker：方案 → 可运行包体 → selftest。机主裁定
        # （2026-10-07）：「能跑得动就说明是正常的」——只回答「跑不跑得动」。
        try:
            workroot = Path(self.deliverables.root).parent / 'materialize'
            # 物化也要用可配超时（2026-10-21 实测）：写死 300 秒时，MiniMax-M3.1-Flash
            # 在物化阶段直接超时失败，产物一个字都没落盘。
            try:
                mat_timeout = max(30, int(os.environ.get('FUSION_TIMEOUT', '600')))
            except ValueError:
                mat_timeout = 600
            report = materialize_candidate(result, orientation, workroot / job['id'],
                                           endpoint=endpoint, model=model, api_key=key,
                                           timeout=mat_timeout, thinking=thinking)
            with self.lock:
                if job['status'] == 'running':
                    # 交付不全 ≠ 完成（口径见 materialize.materialize_verdict，
                    # 单测直接调那个函数——服务端与测试只有一份实现）。
                    status, error = materialize_verdict(report)
                    if status == 'failed':
                        job.update(status='failed', result=report, error=error)
                    else:
                        job.update(status='completed', result=report)
        except Exception as exc:
            with self.lock:
                if job['status'] == 'running':
                    job.update(status='failed', error=str(exc) if isinstance(exc, FusionError) else '物化请求失败，未产生包体')
        finally:
            with self.lock:
                self.running = False

    def _skillhub_download(self, slug, job):
        # SkillHub 下载 worker：整包落盘（先临时目录再换名，失败不留半个包）。
        # 下载是数据操作，不调用模型；异常文案区分明确拒绝与网络失败。
        try:
            report = skillhub_download(slug)
            with self.lock:
                if job['status'] == 'running':
                    job.update(status='completed', result=report)
        except Exception as exc:
            with self.lock:
                if job['status'] == 'running':
                    job.update(status='failed',
                               error=str(exc) if isinstance(exc, FusionError) else 'SkillHub 下载失败，未落盘任何内容')
        finally:
            with self.lock:
                self.running = False

    def _run(self, packet, job, endpoint, model, key, thinking=None):
        try:
            # 2026-10-01 智谱 glm-4.7 实测：完整方案（最小合规结果约 4,177 token）在 90 秒内
            # 跑不完，报的是「连接/超时/编码失败」——把「模型慢」说成了「连不上」。
            # 界面本来就靠任务轮询显示进度，长一点不会变成无反馈等待。
            #
            # **分段模式转正（2026-10-07，机主批准）**：n=16 两臂 A/B（glm-4.5-air）
            # 整份 0/16 vs 分段 14/16（`evaluations/ab-blocksplit.txt`）。
            #
            # **自适应（2026-10-12，机主裁定「分段按自适应 + 前置探测」）**：M3 导航批
            # 反向——整份 1/4、分段 0/4（引文保真度不过段级重锚定）。分段收益是
            # **模型条件性的**，所以默认改为 `auto`：先用一次调用探测第一段
            # （profiles:A）能否过确定性校验，过→分段，不过→整份并如实留探测读数；
            # `FUSION_SPLIT=on/off` 仍可强制。
            # max_tokens 给 32768（2026-10-13/14 glm-5.3 系实测）：默认 8192 对「始终思考」
            # 模型必然截断——思考链先烧掉大半预算，正文 JSON 写到一半断掉；
            # glm-5.3-flash 全链实测 16384 仍不够、32768 通过。上限不是预扣，
            # 非思考模型无代价。
            split_mode = os.environ.get('FUSION_SPLIT', 'auto')
            # 请求超时。2026-10-21 实测：MiniMax-M3.1-Flash 在 165K 字符上下文上
            # 要跑 367 秒以上（它是「自适应思考」模型，不能关思考，思考链吃满预算），
            # 原来的 300 秒固定值必然超时 → 被 catch 成一句通用英文错误，真因全丢。
            # 改成可用 FUSION_TIMEOUT 覆盖，默认 600 秒。
            try:
                run_timeout = max(30, int(os.environ.get('FUSION_TIMEOUT', '600')))
            except ValueError:
                run_timeout = 600
            probe = None
            if split_mode == 'auto':
                try:
                    probe = segment_probe(packet, endpoint=endpoint, model=model, api_key=key,
                                          timeout=min(run_timeout, 300), thinking=thinking,
                                          max_tokens=32768)
                except Exception as exc:                  # noqa: BLE001
                    probe = {'ok': False, 'segment': None,
                             'issues': ['探测请求失败：%s' % str(exc)[:160]]}
                split = bool(probe.get('ok'))
                with self.lock:
                    if job['status'] == 'running':
                        job['split_probe'] = probe
            else:
                split = split_mode != 'off'
            result = synthesize(packet, endpoint=endpoint, model=model, api_key=key,
                                timeout=run_timeout, thinking=thinking, split_segments=split,
                                segment_retries=3, max_tokens=16384)
            if probe is not None:
                result['split_probe'] = probe
            with self.lock:
                if job['status'] == 'running':
                    job.update(status='completed', result=result)
                    self.last_result = result
        except Exception as exc:
            with self.lock:
                if job['status'] == 'running':
                    # 真因必须可见（2026-10-21 实测）：MiniMax-M3.1-Flash 在 165K 字符上下文上
                    # 跑了 367 秒才结束，超过 synthesize(timeout=300) → 抛原始异常 →
                    # 被这句通用英文文案整个盖住，用户和机主都只看到「本次请求失败」。
                    # 保留异常类型 + 原文（截断），FusionError 直接给原文。
                    if isinstance(exc, FusionError):
                        detail = str(exc)
                    else:
                        detail = '%s（%s）: %s' % (type(exc).__name__,
                                                  _exc_label(exc), str(exc)[:400])
                    job.update(status='failed', error=detail)
        finally:
            key = ''
            with self.lock:
                self.running = False


def _scrub_paths(text: str, body) -> str:
    """把**用户传入的路径**从错误消息里脱敏掉（换成 `<路径>`）。

    2026-10-21：HTTP 层开始回显异常原文后，Python 原生异常消息里的路径
    （`FileNotFoundError: [WinError 2] … 'private-missing-path'`）
    就被原样回给了调用方——用户输入的路径不该从错误里漏回去。
    这里按**本请求体里出现过的路径值**精确替换，不做正则猜路径形态
    （免得把正常文案里的东西也改掉）。
    """
    if not isinstance(body, dict) or not text:
        return text
    values = []
    for key in ('a', 'b'):
        v = body.get(key)
        if isinstance(v, str) and len(v) >= 3:
            values.append(v)
    uploads = body.get('uploads')
    if isinstance(uploads, dict):
        for pack in uploads.values():
            if isinstance(pack, dict):
                for v in pack.values():
                    if isinstance(v, str) and len(v) >= 3:
                        values.append(v)
    for v in sorted(set(values), key=len, reverse=True):
        text = text.replace(v, '<路径>')
    return text


def make_server(port=4178, workspace=None):
    workspace = workspace or Workspace()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Neither source paths nor credentials are request logs.

        def reply(self, status, value, content_type='application/json; charset=utf-8'):
            download = value if isinstance(value, Download) else None
            if download:
                value, content_type = download.data, download.content_type
            data = json.dumps(value, ensure_ascii=False).encode() if not isinstance(value, bytes) else value
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('X-Frame-Options', 'DENY')
            origin = self.cors_origin()
            if origin:
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Vary', 'Origin')
            if download:
                self.send_header('Content-Disposition', f'attachment; filename="{download.filename}"')
                self.send_header('X-Spark-SHA256', download.sha256)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def cors_origin(self):
            """需要回写 CORS 头的来源。

            只有桌面壳那两个来源需要：壳内页面与本机服务不同源（tauri://localhost
            → http://127.0.0.1:port），浏览器会先发预检。启动脚本的浏览器走同源，
            不应带这些头（见 test_deliverables 对下载响应的断言）。
            """
            origin = self.headers.get('Origin')
            return origin if origin in SHELL_ORIGINS else ''

        def allowed(self, mutation=False):
            host = f'127.0.0.1:{self.server.server_port}'
            origin = self.headers.get('Origin')
            # 桌面壳页面取本机服务必然被浏览器判为 cross-site（协议+主机都不同），
            # 它不是「陌生站点」，而是壳自身界面；这两种壳来源按第一方放行，仍需 Host
            # 与令牌。陌生来源、以及带 cross-site 标记的第三方来源照旧拒绝。
            shell = origin in SHELL_ORIGINS
            if self.headers.get('Host') != host:
                return False
            if not shell and self.headers.get('Sec-Fetch-Site') == 'cross-site':
                return False
            # 非浏览器 fetch（curl 等）可能完全没有 Origin 头。本服务绑定 127.0.0.1，
            # 只有本机进程可访问——Origin 检查只防御跨站攻击，对本机调用不构成威胁。
            if origin is not None and not shell and origin != 'http://' + host:
                return False
            return not mutation or secrets.compare_digest(self.headers.get('X-Spark-Token', ''), workspace.token)

        def do_OPTIONS(self):
            # 壳内 fetch 带 X-Spark-Token 与 JSON 体属非简单请求，浏览器先发预检；
            # 没有这一步，所有 POST 都会在真正发送前被浏览器拦掉。
            origin = self.cors_origin()
            if not origin or not self.allowed():
                return self.reply(403, {'error': 'Local origin required'})
            self.send_response(204)
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Spark-Token')
            self.send_header('Access-Control-Max-Age', '600')
            self.send_header('Vary', 'Origin')
            self.send_header('Content-Length', '0')
            self.end_headers()

        def do_GET(self):
            if not self.allowed():
                return self.reply(403, {'error': 'Local origin required'})
            path = urlsplit(self.path).path
            if path == '/api/session':
                return self.reply(200, {'token': workspace.token, 'service': 'spark-fusion', 'protocol': 1,
                                        'capabilities': {'exploration_without_goal': True, 'package_uploads': True, 'evidence_reviews': True}})
            name = path.removeprefix('/app/').removeprefix('/')
            if not name or path == '/app/':
                name = 'index.html'
            if name not in ASSETS:
                return self.reply(404, {'error': 'Not found'})
            mime = {'.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html; charset=utf-8'}.get(Path(name).suffix) or mimetypes.guess_type(name)[0] or 'application/octet-stream'
            return self.reply(200, (APP / name).read_bytes(), mime)

        def do_POST(self):
            if not self.allowed(True):
                return self.reply(403, {'error': 'Local session required'})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                path = urlsplit(self.path).path
                body_limit = {'/api/documents': 4 * 1024 * 1024, '/api/packages': 48 * 1024 * 1024}.get(path, MAX_BODY)
                if not 0 < size <= body_limit or self.headers.get('Content-Type') != 'application/json':
                    return self.reply(413, {'error': 'Expected bounded JSON request'})
                self.connection.settimeout(10)
                body = parse_json(self.rfile.read(size).decode('utf-8'))
                # 读完请求体后**放宽超时**：上面的 10s 是给「读 body」设的，
                # 但同一个 socket 超时会连带覆盖后续处理——而 inventory/prepare
                # 要读两个母包（实测 6.2s）+ auto_select 再几秒，必然超 10s，
                # 结果是 socket timeout → OSError → 被吞成「无法读取材料或请求格式错误」
                # （真因不可见）。处理阶段不该受读体超时约束。
                self.connection.settimeout(300)
                path = urlsplit(self.path).path
                if not path.startswith('/api/'):
                    return self.reply(404, {'error': 'Not found'})
                return self.reply(200, workspace.call(path[5:], body))
            except (FusionError, OSError, ValueError, TypeError) as exc:
                # **不要把真因吞成一句通用错**（2026-10-20 实测踩到：socket timeout
                # 被吞成「无法读取材料或请求格式错误」，真因完全不可见，排查绕了大弯）。
                # FusionError 是产品级拒绝，原样给；其余是意外，把**类型 + 原文**带上。
                if isinstance(exc, FusionError):
                    detail = str(exc)
                else:
                    detail = '无法读取材料或请求格式错误，请检查路径和输入（%s: %s）' % (
                        type(exc).__name__, _scrub_paths(str(exc)[:200], body))
                return self.reply(400, {'error': detail})

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def main():
    parser = argparse.ArgumentParser(description='AI--boom local App + fusion service')
    parser.add_argument('--port', type=int, default=4178)
    parser.add_argument('--open', action='store_true', help='Open the App after binding the local port')
    args = parser.parse_args()
    with make_server(args.port) as server:
        url = f'http://127.0.0.1:{server.server_port}/app/'
        print(f'AI--boom: {url}', flush=True)
        if args.open:
            threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
