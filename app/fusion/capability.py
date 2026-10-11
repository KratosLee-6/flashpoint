"""能力结构抽取：与域无关的「话语结构指纹」，让跨域的一对可比。

为什么要这个模块（39 号文 40 号裁决）：
`directions.py` 在**文本里找共同词**，因此跨域的一对（法律 × 汽车维修）永远命中不了
任何规则——`evaluations/cross_domain_probe.py` 用真实函数跑过：产出为空。
本模块换掉原语：抽取的是**话语结构**——步骤、条件、约束、校验、交付物、边界。
这些结构在任何域里都存在，法律包和汽修包可以没有任何共同词汇，但都有步骤和约束，
于是它们在一个与域无关的空间里可比。

硬约束：
- **不调模型、不联网、不加载包内代码**；与 `directions.py` 同一条纪律。
- **不是能力理解，也不是成功概率**：只说明「这个包在结构上做了什么形状的事」。
- 每个条目都带出处（path/行号/sha256），可逐条回查。
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath

from .packages import canonical, digest

POLICY = {
    'version': 'capability-structure-v1',
    'extraction': {
        'max_items_per_axis': 6,
        'max_line_chars': 1500,
        'min_content_chars': 4,
    },
    'axes': {
        'steps': {
            'label': '步骤 / steps',
            'patterns': [r'^\s*(?:step\s*)?\d+[.)、]\s*\S', r'^\s*第[一二三四五六七八九十]+步',
                         r'^\s*[-*+]\s*(?:先|再|然后|最后|接着)'],
            'note': '有序动作；顺序本身就是能力的一部分',
        },
        'conditions': {
            'label': '条件分支 / conditions',
            'patterns': [r'(?:如果|若|当|一旦|除非|假如)', r'\b(?:if|when|unless|otherwise)\b'],
            'note': '条件分支决定同一套动作在不同前提下的走法',
        },
        'constraints': {
            'label': '约束 / constraints',
            'patterns': [r'(?:必须|不得|禁止|务必|仅限|只允许|不应|切勿)', r'\b(?:must|must not|never|only|shall)\b'],
            'note': '硬性边界；缺了它，方案在别人手里就会走样',
        },
        'checks': {
            'label': '校验 / checks',
            'patterns': [r'(?:核对|检查|验证|校验|复查|复核|回检)', r'\b(?:verify|validate|check|review|re-?check)\b'],
            'note': '把「我以为对」变成「有依据的对」',
        },
        'deliverables': {
            'label': '交付物 / deliverables',
            'patterns': [r'(?:输出|交付|产出|生成|出具|提供).{0,12}?(?:报告|清单|表格|文档|方案|卡片|结果|建议|记录)',
                         r'\b(?:output|deliverable|report|checklist|manifest)\b'],
            'note': '交出去的东西决定了这项能力能不能被复用',
        },
        'evidence_refs': {
            'label': '依据 / evidence references',
            'patterns': [r'(?:依据|来源|标准|规范|条款|参照|according to|per (?:the )?\w+ standard)',
                         r'\b(?:reference|citation|spec(?:ification)?)\b'],
            'note': '有没有权威依据，决定结论能不能被追责',
        },
        'boundaries': {
            'label': '边界 / boundaries',
            'patterns': [r'(?:不支持|未实现|不适用|不包含|不负责|暂未)', r'\b(?:not (?:implemented|supported|available)|out of scope)\b'],
            'note': '知道自己不做什么，比多会一点更可信',
        },
        'audience': {
            'label': '受众 / audience',
            'patterns': [r'(?:面向|适用于|适用对象|受众|读者)', r'\b(?:for (?:beginners|experts|learners|users))\b'],
            'note': '同一套动作换一批人就要改表达方式',
        },
    },
    'shapes': {
        'ordered_verified': {'axes': ['steps', 'checks'], 'why': '有步骤且有校验——可复现、可追责'},
        'conditional': {'axes': ['conditions'], 'why': '有条件分支——同一套动作会因前提而变'},
        'governed': {'axes': ['constraints'], 'why': '有硬约束——边界清楚，交给别人也不容易走样'},
        'deliverable_making': {'axes': ['deliverables'], 'why': '有明确交付物——能力可被下游复用'},
        'reference_bound': {'axes': ['evidence_refs'], 'why': '有权威依据——结论可被追溯'},
    },
    'bridge': {
        'version': 'capability-bridge-v1',
        'min_shared_axes': 2,
        'max_surfaces': 4,
    },
}

_SKIP_DIRS = {'test', 'tests', 'fixtures', '__tests__', 'node_modules', '.git'}
_NOUN_AFTER = r'([一-鿿\w\-]{2,12})'


def policy_digest() -> str:
    return digest(canonical(POLICY).encode('utf-8'))


def _is_test_path(path: str) -> bool:
    p = PurePosixPath(path)
    name = p.name
    return (any(part.casefold() in _SKIP_DIRS for part in p.parts)
            or name.startswith('test_') or '.test.' in name or '.spec.' in name)


def frontmatter_span(text: str):
    """YAML frontmatter 占的行号区间（1 起、含首尾 `---`），没有就返回 ``None``。

    ## 为什么必须跳过它（2026-10-03 本地接地评估实测）

    以前 `_scan` **逐行扫全文**，包括 SKILL.md 顶部那段 frontmatter。
    于是这样的行会被当成一条**硬性规则**：

        description: 面向独立开发者与产品设计师。提供安装确认 UX 设计、内容哈希
        校验、人话权限清单、默认拒绝、越界自动拒绝、无交互默认拒绝、负面断言必须
        由静态检测背书（不能由声明背书）。代码示例以 Python + JSON Schema 实现。

    它命中了 constraints 的模式（默认拒绝 / 越界 / 必须），于是**包的自我描述
    被当成了方法本身的约束**。实测 98 个碰撞候选里 **54.1% 至少有一侧是 frontmatter**，
    其中一侧的内容是 `summary: … ¥99 一次性买断，不订阅。`——
    拿**售价行**当「动作」，那不是碰撞，那是噪声。

    frontmatter 描述的是**这个包是什么**（名字、简介、版本、许可），不是**它怎么做**。
    混进能力结构里，它会让跨域比较看起来有共同点，而那个共同点是自我介绍。

    ## 只认文件第一行是 `---` 的那一段

    markdown 正文里 `---` 是常见分隔线。所以判据必须是**第一行就是 `---`**，
    再找下一个 `---`——不能见到 `---` 就跳，否则会把正文整段吞掉。
    """
    lines = (text or '').splitlines()
    if not lines or lines[0].strip() != '---':
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() in ('---', '...'):
            return (1, i + 1)
    return None                      # 没有闭合的 frontmatter：不当成 frontmatter


def _scan(files) -> dict:
    """把每个轴的命中按顺序收集，附带出处。"""
    limit = POLICY['extraction']['max_items_per_axis']
    out = {axis: [] for axis in POLICY['axes']}
    for f in files:
        if f.get('status') != 'text' or _is_test_path(f['path']):
            continue
        body = f.get('text') or ''
        # frontmatter 是包元数据，不是方法本身——见 frontmatter_span 的说明。
        span = frontmatter_span(body)
        for line_no, line in enumerate(body.splitlines(), 1):
            if span and span[0] <= line_no <= span[1]:
                continue
            if len(line) > POLICY['extraction']['max_line_chars']:
                continue
            stripped = line.strip()
            if len(stripped) < POLICY['extraction']['min_content_chars']:
                continue
            for axis, spec in POLICY['axes'].items():
                if len(out[axis]) >= limit:
                    continue
                for pattern in spec['patterns']:
                    found = re.search(pattern, stripped, re.IGNORECASE)
                    if not found:
                        continue
                    out[axis].append({
                        'text': stripped[:220],
                        'evidence': {'path': f['path'], 'start_line': line_no, 'end_line': line_no,
                                     'sha256': f['sha256'], 'quote': stripped[:220]},
                    })
                    break
    return {axis: rows for axis, rows in out.items() if rows}


def _shape_of(axes: set) -> list:
    return [name for name, spec in POLICY['shapes'].items() if set(spec['axes']) <= axes]


def extract(label: str, package: dict) -> dict:
    """从一个已校验的包快照抽能力结构。纯函数，输出有界且稳定。"""
    items = _scan(package['files'])
    present = set(items)
    profile = {
        # 计数覆盖全部轴（含 0），便于界面显示「这一类没有」；
        # has 只列真正抽到的轴——它是集合语义，不能用全轴字典冒充。
        'axis_counts': {axis: len(items.get(axis, [])) for axis in sorted(POLICY['axes'])},
        'has': sorted(present),
        'shapes': _shape_of(present),
    }
    identity = {'policy': policy_digest(), 'label': label, 'source': package['sha256'],
                'axis_counts': profile['axis_counts']}
    return {
        'label': label,
        'source_sha256': package['sha256'],
        'policy_version': POLICY['version'],
        'policy_sha256': policy_digest(),
        'profile_sha256': digest(canonical(identity).encode('utf-8'))[:16],
        'profile': profile,
        'items': items,
        'meaning': '结构指纹：这个包在结构上做了什么形状的事。不是能力理解，也不是成功概率。',
        'understood': False,
    }


def _surface_text(axis: str, a: dict, b: dict) -> str:
    left, right = a['evidence']['quote'][:40], b['evidence']['quote'][:40]
    return (f"{POLICY['axes'][axis]['label']}：A「{left}」/ B「{right}」"
            f" —— {POLICY['axes'][axis]['note']}")


def _scenario_terms(condition_text: str) -> tuple:
    """从一个条件分支里抽出它**触发的环境/场景词**，并区分强弱。

    ## 为什么分强弱（2026-10-01 实测踩到的坑）

    第一版把条件里所有被引号包住的字面量和 `==` 右边的值都当场景词，
    结果 `if any(n["val"] == nonce for n in self.seen[tid]):` 贡献了
    `nonce` 与 `val` —— **那是 B 的局部变量，不是环境场景**。
    三个「未覆盖面」里有两个是噪声。

    噪声比漏报更糟：每个 gap 都要有人或模型去处理，噪声会淹没真信号。
    所以分成两档：
      · **strong** —— 首字母大写的专有名词（Windows / Linux / macOS）、
        或含 `/` `.` 的路径字面量。这类**一定是环境**，全量报出。
      · **weak**   —— 小写的局部变量名。只统计数量，不逐条列出，
        免得 `nonce`、`val`、`tid` 这类东西占满报告。
    """
    import re
    quoted = re.findall(r'["\']([A-Za-z_][A-Za-z0-9_]{2,})["\']', condition_text or '')
    right = re.findall(r'==\s*["\']?([A-Za-z_][A-Za-z0-9_]{2,})', condition_text or '')
    stop = {'encoding', 'rb', 'utf', 'r', 'w', 'true', 'false', 'null', 'utf_8'}
    strong, weak = set(), set()
    for term in set(quoted) | set(right):
        if term.lower() in stop or len(term) <= 2:
            continue
        if term[0].isupper() or '/' in term or '.' in term:
            strong.add(term)
        else:
            weak.add(term)
    # 词表命中的也算强场景词（2026-10-04 机主裁定口径）。
    #
    # **必须按大小写去重**：句法路径给出原形 `Windows`（带引号），
    # 词表路径给出 `windows`（原文里是小写形态）——
    # 同一个环境被当成两个词，于是同一个 gap 被报两次。
    already = {t.lower() for t in strong}
    strong |= {t for t, _ in environment_hits(condition_text) if t.lower() not in already}
    weak -= strong
    weak -= {t for t in weak if t.lower() in {s.lower() for s in strong}}
    return strong, weak


# ── 环境词表（2026-10-04 机主裁定：白名单，不是启发式）────────────────
#
# ## 为什么要从「句法抽取」改成「词表匹配」
#
# 分布诊断（`evaluations/collision_path.py`，36 对真实包）查出来的：
# 失效面只产出 **8/36** 对，而 **9 个包里只有 1 个**写得出来。
# 因为 `_scenario_terms` 是**句法代理**——它靠「引号字面量 / `==` 右值」+
# 「大写或含 `/.`」去**猜**「像不像环境」，于是：
#
#     if platform.system() == "Windows":    ← 猜得到
#     if k not in ALLOWED_KEYS:            ← 猜不到（`not in` 不是 `==`）
#     if scan["critical"]:                 ← 降级成 weak（小写）
#     「新模型一定更好」被当成事实写进报告      ← 压根不是代码
#
# **换句话说：失效面能不能产出，取决于包的条件分支是否恰好写成
# `X == "ProperNoun"`。** 换个书写风格就抽不出来。
#
# ## 为什么是白名单而不是更大的启发式
#
# `capability.py` 原有的告诫在这里成立：
# 「噪声比漏报更糟——每个 gap 都要有人或模型去处理，噪声会淹没真信号」。
# 启发式（黑名单）**无法给噪声定上界**；白名单可以：
# **报出的强词数 ≤ 词表大小**，而词表是有限的、可审计的。
#
# ## 只放 OS / 平台档
#
# 实测（词边界匹配，2026-10-04）：**架构档 0/9 包命中、运行时档 0/9 包命中**。
# 加一个命中不了的档位只会让词表看起来更完备、实际行为完全不变——
# 所以它们**不进来**，等真有材料支撑再说。
#
# ## 扩充纪律
#
# **每次扩充必须有真实材料支撑**，并在 CHANGELOG 记明是哪些包贡献的词。
# 不凭想象加——一个想象出来的词会让「噪声有硬上界」这句话当场失效。
ENVIRONMENT_LEXICON = (
    # 操作系统与平台
    'windows', 'win32', 'win64', 'wsl', 'cygwin', 'msys',
    'linux', 'posix', 'unix', 'bsd', 'macos', 'mac os', 'os x', 'darwin',
    'android', 'ios',
    # 文件系统差异（中文条件句里也常见）
    '路径分隔', '大小写不敏感', '大小写敏感',
)

#: 每份材料的 `conditions` 最多贡献几个强场景词。
#: **这是噪声的硬上界。** 超出就丢弃并记进 `weak_terms`——
#: 宁可少报，也不要让一个包把报告撑满。
MAX_SCENARIO_TERMS = 3

#: 纯 ASCII 词用词边界；含中文/空格的退回子串。
_ASCII_TERM = re.compile(r'[a-z0-9_. ]+')


def environment_hits(text: str) -> list:
    """在文本里找环境词表命中的词，返回 `[(词, 字符偏移)]`。

    **必须词边界匹配。** 2026-10-04 第一版用子串匹配，
    `nt` 于是命中了 `print` / `environment` / `different`——
    一个**假阳性**把天花板从 15/36 虚报成 21/36。
    短词（`nt`、`ios`）尤其危险，所以这里不给它们任何子串余地。
    """
    low = (text or '').lower()
    hits = []
    for term in ENVIRONMENT_LEXICON:
        pattern = (r'(?<![a-z0-9_])' + re.escape(term.strip()) + r'(?![a-z0-9_])'
                   if _ASCII_TERM.fullmatch(term)
                   else re.escape(term))
        match = re.search(pattern, low)
        if match:
            hits.append((term.strip(), match.start()))
    return hits


def bridge_failures(cap_a: dict, cap_b: dict) -> dict:
    """**机制级失效面**：A 的哪条机制，在 B 的哪个场景下没有任何约束覆盖。

    ## 为什么要有它

    `bridge()` 现在只回答「结构上有没有共同轴」，`surfaces` 也只是
    「A 的第一条 / B 的第一条」并排摆着（`items[axis][0]`）——
    **它从不说「A 在 B 的哪个场景下会失效或没被覆盖」**。
    而那恰恰是跨包碰撞真正会发生的地方。

    ## 判据（纯确定性，不调模型）

    1. 从 B 的 `conditions` 抽**强场景词**（专有名词 / 平台名 / 路径）；
    2. 拿 A 的 `constraints`（硬性规则）逐条去覆盖；
    3. 场景词在 A 的**任何**约束里都没出现 → 这是一个**未覆盖面**。

    例：`device-key-attestation` 有 `if platform.system() == "Windows":`，
    而 `ast-static-scan-arch` 的约束里一次都没提 `Windows` ——
    「A 在 Windows 场景下没有任何约束覆盖」是**可核的事实**，不是推测。

    **本函数不判断两个包该不该融合**，只如实列出没被覆盖的地方——
    与 `bridge()` 的职责边界一致：结构事实归本地，判断归模型或人。
    """
    a_conditions = cap_a['items'].get('conditions') or []
    b_conditions = cap_b['items'].get('conditions') or []
    a_constraints = cap_a['items'].get('constraints') or []
    b_constraints = cap_b['items'].get('constraints') or []
    if not (a_conditions or b_conditions) or not (a_constraints or b_constraints):
        # **两个分支必须返回同一套键。** 早返回少给几个键，调用方在「算不出来」
        # 的路径上就会 KeyError——而那正是最不该崩的路径（材料本来就不足时）。
        return {'version': 'capability-bridge-failures-v1',
                'gaps': [], 'checked': 0, 'covered': 0, 'gap_count': 0,
                'forward_gaps': 0, 'backward_gaps': 0, 'weak_terms': 0,
                'note': '两边缺少条件分支或硬性约束——本层算不出未覆盖面，'
                        '这不等于「没有缺口」。',
                'semantic_verified': False}

    def uncovered(side_conditions, other_constraints, side, other):
        """`side` 的环境场景，有几个在 `other` 的硬性约束里没有任何提及。

        **`other_constraints` 必须真的是 `other` 的约束**——第一版两向都传了
        cap_a 的 constraints，于是 backward 那一侧在拿 A 的规则去覆盖 A 的场景，
        结果恒为 0，且看起来「A 没有缺口」——是守卫抓出来的。
        """
        other_lower = ' \n '.join(str(c.get('text', ''))
                                  for c in other_constraints).lower()
        found, covered, weak = [], 0, set()
        seen = set()
        for cond in side_conditions:
            text = str(cond.get('text', ''))
            strong, w = _scenario_terms(text)
            weak |= w
            # **噪声判据 ①：每个词必须能指回原文。** 词表命中自带字符偏移；
            # 句法抽出来的词也用 `text.find` 定位——定位不到就**不进** strong。
            offsets = dict(environment_hits(text))
            for term in strong:
                # **按大小写去重**：同一个环境写成 `Windows` 或 `windows`
                # 只算一次——否则同一个 gap 会被报两遍。
                if term.lower() in seen:
                    continue
                seen.add(term.lower())
                if term not in offsets:
                    # **必须大小写不敏感地找。** 词表项是小写（`macos`），
                    # 而原文常写成 `macOS`——直接 `find` 找不到就把词丢了，
                    # `anchored_share` 因此从 0.914 掉到 0.857。
                    # **这条守卫（冻结基线）当场把它抓了出来。**
                    offset = text.lower().find(term.lower())
                    if offset < 0:
                        # 定位不到 = 无法核对 = 不报。**宁可漏报，不可报个查无实据的。**
                        continue
                    offsets[term] = offset
                if term.lower() in other_lower:
                    covered += 1
                    continue
                found.append({
                    'scenario_term': term,
                    # **噪声判据 ②：已覆盖的不报。**（上面的 `continue` 就是）
                    'term_offset': offsets[term],
                    'side': side,
                    'from_side': {'text': text[:160], 'evidence': cond.get('evidence')},
                    'why': f'{side} 的环境场景 `{term}` 在 {other} 的 '
                           f'{len(other_constraints)} 条硬性约束里一次都没被提到，'
                           f'所以 {other} 在这个场景下**未被约束覆盖**',
                    'kind': 'uncovered_scenario',
                })
        # **噪声判据 ③：每侧最多 `MAX_SCENARIO_TERMS` 个词。**
        # 超出就丢弃并**如实计数**——静默丢弃和「本来就没有」看起来一模一样。
        if len(found) > MAX_SCENARIO_TERMS:
            dropped = found[MAX_SCENARIO_TERMS:]
            found = found[:MAX_SCENARIO_TERMS]
            weak |= {'(超过 %d 上限已丢弃: %s)' % (MAX_SCENARIO_TERMS,
                                        ','.join(g['scenario_term'] for g in dropped))}
        return found, covered, seen, weak

    fwd, cov_fwd, seen_f, weak_f = uncovered(b_conditions, a_constraints, 'B', 'A')
    bwd, cov_bwd, seen_b, weak_b = uncovered(a_conditions, b_constraints, 'A', 'B')
    gaps = fwd + bwd
    return {
        'version': 'capability-bridge-failures-v1',
        'gaps': gaps[:8],
        'checked': len(seen_f) + len(seen_b),
        'covered': cov_fwd + cov_bwd,
        'gap_count': len(gaps),
        'forward_gaps': len(fwd),
        'backward_gaps': len(bwd),
        # 小写局部变量名（nonce / val / tid…）不是环境场景，只记数不逐条列出。
        'weak_terms': len((weak_f | weak_b) - seen_f - seen_b),
        'note': (f'双向检查了 {len(seen_f) + len(seen_b)} 个环境场景词：'
                 f'A 未覆盖 B 的场景 {len(fwd)} 个，B 未覆盖 A 的场景 {len(bwd)} 个'
                 + (f'（另有 {len((weak_f | weak_b) - seen_f - seen_b)} 个局部变量名'
                    f'不是环境场景，未计入）'
                    if (weak_f | weak_b) - seen_f - seen_b else '')
                 + '。**未覆盖不等于有问题**，只说明这一侧在该场景下没有可援引的规则——'
                   '要不要补、补什么，交给模型或人。'),
        'semantic_verified': False,
    }


def bridge(cap_a: dict, cap_b: dict) -> dict:
    """确定性的桥接：只看结构轴的重叠，不看词。

    这里**不判断**两个包是否真的能碰撞——那要交给模型或人。这里只负责
    「结构上有没有可以对接的地方」，并如实说明没对上时是什么情况。
    """
    cfg = POLICY['bridge']
    has_a, has_b = set(cap_a['profile']['has']), set(cap_b['profile']['has'])
    shared = sorted(has_a & has_b)
    union = has_a | has_b
    overlap = round(len(shared) / len(union), 3) if union else 0.0
    surfaces = []
    if len(shared) >= cfg['min_shared_axes']:
        for axis in shared[:cfg['max_surfaces']]:
            surfaces.append({
                'axis': axis,
                'label': POLICY['axes'][axis]['label'],
                'why': POLICY['axes'][axis]['note'],
                'a': cap_a['items'][axis][0],
                'b': cap_b['items'][axis][0],
            })
    if surfaces:
        # status 只回答「有没有落点」这个事实；强弱由 surface_count 与 structural_overlap 表达。
        # 2 个共同结构轴（哪怕零共同词汇）已经足够跨域对接，不该被说成「薄弱」。
        status, reason = 'cross_domain_surface_found', 'surfaces_found'
        note = '结构上有共同轴，跨域碰撞在这里有落点；强度见下方度量。'
    else:
        status = 'no_structural_overlap'
        if shared:
            # 「没落点」有两种**性质完全不同**的机制，混成一句话就是在骗人：
            # 这里离门槛只差几个轴（实测 36 对真实包里的 3 个负面案例全在这里），
            # 告诉用户「没有共同项」会让他以为材料不兼容，而真实情况是几乎就绪。
            reason = 'below_threshold'
            note = (f'两边共有 {len(shared)} 个结构轴（{"、".join(shared)}），'
                    f'低于门槛 {cfg["min_shared_axes"]}，因此本层不给落点。'
                    f'这是**门槛没到**，不是材料不兼容——补上第 {cfg["min_shared_axes"]} 个共同轴即可，'
                    f'或交由模型探索。')
        else:
            reason = 'no_shared_axes'
            note = '两包在结构轴上没有任何共同项——本层按构造给不出方向，需要模型探索或人工判断。'
    return {
        'bridge_version': cfg['version'],
        'status': status,
        'no_surface_reason': reason,
        'min_shared_axes': cfg['min_shared_axes'],
        'note': note,
        'shared_axes': shared,
        'only_in_a': sorted(has_a - has_b),
        'only_in_b': sorted(has_b - has_a),
        'structural_overlap': overlap,
        'shared_axis_count': len(shared),
        'surface_count': len(surfaces),
        'surfaces': [_surface_text(s['axis'], s['a'], s['b']) for s in surfaces],
        'shapes_only_a': cap_a['profile']['shapes'],
        'shapes_only_b': cap_b['profile']['shapes'],
        'semantic_verified': False,
    }
