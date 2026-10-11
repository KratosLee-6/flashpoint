"""The exact JSON contract used in prompts and local structural validation.

Only the schema keywords below are used/supported; this is not a general JSON Schema engine.
Evidence/feasibility checks are additional and cannot prove semantic correctness.
"""
import re
from copy import deepcopy

from .packages import FusionError, safe_path

# ── 字段级不变量清单（单一数据源）────────────────────────────────────
# 为什么要有这一份清单：2026-10-01 用真实 `glm-4.7` 跑完整方案，**五次全被契约拒绝**，
# 而五次都是**契约判对、提示词缺边界**——引文可转写、空数组要显式、`independent` 两义、
# `gaps_to_fill` 双向锁、`retention` 两个数组非空。每条隐含约定都要靠真实调用去撞一次，
# 那个代价太高。
#
# 做法：把「契约强制、但模型看不见」的规则**声明在这里**，由 `render_invariants()` 渲染进
# SYSTEM，再由测试逐条断言「不变量在 SYSTEM 里」。**漂移因此不可能再发生**——
# 新增契约规则时忘记写提示词，测试会红。
#
# 每条只写「模型必须知道什么」，不写「契约怎么实现」——后者是代码的事。
PROMPT_INVARIANTS = [
    {
        'id': 'profiles_exactly_two',
        'text': 'profiles MUST contain exactly two entries: one with parent "A" and one with parent "B".',
    },
    {
        'id': 'candidate_must_anchor_to_collision',
        'text': 'A candidate oriented A or B MUST set direction_id to one of the collision_id values '
                'supplied under the packet\'s collision.candidates — those are locally computed '
                'landing points. Do not invent an id, and do not restate one of them as your own '
                'idea. Only orientation C may use "independent", because the third class is by '
                'definition unrelated to A and B. If the packet supplied no collision candidates, '
                'you may explore freely.',
    },
    {
        'id': 'candidates_exactly_three',
        'text': 'candidates MUST contain exactly three entries, whose orientations are exactly A, B and C — one each. '
                'A direction that turns out unsuitable is marked not_recommended, not omitted.',
    },
    {
        'id': 'retention_arrays_non_empty',
        'text': 'Each profile\'s retention.keep AND retention.discard must each hold 1 to 8 entries. '
                'An empty discard is rejected: every real Skill carries material that does not transfer, '
                'so an empty discard means you have not finished reading the package.',
    },
    {
        'id': 'capabilities_non_empty',
        'text': 'Each profile\'s capabilities must hold 1 to 16 entries; every claim needs at least one evidence reference.',
    },
    {
        'id': 'ablations_exactly_two',
        'text': 'Each candidate\'s ablation_checks must contain exactly two entries: one removing A and one removing B.',
    },
    {
        'id': 'baselines_exactly_five',
        'text': 'Each evaluation entry\'s baselines must contain exactly these five, with no duplicates: '
                'A_only, B_only, concatenation, fusion, model_only.',
    },
    {
        'id': 'contribution_kind_required',
        'text': 'Each contribution MUST set kind. Use "way_of_working" when you borrowed the SHAPE of how the other '
                'Skill works (its steps, conditions, constraints, checks), and "capability" only when you borrowed '
                'what it can do. If you claim outcome_class "independent_usable" — landing somewhere that belongs '
                'to neither domain — BOTH contributions must be "way_of_working", with evidence pointing at the '
                'lines that show the method rather than the topic.',
    },
    {
        'id': 'gap_lock_both_directions',
        'text': 'gaps_to_fill and expected_outcome.outcome_class are locked together and BOTH directions are checked: '
                'a candidate whose outcome_class is "needs_ai_fill" MUST have a non-empty gaps_to_fill, and a '
                'candidate of ANY other class (beats_A, beats_B, beats_both, independent_usable) MUST leave '
                'gaps_to_fill EMPTY. Listing gaps while promising value is treated as a contradiction, '
                'not as extra caution. '
                # 2026-10-10 在真实模型上实测：没有这句澄清时，6 个候选的
                # gaps_to_fill 全部为空，第 5 类一次都没出现，补足闭环从未被触发——
                # 因为上面这条锁**同时**禁止「认领价值 + 列缺口」，
                # 于是「不认领第 5 类」和「不列缺口」是同一个开关的两面。
                # 根因是语义歧义：SYSTEM 全文的 missing 一贯指材料/包层面，
                # 而 gaps_to_fill 指方案层面，两处都没说。
                'CLARIFICATION (measured 2026-10-10 on the real model): gaps_to_fill means gaps '
                'in YOUR OWN proposal — parts you could not write, evidence you could not support '
                'from the supplied material, or results the two packages cannot deliver as-is. '
                'It is NOT about missing package material. If your proposal is genuinely complete, '
                'leave it empty; if any part of it is still unsupported, say exactly what and why '
                'and claim needs_ai_fill accordingly.',
    },
    {
        'id': 'direction_reviews_mirror_suggestions',
        'text': 'direction_reviews must contain exactly one entry per entry in rule_directions.suggestions, '
                'with matching direction_id, and nothing else. If rule_directions.suggestions is EMPTY then '
                'direction_reviews MUST be an empty array — never invent a direction ID. '
                'Capability-layer bridging surfaces are NOT rule_directions suggestions and are NOT reviewable '
                'directions: never review a surface.',
    },
    {
        'id': 'independent_has_two_meanings',
        'text': '"independent" means two unrelated things, and mixing them up is the most common rejection: '
                'orientation "C" is the independent-new-scenario orientation, while direction_id "independent" '
                'means "this candidate is not anchored to a rule-layer suggestion". They are independent of each '
                'other: an A-oriented or B-oriented candidate may legitimately use direction_id "independent", '
                'and it usually must whenever rule_directions.suggestions contains no suggestion of that '
                'orientation (the normal case for a cross-domain pair). In that case say so in rationale and '
                'justify the departure from evidence.',
    },
    {
        'id': 'evidence_path_must_exist',
        'text': 'Every evidence.path MUST be copied verbatim from the package inventory shown to you — '
                'that inventory is the COMPLETE list of citable files, and nothing else may be cited. '
                'A heading inside a document is NOT a filename: if you read a section like '
                '"### 4) 5 类越界规则" or a code block titled "SINKS", that text lives INSIDE a file '
                'you must already be citing; do not invent "auth_audit_rules.md" or similar from it. '
                'If a package contains only SKILL.md, then only SKILL.md may be cited. A path that is '
                'not in the inventory is rejected outright as unsafe.\n'
                '    CRITICAL — evidence.path and artifacts[].path are DIFFERENT fields and never '
                'interchangeable. evidence.path names an EXISTING file you are quoting from; '
                'artifacts[].path names a file the NEW Skill is supposed to produce. '
                'Never write a name you intend to CREATE (a rules file, a report, a checker) into '
                'evidence.path — that field may only ever hold a path from the inventory. '
                'Invented names are the single most common rejection in real runs.',
    },
    {
        'id': 'artifact_path_needs_extension',
        'text': 'Every artifacts[].path MUST end with a real file extension — tool.py, schema.json, '
                'checklist.md, template.txt and so on. A name that just stops after a dot '
                '("audit_report_schema.") is rejected: it means the extension was never written, '
                'and nothing here can guess whether you meant .py, .json or .md.',
    },
    {
        'id': 'blocked_needs_blockers',
        'text': 'A candidate marked blocked MUST list at least one concrete blocker. Conversely, a candidate that '
                'lists no blocker may not be marked blocked.',
    },
    {
        'id': 'blockers_imply_blocked',
        'text': 'The rule runs both ways, not one: a candidate marked "blocked" MUST list at least one '
                'blocker, AND a candidate that lists any blocker MUST be marked "blocked" or '
                '"not_recommended" — never "hypothesis". If you wrote a blocker, the candidate '
                'cannot also be claiming it is ready to build.',
    },
    {
        'id': 'no_extra_fields',
        'text': 'Every object must contain exactly the fields shown in the schema and no others — additional '
                'properties are rejected, at every nesting level.',
    },
    {
        'id': 'coverage_reflects_missing',
        'text': 'A profile with any entry in missing MUST use coverage "partial". A profile may only claim '
                '"sufficient_for_proposal" when missing is empty.',
    },
    {
        'id': 'no_unescaped_quotes_in_string_values',
        'text': 'JSON string values must never contain a raw escaped quote such as \\". '
                'When a value needs to quote source code that itself uses double quotes, '
                'use single quotes around the code instead of escaping the inner ones, '
                'or describe the construct in words. A missing escape makes the whole '
                'response unparseable and nothing is saved.',
        # 2026-10-03 A/B 实测（M3 / thinking disabled / 32768，每臂 4 次）：
        #   带上这行：语法失败 **0/4**；不带：**3/4**（Extra data /
        #   Expecting property name / Expecting ',' delimiter）
        # 🔴 **2026-10-04 n=8 端到端重测：语法失败仍是 4/8。**
        #    **上面那个 0/4 是 n=4 的假象——本项目第八次小样本，
        #    而且这次它把一个负面结果翻成了正面。**
        #    实际死的那几次是 `Expecting ',' delimiter`（3 次）与
        #    `Expecting property name`（1 次），**都不是**「引号转义不配对」——
        #    所以这行提示词本来就打错了靶子，是 n=4 让它看起来像打中了。
        #
        #    **保留它的理由不是「有效」，是「原理上无害」**：模型本就该做对，
        #    代价只有一行提示词长度。**它目前没有任何数据支持。**
        #    也不因为「猜它没用」就撤掉——撤销一条无害提示同样要数据支撑。
        # 带上这行之后浮出一个**新类别**：重复键。那是我们自己的严格检查
        # 拒的（语法是好的），**不算这条提示词的失败**，但值得单独盯。
    },
    {
        'id': 'no_evidence_sha256',
        'text': 'Do NOT write a sha256 field on any evidence item. It is filled in locally '
                'from the real file after you answer, and writing it yourself is a common '
                'cause of the whole response becoming unparseable: copying 64 hexadecimal '
                'characters is pure transcription, it carries no reasoning, and 2026-10-03 '
                'measurement showed those fields were 12.3% of your output while a real '
                'failure was caused by one of them being cut off mid-value.',
    },
    {
        'id': 'workflow_needed_unless_rejected',
        'text': 'A candidate that is not marked not_recommended MUST provide a non-empty workflow; a workflow may '
                'be empty only for not_recommended candidates, which then build no package.',
    },
]


