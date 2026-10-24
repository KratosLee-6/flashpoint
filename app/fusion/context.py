"""Explicit whole-file context selection; full snapshots stay local and unchanged."""
from copy import deepcopy
from pathlib import PurePosixPath
import posixpath

from .packages import FusionError, canonical

ROLES = {'entry', 'third_party', 'dependency', 'example_test', 'template', 'implementation', 'knowledge', 'other'}


def default_selection(packages):
    return {parent: [{'path': entry['path'], 'include': True, 'role': classification(entry)[0],
                      'classification_reason': classification(entry)[1],
                      'reason': '默认保留完整正文；请按任务审查 / Full text, pending review'}
                     for entry in package['files'] if entry['status'] == 'text']
            for parent, package in packages.items()}


def validate_selection(packages, selection):
    if type(selection) is not dict or set(selection) != {'A', 'B'}:
        raise FusionError('Context selection requires A/B lists')
    for parent, rows in selection.items():
        expected = {entry['path'] for entry in packages[parent]['files'] if entry['status'] == 'text'}
        if type(rows) is not list or len(rows) != len(expected):
            raise FusionError('Review every readable file; selection cannot silently omit entries')
        seen = set()
        for row in rows:
            if (type(row) is not dict or set(row) != {'path', 'include', 'reason', 'role', 'classification_reason'}
                    or not isinstance(row['path'], str) or row['path'] not in expected or row['path'] in seen
                    or type(row['include']) is not bool or not isinstance(row['reason'], str)
                    or not row['reason'].strip() or len(row['reason']) > 1000
                    or not isinstance(row['role'], str) or row['role'] not in ROLES
                    or not isinstance(row['classification_reason'], str)
                    or not row['classification_reason'].strip() or len(row['classification_reason']) > 1000):
                raise FusionError('Each file needs a unique path, boolean selection and nonempty review reason')
            if row['path'] == 'SKILL.md' and not row['include']:
                raise FusionError('The root SKILL.md must remain in context')
            if row['path'] == 'SKILL.md' and row['role'] != 'entry':
                raise FusionError('The root SKILL.md must retain its entry classification')
            seen.add(row['path'])


def selected_paths(packet, parent):
    return {row['path'] for row in packet['context_selection'][parent] if row['include']}


def selected_packages(packet):
    packages = deepcopy(packet['packages'])
    for parent, package in packages.items():
        selected = selected_paths(packet, parent)
        for entry in package['files']:
            if entry['status'] == 'text' and entry['path'] not in selected:
                entry.pop('text')
                entry['status'] = 'omitted'
    return packages


def context_gaps(packet):
    gaps = {}
    for parent, package in packet['packages'].items():
        selected = selected_paths(packet, parent)
        omitted = sum(entry['status'] == 'text' and entry['path'] not in selected for entry in package['files'])
        unparsed = sum(entry['status'] == 'unparsed' for entry in package['files'])
        excluded = sum(entry['status'] == 'excluded' for entry in package['files'])
        gaps[parent] = ([f'Context coverage {parent}: {omitted} omitted text files, {unparsed} unparsed files, {excluded} excluded entries.']
                        if omitted or unparsed or excluded else [])
        unresolved = sum(reference['status'] == 'unresolved' and reference['file'] in selected
                         for reference in package['references'])
        if unresolved:
            gaps[parent].append(f'Context references {parent}: {unresolved} unresolved literal references in selected files; inspect the reference inventory, do not assume dependency closure.')
    return gaps


def effective_gaps(packet):
    coverage = context_gaps(packet)
    return {parent: packet['known_gaps'][parent] + coverage[parent] for parent in 'AB'}


def classification(entry):
    path = PurePosixPath(entry['path'].lower())
    parts = set(path.parts)
    if path.name == 'skill.md':
        return 'entry', 'Skill 入口文件 / Skill entry'
    if (parts & {'vendor', 'vendors', 'third_party', 'third-party', 'node_modules'}
            or '.min.' in path.name
            or (path.suffix in {'.js', '.mjs', '.css'} and any(len(line) > 4000 for line in entry.get('text', '').splitlines()))):
        return 'third_party', '路径或压缩长行提示第三方依赖；须人工确认 / Dependency heuristic, not verified ownership'
    if path.name in {'package.json', 'package-lock.json', 'requirements.txt', 'pyproject.toml', 'license', 'license.md'}:
        return 'dependency', '依赖或许可声明 / Dependency or license declaration'
    if parts & {'tests', 'test', 'examples', 'samples', 'fixtures'} or path.name.startswith('test_'):
        return 'example_test', '样例或测试；不等于已执行 / Example or test, not execution evidence'
    if parts & {'templates', 'template', 'assets'} or path.suffix in {'.svg', '.png', '.jpg', '.jpeg'}:
        return 'template', '模板或素材路径 / Template or asset path'
    if path.suffix in {'.py', '.js', '.ts', '.mjs', '.cjs', '.sh', '.ps1'}:
        return 'implementation', '实现或提示词代码；仅静态读取 / Implementation or prompt code, static only'
    if parts & {'references', 'knowledge', 'docs'}:
        return 'knowledge', '知识或说明资料路径 / Knowledge or documentation path'
    return 'other', '待人工分类 / Needs manual classification'


def wire_entry(entry, included):
    result = {key: value for key, value in entry.items() if key != 'text'}
    if entry['status'] == 'text':
        result['body_sent'] = included
        if included:
            result['numbered_text'] = '\n'.join(f'{number}: {line}' for number, line in enumerate(entry['text'].splitlines(), 1))
    return result


def body_characters(entry):
    if entry['status'] != 'text':
        return 0
    return len(canonical(wire_entry(entry, True))) - len(canonical(wire_entry(entry, False))) + 1


def auto_selection(packet: dict, budget: int = 30000) -> dict:
    """快速碰撞的**自动精简选择**：SKILL.md 必留，其余按体积升序装满正文预算。

    为「简单模式」而生：完整审查流（02 层逐文件勾选）保留给专家模式；
    快速模式里每个文件的去留**同样有逐条 reason**（走 `select_context`
    正式重签，capability/collision 随之重算），审查轨迹不因自动化而消失——
    自动化的选择依然是被记录的选择。

    与 `validate_selection` 同一条硬约束：根 SKILL.md 必留、逐文件表态、
    角色取自 classification——**没有放宽任何校验**。

    ⚠️ **预算语义（2026-10-20 修正）**：`budget` 是**两包共享的总预算**，
    单位是**字符**（与 `context_limit_chars` 同尺度）。

    原实现有三个错，叠加后必然超预算（实测：两个包共 107 个文件，
    正文只入选 74K 字符，但最终 request 达 240K 字符 → 「Context budget
    exceeded」）：

    ① 预算**每包各算一次**（`used` 在包循环里重置）→ 实际是 2×budget；
    ② 只累加**正文字节**，单位与 `context_limit_chars`（字符）不一致；
    ③ 只算正文，**不算固定开销**——107 条 selection 记录（每条含 path + role +
       两条 reason）+ 每文件元数据 + answer_template + system prompt，
       实测合计约 **159K 字符**，与正文无关。估不出来，只能**量**。

    修法：先按预算选，再用 `messages()` **实量**实际发送量；超了就从
    「体积最大的已选正文文件」开始逐个剔除，直到装得下（SKILL.md 永不剔除）。
    这是「量出来再缩」，不是「估一个系数」——估的系数一定会错。
    """
    from .engine import messages, select_context

    selection = {}
    used = 0                      # **两包共享**（原实现每包重置 → 2×预算）
    for parent, package in packet['packages'].items():
        text = [e for e in package['files'] if e['status'] == 'text']
        rows = []
        for entry in sorted(text, key=lambda e: (e['path'] != 'SKILL.md', e.get('bytes') or 0)):
            role, why = classification(entry)
            is_entry = entry['path'] == 'SKILL.md'
            cost = body_characters(entry)
            fits = is_entry or used + cost <= budget
            if fits:
                used += cost
            rows.append({'path': entry['path'], 'include': fits, 'role': role,
                         'classification_reason': why,
                         'reason': ('入口文件必留' if is_entry else
                                    ('快速模式：预算内保留正文' if fits else
                                     '快速模式：超预算省略，未读取不得声称已读'))})
        selection[parent] = rows

    packet = select_context(packet, selection)

    # ── 量出来再缩：超了就剔最大的已选正文文件，直到装得下 ──
    # ⚠️ 每轮都从**同一个原始包**重新 select_context（它每次 deepcopy 并重算 sha，
    #    拿上一轮的返回值再调会撞 verify_packet 的「Prepared packet changed」）。
    base = packet
    limit = base.get('context_limit_chars') or 0
    current = dict(selection)
    for _ in range(200):                       # 上限：文件总数级
        try:
            wire = messages(current_packet := select_context(base, current),
                            enforce_budget=False)
        except Exception:
            break
        total = sum(len(m['content']) for m in wire)
        if not limit or total <= limit:
            packet = current_packet
            break
        # 找出「已选且非 SKILL.md」里正文最大的那个
        victim = None
        victim_cost = -1
        for parent, package in base['packages'].items():
            chosen = {r['path'] for r in current[parent] if r['include']}
            for entry in package['files']:
                if entry['path'] in chosen and entry['path'] != 'SKILL.md':
                    cost = body_characters(entry)
                    if cost > victim_cost:
                        victim, victim_cost = (parent, entry['path']), cost
        if victim is None:
            packet = current_packet
            break                               # 只剩 SKILL.md 还超 → 交给上层报错
        parent, path = victim
        for row in current[parent]:
            if row['path'] == path:
                row['include'] = False
                row['reason'] = '快速模式：整体 context 超限，剔除本文件（未读取不得声称已读）'
                break
    else:
        packet = select_context(base, current)
    return packet