def render_invariants() -> str:
    """把不变量清单渲染成 SYSTEM 的一段。**不要手写第二份**——那必然漂移。"""
    return ('\nThe following field-level rules are ENFORCED by the validator, not suggestions. '
            'Violating any of them causes the entire result to be rejected and nothing is saved.\n'
            + '\n'.join(f'- {item["text"]}' for item in PROMPT_INVARIANTS))



def string(maximum=6000):
    return {'type': 'string', 'minLength': 1, 'maxLength': maximum}


def array(items, minimum=0, maximum=24):
    return {'type': 'array', 'items': items, 'minItems': minimum, 'maxItems': maximum}


def obj(**properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def choice(*values):
    return {'type': 'string', 'enum': list(values)}


# ── 严格模式投影（`json_schema` 传输用）─────────────────────────────
#
#: 严格解码器只认这几个关键字。**长度与数量类约束不在其中**——
#: 它们本来就是**解码器做不到的事**（tokenizer 逐 token 生成，无法保证「恰好 3 个」），
#: 所以发过去要么被拒，要么被静默忽略。
_STRICT_DROP = frozenset({'minLength', 'maxLength', 'minimum', 'maximum',
                          'minItems', 'maxItems', 'minProperties', 'maxProperties'})
#: 这些是投影后**必须**保留的：少了任何一个，严格模式就会形同虚设。
_STRICT_KEEP = frozenset({'type', 'properties', 'required', 'additionalProperties',
                          'items', 'enum', 'description', '$ref', '$defs', 'anyOf'})


def strict_schema(schema=None, _depth=0):
    """把 `RESULT_SCHEMA` 投影成**严格模式能接受**的子集。

    ## 为什么要投影而不是直接发（2026-10-03）

    `RESULT_SCHEMA` 带着 `minLength` / `maxItems` 这类关键字，它们表达的是
    「候选恰好 3 个」「`profiles` 恰好 2 个」——**但严格解码器做不到这件事**：
    它逐 token 约束语法，保证不了「恰好 3 个」。所以原样发过去，
    要么被 400 拒，要么被静默忽略（后者更糟——用户以为有约束，其实没有）。

    投影后保留的只有解码器**真能强制**的东西：
    对象结构、字段名、必填项、枚举值。

    **被丢掉的那些不是不管了**——它们由 `validate_result()` 在本地强制执行，
    守卫仍是 `PROMPT_INVARIANTS` + 契约校验。生成期少拦一点，验收期一分不让。

    ## ⚠️ 但它目前**没有接进任何请求**（2026-10-03 实测）

    逐家实测的结论：`json_schema` 在实测过的三个端点上**是个安慰剂**——
    全部 200 OK，然后**把整个 schema 丢弃**，MiniMax 与智谱 Coding 的原话是
    「你没给我 schema」。留档 `evaluations/json-schema-{support,enforcement}.json`，
    守卫 `test_json_schema.py` 钉住「没有人能顺手把它打开」。

    所以这个函数现在**只被测试调用**。留着它的理由：它把
    「哪些约束解码器做得到、哪些做不到」这条知识固化成了**可执行的代码**，
    而不是留在某段注释里。**哪天真有一家支持了**（必须像这次一样实测），
    才把它接进 `chat_complete`——接之前先改那条守卫并写清证据。

    **被丢掉的那些不是不管了**——它们由 `validate_result()` 在本地强制执行，
    守卫仍是 `PROMPT_INVARIANTS` + 契约校验。生成期少拦一点，验收期一分不让。

    （2026-10-03 第一版探针曾得出「投影后可通过、顶层键一致」的结论，
    第二版加大预算并显式关掉思考后发现那是**探针的缺陷**——200 token 根本写不完。
    判别式探针给出的最终结论是「端点丢弃 schema」。**探针本身有缺陷时，
    结论必须重测，不能保留。**）
    """
    if _depth > 40:                       # 防御性上限：schema 本来就没有环
        raise FusionError('Schema nesting is too deep to project')
    if schema is None:
        schema = RESULT_SCHEMA
    if isinstance(schema, list):
        return [strict_schema(s, _depth + 1) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out = {}
    for key, value in schema.items():
        if key in _STRICT_DROP:
            continue
        if key not in _STRICT_KEEP:
            continue                     # 未知关键字一律不外发
        if key in ('properties', '$defs'):
            out[key] = {k: strict_schema(v, _depth + 1) for k, v in value.items()}
        elif key in ('items', 'additionalProperties'):
            out[key] = strict_schema(value, _depth + 1) if isinstance(value, dict) else value
        else:
            out[key] = value
    return out


EVIDENCE = obj(parent=choice('A', 'B'), path=string(512), sha256=string(64),
               start_line={'type': 'integer', 'minimum': 1},
               end_line={'type': 'integer', 'minimum': 1}, quote=string(3000))

#: **本地填、不该问模型的字段**。
#:
#: 判据是**对象形状**不是路径——`EVIDENCE` 在 schema 树里被引用了多处
#: （`profiles[].capabilities[].evidence`、`candidates[].contribution_a.evidence`…），
#: 路径各不相同，按路径匹配会**一处都摘不掉**。
EVIDENCE_KEYS = {'parent', 'path', 'start_line', 'end_line', 'quote'}


def _strip_local_filled(node):
    """从一份 schema 副本里摘掉本地填的字段。**不改原对象。**

    判据：`properties` 恰好是 EVIDENCE 形状（五个字段齐全）时，
    把 `sha256` 从 `properties` 和 `required` 里一起摘掉。
    """
    out = {}
    for k, v in node.items():
        out[k] = _strip_local_filled(v) if isinstance(v, dict) else v
    props = out.get('properties')
    if isinstance(props, dict) and EVIDENCE_KEYS <= set(props) \
            and 'sha256' in props:
        props.pop('sha256')
        if isinstance(out.get('required'), list):
            out['required'] = [f for f in out['required'] if f != 'sha256']
    return out


def wire_schema():
    """**发给模型的**结果 schema——与验收用的 `RESULT_SCHEMA` 差在本地填的字段上。

    ## 为什么必须分两份（2026-10-06 实测）

    `no_evidence_sha256` 写着「Do NOT write a sha256 field」，
    骨架的 evidence 槽里也**没有** sha256——
    可发出去的那份 `RESULT_SCHEMA` 里 `EVIDENCE` **仍把 sha256 列为必填**。

    三处信号，两处说「不许写」、一处说「必填」。
    模型面对矛盾时选择了**写**：实测样本 `deep-cb-r06` 里 sha256 被写成了
    **重复键**（两个值只差一个字符），然后整份结果被严格校验拒掉。

    而 sha256 曾经占过输出的 **12.3%**（`evaluations/copy-load.txt`）。

    **验收那一侧不动**：`fill_evidence_hashes` 在 `validate_result` 之前填好，
    所以验收看到的对象**本来就该有** sha256，schema 要求它是自洽的。
    这与 relocation mark 的处理是同一族——**发给模型的形状 ≠ 验收的形状**。
    """
    return _strip_local_filled(deepcopy(RESULT_SCHEMA))


def wire_item_schema(key: str):
    """**单项级**的 wire schema（语义分段用）：`key` 下一个条目的形状，sha256 已摘。

    与 `wire_schema` 同一条规则、同一份数据源，只是截取到单个条目这一层——
    段级指令与段级校验都从它生成，契约改动自动同步，不存在第二份手写清单。
    """
    section = RESULT_SCHEMA.get('properties', {}).get(key)
    if not isinstance(section, dict) or 'items' not in section:
        raise FusionError(f'No per-item schema for section {key!r}')
    return _strip_local_filled(deepcopy(section['items']))
CONTRIBUTION = obj(
    # 贡献分两种，这是整个契约最要紧的一处设计（39 号文 §7.9）：
    #   capability      —— 借了对方「会做什么」
    #   way_of_working  —— 借了对方「怎么做事的结构」（步骤/条件/约束/校验）
    # 跨域涌现的方案往往与 A、B 的**具体能力**无关，只借了它们的**做法**。
    # 以前只有一个 CONTRIBUTRIBUTION，等于逼模型对涌现方案硬凑「双方各自的能力贡献」，
    # 于是涌现被压成合并（M2 实测 fusion_without_A > fusion 就是这个偏向的后果）。
    kind=choice('capability', 'way_of_working'),
    mechanism=string(), evidence=array(EVIDENCE, 1, 8))
RESULT_SCHEMA = obj(
    direction_reviews=array(obj(direction_id=string(100), decision=choice('adopt', 'adapt', 'reject'), reason=string()), 0, 6),
    profiles=array(obj(
        parent=choice('A', 'B'),
        capabilities=array(obj(claim=string(), basis=choice('observed', 'declared', 'inferred'),
                               evidence=array(EVIDENCE, 1, 8)), 1, 16),
        # 显式取舍：全量合并会稀释双方（M2 实测 fusion_without_A > fusion），必须说清留什么、弃什么。
        retention=obj(keep=array(string(), 1, 8), discard=array(string(), 1, 8), reason=string()),
        dependencies=array(string()), missing=array(string()),
        coverage=choice('partial', 'sufficient_for_proposal')), 2, 2),
    candidates=array(obj(
        orientation=choice('A', 'B', 'C'), direction_id=string(100), disposition=choice('hypothesis', 'blocked', 'not_recommended'),
        # 五类结果口径（机主 2026-09-30）：比 A 好 / 比 B 好 / 与 A、B 无关但可用 / 比双方都好 /
        # 不可用有缺失需 AI 补足。模型必须先认领类别并给依据，事后才好判断它是否食言。
        expected_outcome=obj(outcome_class=choice('beats_A', 'beats_B', 'independent_usable', 'beats_both', 'needs_ai_fill'),
                              basis=string()),
        gaps_to_fill=array(string(), 0, 8),
        title=string(200), problem=string(), contribution_a=CONTRIBUTION, contribution_b=CONTRIBUTION,
        new_mechanism=string(), workflow=array(string(), 0, 12),
        artifacts=array(obj(path=string(512), purpose=string()), 0, 16),
        blockers=array(string()), rationale=string(),
        ablation_checks=array(obj(removed_parent=choice('A', 'B'), task=string(), expected_loss=string()), 2, 2),
        evaluation=array(obj(task=string(), baselines=array(choice('A_only', 'B_only', 'concatenation', 'fusion', 'model_only'), 5, 5),
                             success_criterion=string()), 1, 8)), 3, 3))



def proposed_path(name: str) -> str:
    """校验**待产出**的文件路径（artifacts[].path）。

    与 `safe_path` 的区别正是问题的关键：
      · `safe_path` 管的是「引用包内哪个文件」——它必须落在包内清单里；
      · 本函数管的是「新 Skill 要造哪个文件」——它**必然不在包里**。

    仍然要挡的是真正危险的东西：路径穿越（`..`）、反斜杠、绝对路径、控制字符、
    Windows 保留名——这些会让产物写到包外或写到不该写的地方。
    不再要求它指向包内已有的文件。

    顺带把「以点结尾」这类截断的报错说清楚：它几乎总是模型把文件名写断了，
    而不是想指向某个隐藏文件。
    """
    parts = name.split('/')
    if (not name or '\\' in name or any(c in name for c in ':\x00')
            or any(ord(c) < 32 for c in name)):
        raise FusionError(f'Proposed artifact path is not a plain relative path: {name[:80]!r}')
    if any(p in {'', '.', '..'} for p in parts):
        raise FusionError(
            f'Proposed artifact path must stay inside the new Skill folder: {name[:80]!r}')
    if any(re.fullmatch(r'(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', p, re.I) for p in parts):
        raise FusionError(f'Proposed artifact path uses a reserved device name: {name[:80]!r}')
    if any(p.endswith(' ') for p in parts):
        raise FusionError(f'Proposed artifact path ends with a space: {name[:80]!r}')
    if any(p.endswith('.') for p in parts):
        # 这几乎总是「文件名被写断了」，而不是指向某个特殊文件——说清楚，别让用户猜。
        raise FusionError(
            f'Proposed artifact path ends with a dot, which usually means the file name was cut '
            f'short: {name[:80]!r}')
    if '.' not in parts[-1]:
        # 产出处路径必须带扩展名。
        #
        # 2026-10-01 用循环探针抓到实据：模型写出 'audit_report_schema.'，而它的 purpose
        # 明明写着「JSON Schema 定义」——**扩展名没写完**。同一份 content 里
        # completion_tokens 6776/8192、reasoning_tokens 0、finish_reason stop、
        # JSON 解析成功、截断点之后结构完整——**输出没有被截断，是模型主动写错**。
        #
        # 因此这条**不靠提示词、也不靠自动补全**（补成 .py/.json/.md 都没有依据），
        # 而是写成契约里一条可验证的规则：产出的文件得有扩展名。
        raise FusionError(
            f'Proposed artifact path needs a file extension (e.g. tool.py, schema.json): '
            f'{name[:80]!r}')
    return name


def check_shape(value, schema, location='result'):
    kind = schema['type']
    matches = {'object': type(value) is dict, 'array': type(value) is list,
               'string': type(value) is str, 'integer': type(value) is int}
    if not matches[kind]:
        raise FusionError(f'Invalid JSON type at {location}')
    if 'enum' in schema and value not in schema['enum']:
        raise FusionError(f'Invalid choice at {location}')
    if kind == 'object':
        if set(value) != set(schema['required']):
            # 2026-09-30 真实小模型跑出来才发现：原来只报「result 处字段不符」，
            # 用户完全不知道模型少给/多给了什么。模型不合规时要说清差在哪，
            # 否则「连接失败」和「方案不合格」在用户眼里没有区别。
            missing = sorted(set(schema['required']) - set(value))
            unknown = sorted(set(value) - set(schema['required']))
            raise FusionError(
                f'方案结构与约定不符（{location}）：缺少 {missing or "无"}；多出 {unknown or "无"}。'
                f'约定必填 {sorted(schema["required"])}')
        for key, child in schema['properties'].items():
            check_shape(value[key], child, f'{location}.{key}')
    elif kind == 'array':
        if not schema['minItems'] <= len(value) <= schema['maxItems']:
            raise FusionError(f'Invalid array length at {location}')
        for index, child in enumerate(value):
            check_shape(child, schema['items'], f'{location}[{index}]')
    elif kind == 'string':
        if not value.strip() or not schema.get('minLength', 1) <= len(value) <= schema.get('maxLength', 6000):
            raise FusionError(f'Empty or oversized text at {location}')
    elif value < schema.get('minimum', 0):
        raise FusionError(f'Invalid integer at {location}')


def _collision_ids(packet):
    """本次 packet 里本地算出的碰撞落点 id。空则返回空集（回落自由探索）。"""
    return {c['collision_id']
            for c in (packet.get('collision') or {}).get('candidates') or []}


def _check_collision_anchors(result, packet):
    """候选必须锚到**本地算出的**碰撞落点上（2026-10-02 机主裁决）。

    为什么这是硬约束：域4 实测里，`fusion` 臂 9/9 个方向标题与给定材料
    完全重合——因为提问方式是「你想出方向」，模型就照着材料复述。
    碰撞发现权收回本地之后，A/B 两个方向的 `direction_id` 必须是
    本次 packet 里的 `collision_id`。

    **orientation C 豁免**：第三类本就是「与 A、B 无关但可用」
    （机主明确要求它是一等公民），它没有母包落点可锚。

    **本地候选为空时一律放行**——那意味着「本层给不出落点」，
    必须回落到模型自由探索。把它变成拒绝就是扼杀探索。
    """
    collisions = (packet.get('collision') or {}).get('candidates') or []
    if not collisions:
        return
    valid = {c['collision_id'] for c in collisions}
    for cand in result['candidates']:
        if cand['orientation'] == 'C':
            continue
        if cand['direction_id'] not in valid:
            raise FusionError(
                f"Candidate {cand['orientation']} must anchor to a local collision "
                f"(direction_id), but {cand['direction_id']!r} is not one of: "
                f"{', '.join(sorted(valid))}. Direction C may use 'independent'.")


def _check_title_echo(result, packet):
    """标题复述 → **硬拒绝**（2026-10-02 机主裁决：复述分级处理的第一级）。

    为什么标题照抄不可能是有意的碰撞：标题本来就该是新的。
    实测 `fusion` 臂 9/9 是整段照抄，一刀切拒绝的误杀风险因此很低。

    判据刻意保守（完全相同或互为子串，长边 ≥ 8 字），因为这是**拒绝**条件——
    宁可漏判也不误杀。漏判的后果是「多收一个平庸候选」，
    误判的后果是「把好结果扔掉」。

    只比对**母包条目**与**碰撞落点描述**，不比对 `new_mechanism`——
    机制复述由 `spark` 标风险，那是第二级。
    """
    from .collide import echo_ratio

    materials = []
    for cap in (packet['capability'].get('A'), packet['capability'].get('B')):
        materials.extend(
            str(entry.get('text', ''))
            for entries in (cap.get('items') or {}).values()
            for entry in entries or []
        )
    for cand in packet.get('collision', {}).get('candidates', []):
        materials.append(str(cand.get('gap', '')))

    for cand in result['candidates']:
        for material in materials:
            hit = echo_ratio(cand['title'], material)
            if hit['echo']:
                raise FusionError(
                    f"Candidate title is an echo of the supplied material "
                    f"({hit['why']}); a collision must introduce a new title, "
                    f"not restate what was given")


def validate_result(result, packet):
    from .context import selected_paths, effective_gaps
    check_shape(result, RESULT_SCHEMA)
    files = {(label, f['path']): f for label, package in packet['packages'].items()
             for f in package['files'] if f['status'] == 'text' and f['path'] in selected_paths(packet, label)}

    def evidence(refs, expected_parent):
        for ref in refs:
            f = files.get((ref['parent'], ref['path']))
            if (ref['parent'] != expected_parent or f is None or f['sha256'] != ref['sha256']):
                raise FusionError('Evidence does not match the supplied parent/file fingerprint')
            lines = f['text'].splitlines()
            start, end = ref['start_line'], ref['end_line']
            if not 1 <= start <= end <= len(lines) or ref['quote'] not in '\n'.join(lines[start-1:end]):
                raise FusionError('Evidence line range or exact quotation is invalid')

    if {p['parent'] for p in result['profiles']} != {'A', 'B'}:
        raise FusionError('Exactly one profile per parent is required')
    for p in result['profiles']:
        if any(gap not in p['missing'] for gap in effective_gaps(packet)[p['parent']]):
            raise FusionError('Model omitted a user-audited source gap')
        if p['missing'] and p['coverage'] != 'partial':
            raise FusionError('A profile with missing material must be partial')
        for c in p['capabilities']:
            evidence(c['evidence'], p['parent'])
    if {c['orientation'] for c in result['candidates']} != {'A', 'B', 'C'}:
        raise FusionError('Exactly one A/B/C candidate is required; unsuitable directions may be rejected')
    _check_collision_anchors(result, packet)
    _check_title_echo(result, packet)
    suggested = {d['id']: d for d in packet['rule_directions']['suggestions']}
    reviews = {r['direction_id']: r for r in result['direction_reviews']}
    if len(reviews) != len(result['direction_reviews']) or set(reviews) != set(suggested):
        raise FusionError('Every supplied direction needs exactly one review; invented direction IDs are rejected')
    collision_ids = _collision_ids(packet)
    for c in result['candidates']:
        # **锚到本地碰撞落点的候选，只豁免「方向引用」这一项校验**——
        # 即下面 `hint is None` 那个分支。它豁免不了、也不该豁免
        # `blockers` / `gaps_to_fill` / `workflow` 这些**与方向无关**的判据：
        # 那些是候选自身的完整性，与它从哪儿来无关。
        #
        # 为什么必须分开：跨域的一对 `suggestions` **本来就是空的**
        # （旧层在文本里找共同词，域外一对按构造就产不出方向），
        # 于是「A/B 候选必须引用被采纳的方向」在跨域场景下**永远不可能满足**。
        # 以前是靠 `independent` 侥幸通过的——那正是 `independent` 两义的来源。
        # 现在碰撞落点取代了它的作用：落点由本地给，审不审得由本地说了算。
        anchored = c['direction_id'] in collision_ids
        if not anchored and c['direction_id'] != 'independent':
            hint = suggested.get(c['direction_id'])
            if hint is None or hint['orientation'] != c['orientation'] or reviews[c['direction_id']]['decision'] == 'reject':
                raise FusionError('Candidate must reference an adopted/adapted direction of its orientation or be independent')
            if hint['blockers'] and c['disposition'] == 'hypothesis':
                raise FusionError('A blocked source direction cannot become an unblocked candidate')
        evidence(c['contribution_a']['evidence'], 'A')
        evidence(c['contribution_b']['evidence'], 'B')
        # 认领「与 A、B 都无关但可用」就必须用**做法**交账：若两边仍按 capability 交账，
        # 那它本质上还是合并，不该占着第 3 类。这是把机主的口径真正落进结构里。
        if c['expected_outcome']['outcome_class'] == 'independent_usable' and {c['contribution_a']['kind'], c['contribution_b']['kind']} != {'way_of_working'}:
            raise FusionError('认领「与 A、B 都无关但可用」时，双方贡献都必须声明为 way_of_working（借做法而非借能力）；'
                              '否则它就是合并，应改用比 A 好 / 比 B 好 / 比双方都好')
        if c['blockers'] and c['disposition'] == 'hypothesis':
            raise FusionError('A candidate with blockers must be blocked or not_recommended')
        if c['disposition'] == 'blocked' and not c['blockers']:
            raise FusionError('Blocked candidates must explain blockers')
        # 第 5 类是「不可用、有缺失、需要 AI 补足」——不是失败，是一条待补的工序。
        # 认领了它就必须列出要补什么；反过来，判了 blocked 却说不需要补，也自相矛盾。
        claimed_fill = c['expected_outcome']['outcome_class'] == 'needs_ai_fill'
        if claimed_fill != bool(c['gaps_to_fill']):
            raise FusionError('needs_ai_fill must list gaps_to_fill, and only it may list them')
        if c['disposition'] == 'blocked' and not claimed_fill:
            raise FusionError('A blocked candidate must be declared as needs_ai_fill with gaps_to_fill')
        if c['disposition'] != 'not_recommended' and (not c['workflow'] or not c['artifacts']):
            raise FusionError('A proposed candidate must specify workflow and new artifacts')
        paths = set()
        for artifact in c['artifacts']:
            # 这里**不能**用 safe_path：那是「包内相对路径」校验器，
            # 而 artifacts[].path 指的是**新 Skill 要造出来的文件**，本来就不在包里。
            # 2026-10-01 真实 glm-4.7 采样抓到：模型写的
            # graded_degradation_report.py / soft_evidence_prover.py /
            # policy_compliance_checker.py 全部是**正确的产出处写法**，
            # 却被这套包内规则判成 `Unsafe package-relative path`——
            # **产品把两类路径混为一谈了，模型没有做错任何事。**
            path = proposed_path(artifact['path']).casefold()
            if path in paths:
                raise FusionError('Duplicate proposed artifact path')
            paths.add(path)
        if {a['removed_parent'] for a in c['ablation_checks']} != {'A', 'B'}:
            raise FusionError('Ablation plan must remove A and B separately')
        for test in c['evaluation']:
            if set(test['baselines']) != {'A_only', 'B_only', 'concatenation', 'fusion', 'model_only'}:
                raise FusionError('Evaluation must include all five baselines')
    return result