def coverage_report(packet):
    report = {}
    for parent, package in packet['packages'].items():
        choices = {row['path']: row for row in packet['context_selection'][parent]}
        files = []
        for entry in package['files']:
            role, basis = classification(entry)
            choice = choices.get(entry['path'])
            if choice:
                role, basis = choice['role'], choice['classification_reason']
            delivery = ('selected' if choice['include'] else 'omitted') if choice else entry['status']
            body_cost = body_characters(entry)
            sent_cost = body_cost if delivery == 'selected' else 0
            files.append({**{key: value for key, value in entry.items() if key != 'text'},
                          'role': role, 'classification_basis': basis, 'delivery': delivery,
                          'reason': choice['reason'] if choice else '仅清单；正文未解析或因安全规则未读取 / Inventory only',
                          'characters': len(entry.get('text', '')), 'body_characters': body_cost,
                          'sent_body_characters': sent_cost,
                          'inventory_characters': len(canonical(wire_entry(entry, delivery == 'selected'))) - sent_cost})
        by_path = {entry['path']: entry for entry in files}
        references = []
        for reference in package['references']:
            target = posixpath.normpath(str(PurePosixPath(reference['file']).parent / reference['target'].split('#')[0]))
            references.append({**reference, 'resolved_path': target,
                               'context_status': by_path[target]['delivery'] if target in by_path else 'unresolved'})
        report[parent] = {'sha256': package['sha256'], 'files': files, 'references': references,
                          'selected_body_characters': sum(entry['sent_body_characters'] for entry in files),
                          'roles': {role: sum(entry['role'] == role for entry in files) for role in sorted(ROLES)},
                          'summary': {state: sum(entry['delivery'] == state for entry in files)
                                      for state in ('selected', 'omitted', 'unparsed', 'excluded')}}
    return report


def source_excerpt(packet, parent, path, offset=0):
    if parent not in {'A', 'B'} or not isinstance(path, str) or type(offset) is not int or offset < 0:
        raise FusionError('Invalid source lookup')
    entry = next((item for item in packet['packages'][parent]['files'] if item['path'] == path), None)
    if entry is None or entry['status'] != 'text':
        raise FusionError('Only readable files in this inventory can be reviewed')
    text = entry['text']
    if offset > len(text):
        raise FusionError('Source offset is outside the file')
    end = min(offset + 12000, len(text))
    return {'parent': parent, 'path': path, 'sha256': entry['sha256'], 'offset': offset,
            'end': end, 'total_characters': len(text), 'first_line': text[:offset].count('\n') + 1,
            'text': text[offset:end], 'next_offset': end if end < len(text) else None}
