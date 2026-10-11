"""本地碰撞候选生成：**把「碰撞在哪里发生」从模型手里收回本地**（2026-10-02 机主裁决）。

## 为什么要有这个模块

机主裁定的两件事在这里落地：

1. **碰撞发现权收回本地**——本地算法出候选，模型只做具体化。
2. **复述分级处理**——标题复述在 `contract` 层硬拒绝，机制复述在 `spark` 层标风险。

依据是四套对照实验的实测（2026-10-01，见 `CHANGELOG.md`）：

| 现象 | 证据 |
|---|---|
| 给定方向清单 → 模型整段照抄 | 域4 `fusion` 臂 9/9 方向标题与材料完全重合 |
| 五类认领与输入无关 | 两个差异很大的 goal，五类分布一字不差 |
| 现有核对器抓不住复述 | 上述 9/9 复述，`spark` 里**零** `new_mechanism_near_*` 风险 |
| 给「失效面」后模型交出真实跨包迁移 | `failures` 臂：把 A 的 `map_sink_to_perm` 用到 B 的 `serialization.load_pem_private_key` |

**「让模型想方向」这个提问方式本身在诱导抄写。** 所以提问方式要改：
本地算出落点，模型拿落点去做具体化。

## 与既有模块的分工

- `capability.extract()` 抽**话语结构**（8 个与域无关的轴）
- `capability.bridge()` 判**结构上有没有共同轴**
- `capability.bridge_failures()` 算**失效面**（A 在 B 的哪个环境下没有可援引规则）
- **本模块**把前两者的结果变成**可锚定的碰撞候选**，并提供三个原文比对原语
  与一个词汇层距离原语（`lexical_distance`）

## `candidates` 与 `unanchored` 的区别（2026-10-02 实测加的）

`candidates` 是**带抓手**的落点：每个候选的 `anchors` 都取自母包条目，
模型可以指着它做具体化，契约也要求 A/B 候选的 `direction_id` 指到其中一个。
**没有抓手的候选不进这个列表**，改放 `unanchored`——
两包越远、条目里越可能一个英文标识符都没有（实测跨域对 19% 的候选是零锚点），
把「本层算不出抓手」混进「可锚定落点」会让契约逼模型去锚一个空的东西。

## 硬约束（与 `capability.py` 同一条纪律）

- **不调模型、不联网、不加载包内代码**；纯函数、确定性、零外部状态。
- **不判断两个包该不该融合**——只如实列出「可对接的地方」与「没覆盖的地方」。
- **`shared_axes` 为空就不产出候选**：按构造就接不上，不假装接得上。

## 一个必须守住的边界

**本地候选为空时不得变成「不许模型试」。** 那是把「本层算不出」偷换成
「这条路不存在」。`collide()` 在无候选时返回空列表 + 说明原因的 `note`，
由调用方**回落到自由探索**。
"""
from __future__ import annotations

import re

#: 新颖性比率的**最小分母**。低于它，这个比率不可用——不是「偏低」也不是「偏高」，
#: 而是**测不出来**。数值是量出来的：2026-10-01 分组统计里，
#: 分母<8 的方向标准差 26.5%，分母>=8 的只有 15.2%（n=48）。
#: **改这个数必须同时给出新的分组统计来支撑**，否则守卫会红。
MIN_DENOMINATOR = 8

#: 碰撞候选的形状版本。形状变了就该重算，别让旧候选混进新结果。
#: v2（2026-10-02）：零锚点候选移出 `candidates`、另列 `unanchored`——
#: 2026-10-02 实测 36 对真实包，跨域对有 19% 的候选 `anchors` 为空，
#: 而 `candidates` 对外承诺的是「**可锚定**的落点」，契约还要求模型把
#: A/B 候选的 `direction_id` 指到其中一个。空锚点的候选既给不了抓手，
#: 又占着契约锚点的位置——必须与可锚定候选分开报，不能混在一起。
COLLISION_VERSION = 'local-collision-v2'

#: 词汇层距离的形状版本。`basis` 一旦换算法（条目 → 全文），版本必须跟着变。
DISTANCE_VERSION = 'lexical-distance-v1'

#: 「算近」的门槛：共有标识符占比 ≥ 它就算近。**这个数是量出来的**——
#: 2026-10-02 对仓库里 9 个包 36 对实测（按正文全部标识符算 Jaccard）：
#: 14 个跨域对落在 0.010–0.029，22 个同域对落在 0.113–0.864，
#: 门槛 0.05 落在两段之间，两侧余量都在 2 倍以上。
#: **改这个数必须同时给出新的分组统计来支撑**，否则守卫会红。
DISTANCE_BAND_THRESHOLD = 0.05

#: 回声判据的**归一化后**最小长度。低于它不算复述。
#: 归一化会剥掉空格与标点，所以门槛必须在归一化之后判（见 `echo_ratio`）。
ECHO_MIN_CHARS = 8

#: 回声判据的**最小汉字数**。只按字符数判会被英文标识符灌水——
#: `icacls 权限` 只有 2 个汉字，却占 8 个字符（见 `echo_ratio` 里的踩坑记录）。
ECHO_MIN_HAN = 5

_HAN = re.compile(r'[一-鿿]')

#: 退化路径里参与配对的轴：一方给硬性规则，另一方给动作/校验，
#: 这样「这条规则会在哪一步生效」才有意义。
_RULE_AXES = ('constraints',)
_TARGET_AXES = ('steps', 'checks', 'conditions', 'deliverables')

#: 归一化：只留下有区分度的字符，剥掉空白与标点。
_NOISE = re.compile(r'[^0-9A-Za-z一-鿿]')
_MARKER = re.compile(r'\b[A-Za-z_][A-Za-z0-9_]{2,}\b')

#: 英文停用词：到处都有的词当不了「具体锚点」。
_STOP_WORDS = {
    'the', 'and', 'for', 'with', 'this', 'that', 'from', 'then', 'than',
    'are', 'was', 'were', 'has', 'have', 'had', 'not', 'but', 'you',
    'all', 'any', 'can', 'will', 'would', 'should', 'its', 'our', 'use',
}


def markers(text: str) -> set:
    """文本里的**具体标识符**（函数名、字段名、配置键）。

    为什么不用相似度：2026-10-01 实测，字符二元组的 Jaccard 在长文本上完全失效——
    真实融合产物对母包的相似度只有 0.05，**比两个母包之间的 0.22 还低**，
    于是 `1 - 相似度` 恒等于 1，把**一句空话排在最「新颖」的位置**（0.998）。
    标识符没有这个问题：`load_or_create_key` 在不在母包里，是可以逐个查的事实。
    """
    return {m for m in _MARKER.findall(text or '') if m.lower() not in _STOP_WORDS}


def _norm(text: str) -> str:
    return _NOISE.sub('', text or '')


def echo_ratio(text: str, material: str) -> dict:
    """产物文本与**给定材料**是否构成回声（复述）。

    判据刻意保守：完全相同，或互为子串（长边 ≥ 8 字）。
    只认这两种——宁可漏判也不误判，因为这个判据会**硬拒绝**整份结果。

    2026-10-01 的教训：相似度版本会把空话排最前，已弃用；
    这里的「子串」判据在域4 `fusion` 臂上实测 9/9 命中。

    ## 「长边 ≥ 8」必须按**汉字数**判，不能按字符数

    第一版按归一化后的字符数判，于是 `'icacls 权限'`（2 个汉字 + 6 个 ASCII，
    共 8 字符）刚好越过门槛被判成回声——**一个英文标识符就把 5 个字的中文短语
    灌过了门槛**，8 字门槛形同虚设。
    现在要求：归一化后长度 ≥ `ECHO_MIN_CHARS` **且** 汉字数 ≥ `ECHO_MIN_HAN`。

    归一化会剥掉空格与标点，所以两个门槛都必须在归一化之后判。
    """
    own, other = _norm(text), _norm(material)
    if not own or not other:
        return {'echo': False, 'why': '有一侧是空的'}
    if own == other:
        return {'echo': True, 'why': '与材料完全相同'}
    for side in (own, other):
        if len(side) < ECHO_MIN_CHARS or len(_HAN.findall(side)) < ECHO_MIN_HAN:
            return {'echo': False, 'why': ''}
    if own in other or other in own:
        return {'echo': True, 'why': '与材料互为子串'}
    return {'echo': False, 'why': ''}


def provenance(mechanism: str, source_a: str, source_b: str) -> dict:
    """机制里的标识符，有多少是**母包没有**的、有多少是**母包带来的**。

    好的新机制两者都要有：全是母包的 = 照改，全是新的 = 空谈。
    分母不足时 `ratio` 给 `None`（**不可测**），**不是 0**——
    0 会被读成「测出来是零」，那是两件完全不同的事。

    落地率 `parent_ratio` **不受分母门槛限制**：它是「新造 / 母包」的比例，
    分母小的时候它仍然有信息（方向1 只有 6 个标识符，但 4 个来自母包）。
    """
    own = markers(mechanism)
    known = markers(source_a) | markers(source_b)
    if not own:
        return {'markers': 0, 'new': 0, 'ratio': None, 'measurable': False,
                'parent_ratio': None, 'from_parents': 0,
                'new_examples': [], 'note': '一个标识符都没有——这段话不可核对'}
    new = own - known
    from_parents = own & known
    measurable = len(own) >= MIN_DENOMINATOR
    return {
        'markers': len(own),
        'new': len(new),
        'ratio': round(len(new) / len(own), 3) if measurable else None,
        'measurable': measurable,
        'min_denominator': MIN_DENOMINATOR,
        # 落地率无条件报：分母小的时候它仍有信息。
        'from_parents': len(from_parents),
        'parent_ratio': round(len(from_parents) / len(own), 3),
        'new_examples': sorted(new)[:8],
        'note': '' if measurable else
                f'标识符只有 {len(own)} 个（门槛 {MIN_DENOMINATOR}）——新颖比率不可测，'
                f'但确有 {len(new)} 个新标识符、{len(from_parents)} 个来自母包'
                f'（绝对计数不受分母影响）',
    }


def claimed_source_verified(claim: str, source: str) -> dict:
    """模型**自述**「我用了母包的这一处」——它能不能在母包里核到。

    `new_mechanism` 文本实测**既不用母包标识符、也不用母包中文原句**（模型自己重写），
    所以从 `new_mechanism` 判落地会系统性低估。`from_a`/`from_b` 是模型明确声明的
    借用来源，虽然也是自述、**可能编造**，但**可核**。

    判据：自述里的中文片段（≥4 字）与标识符，有多少能在母包找到。
    分母取**片段自己的二元组**——不能用 Jaccard，母包几千字会把交集稀释掉。
    2026-10-01 实测：真实产出核验率 25%–100%，编造与泛泛声明都是 0。
    """
    source_grams = _grams(source or '')
    source_marks = markers(source or '')

    def covered(fragment: str) -> bool:
        grams = _grams(fragment)
        if not grams:
            return False
        return len(grams & source_grams) / len(grams) >= 0.7

    fragments = re.findall(r'[一-鿿]{4,}', claim or '')
    marks = markers(claim or '')
    hits = sum(1 for f in fragments if covered(f)) + len(marks & source_marks)
    total = len(fragments) + len(marks)
    if total == 0:
        return {'checkable': 0, 'hits': 0, 'ratio': None,
                'note': '自述里没有可核的具体指认——按不可采信处理'}
    return {'checkable': total, 'hits': hits, 'ratio': round(hits / total, 3), 'note': ''}


def _grams(text: str) -> set:
    """字符二元组。分母用片段自己的二元组数，才不会被长母包稀释。"""
    cleaned = re.sub(r'\s+', '', text or '')
    return {cleaned[i:i + 2] for i in range(len(cleaned) - 1)}


def lexical_distance(text_a: str, text_b: str) -> dict:
    """两包在**词汇层**离多远：它们共有多少个具体标识符。

    ## 为什么要有它（2026-10-02 实测）

    上游文献（arXiv:2603.19087）说「源-目标语义距离越大，双方产出的原创性越高」，
    而本项目四套对照实验（`evaluations/e3-five-arm-product.json` 等）**全部用同一对
    安全包**——语义距离这个变量**从来没有被操纵过**。要谈它，先得能把它量出来。

    量法只用本模块已有的 `markers()`：两份正文各抽一遍具体标识符，算 Jaccard。
    不用字符二元组相似度——那套原语在 2026-10-01 已经因为「长文本上完全失效」被弃用
    （见 `markers` 的 docstring），同一个坑不踩两次。

    ## 量在**正文全文**上，不是 `capability` 的条目上——这是实测逼出来的

    第一版按 `capability` 的条目算，结果**分不开远近**：跨域对 0.000–0.103，
    同域对 0.014–0.633，两段大面积交叠（写作包 `evidence-collector` 从条目里
    只抽到 **1 个**标识符，Jaccard 的分母小到没有意义）。
    换成两份正文的全部标识符之后，跨域 0.010–0.029、同域 0.113–0.864，
    门槛 `DISTANCE_BAND_THRESHOLD` = 0.05 落在中间的空白里。
    所以调用方必须传**整份正文**，不是条目。

    ## 它测不到什么（这一条不许含糊）

    - **它不是语义相似度**。两个包用完全不同的词描述同一件事，会被判成「远」。
      词表是 8 个轴之外的另一套坐标系，**没有引入任何新词表**——只数标识符。
    - **没有分母就不能报数**。两侧正文一个标识符都没有时 `similarity` 给 `None`
      （**不可测**），不是 0——0 会被读成「测出来是零」。
    - 它**不判断两个包该不该融合**，与本模块其余部分同一条纪律。
    """
    own_a, own_b = markers(text_a), markers(text_b)
    union = own_a | own_b
    out = {
        'version': DISTANCE_VERSION,
        'basis': 'package_body_markers',
        'markers_a': len(own_a),
        'markers_b': len(own_b),
        'shared_markers': len(own_a & own_b),
        'threshold': DISTANCE_BAND_THRESHOLD,
        'semantic_verified': False,
        'meaning': '两包共有具体标识符的占比：越小表示词汇上离得越远。'
                   '**这是词汇层的距离，不是语义理解**——换个说法描述同一件事会被读成「远」。'
                   '它不判断两个包该不该融合。',
    }
    if not union:
        out.update({'similarity': None, 'measurable': False, 'band': None,
                    'note': '两侧正文里一个标识符都没有——本层测不出距离，'
                            '这**不等于距离为零**，也不等于「近」。'})
        return out
    shared = own_a & own_b
    similarity = round(len(shared) / len(union), 4)
    out.update({
        'similarity': similarity,
        'measurable': True,
        'band': 'near' if similarity >= DISTANCE_BAND_THRESHOLD else 'far',
        'note': f'共有标识符 {len(shared)} 个，占并集 {len(union)} 个的 {similarity}'
                f'（门槛 {DISTANCE_BAND_THRESHOLD}）。**近不等于该融合，远也不等于不该融合。**',
    })
    return out


def _parent_texts(cap: dict) -> str:
    """把一个包抽出的所有条目拼成一段文本，用于原文比对。"""
    return '\n'.join(
        str(entry.get('text', ''))
        for entries in (cap.get('items') or {}).values()
        for entry in entries or []
    )


def _entry_markers(cap: dict) -> set:
    """一个包抽出的所有标识符——候选 anchors 必须落在这个集合里。"""
    return {m for entries in (cap.get('items') or {}).values()
            for entry in entries or [] for m in markers(str(entry.get('text', '')))}


def _first(items, axis):
    entries = (items or {}).get(axis) or []
    return entries[0] if entries else None


#: 退化路径里，引用另一侧原文时最多带多少字。
#: 要够长到能认出是哪一条，又不能长到把候选正文撑爆——`gap` 会被整份塞进提示词。
_QUOTE_CHARS = 48


def _quote_of(entry: dict) -> str:
    """把条目原文截成**可辨认**的一小段。

    只取开头是不够的：规则条目的开头常常是「### 4) …」这类小标题，
    截断后所有候选长得一模一样。取头尾拼一下更可能认得出是哪一条。
    """
    text = re.sub(r'\s+', ' ', str((entry or {}).get('text', ''))).strip()
    if len(text) <= _QUOTE_CHARS:
        return text
    return text[:_QUOTE_CHARS - 1] + '…'


def _degenerate_gap(axis_a, entry_a, axis_b, entry_b) -> str:
    """退化路径的 `gap`——**必须说出是哪一条规则 × 哪一个动作**。

    ## 为什么要改（2026-10-03 本地接地评估实测）

    原来写死成模板：

        A 的这条硬性规则在 B 的「{axis_b}」这一步上会怎样生效，B 现在没有对应约束

    「**这条**硬性规则」没说是哪一条。于是**两个不同的候选产出一模一样的 gap**：
    差异只藏在 `pair.a.text` 里，而那是结构字段、模型不会去比对。
    实测 21 对里 14 个候选属于「gap + anchors 完全相同、底层 pair 却不同」——
    **对模型来说它们就是同一个候选**，模型拿到手无从判断自己锚在哪条上。

    这不是去重问题：底层 pair 确实不同，丢掉就丢了真信号。
    修法是**把差异摆到模型看得见的地方**。

    同时把两侧的轴名都写出来：原来只写 `axis_b`，
    于是「同一条规则配四个不同轴」看起来像四条不同的碰撞，其实只差一个词。
    """
    return (f'A 的「{axis_a}」条目「{_quote_of(entry_a)}」'
            f'落在 B 的「{axis_b}」条目「{_quote_of(entry_b)}」上，'
            f'而 B 在这一步没有对应约束')


def _candidate(collision_id, side_a, side_b, gap, why, anchors, cap_a, cap_b):
    """组装一个候选，并**当场核对 anchors 确实出自母包**。

    anchors 是给模型的具体抓手，也是核对器的依据——**编出来的锚点会让整个
    候选层变成「看起来有依据的猜测」**。所以这里宁可少给，不给假的。

    ## 2026-10-02 抓到的一处真实漏洞

    第一版把 `bridge_failures` 的**场景词**（`Windows`）直接放进 anchors，
    但那条 `if platform.system() == "Windows":` 可能因为
    `max_items_per_axis=6` 截断而**根本没进 `items`**——
    于是 anchors 里有了一个母包条目里查不到的词。
    守卫 `test_every_anchor_can_be_found_in_a_parent` 当场把它抓了出来。

    修法不是放宽守卫，而是**让 anchors 严格来自 `items`**。
    场景词仍然出现在 `gap` 文字里（那是可读的解释），
    但**只有能被母包条目背书的词才配当抓手**。
    """
    allowed = _entry_markers(cap_a) | _entry_markers(cap_b)
    real = [a for a in anchors if a in allowed][:6]
    return {
        'collision_id': collision_id,
        'pair': {'a': side_a, 'b': side_b},
        'gap': gap,
        'why': why,
        'anchors': real,
        # 记下被丢掉的锚点：静默丢弃和「本来就没有」看起来一模一样。
        'anchors_dropped': [a for a in anchors if a not in allowed][:6],
    }


def collide(cap_a: dict, cap_b: dict) -> dict:
    """算出本地的碰撞候选。确定性、零模型调用。

    三步：
      1. **失效面优先**——`bridge_failures` 的 gap 是「A 在 B 的某环境下没有可援引
         规则」这一**可核事实**，比「哪里像」强得多，用它当候选骨架。
      2. **机制配对补齐**——失效面为空时（两包没有环境分支），退化为
         A 的硬性规则 × B 的动作/校验，按轴组合。
      3. **`candidates` 只放**带抓手**的候选**——见下面「零锚点」一节。

    ## 零锚点候选为什么单列（2026-10-02 实测）

    `candidates` 对外的承诺是「**可锚定**的落点」，契约
    （`contract._check_collision_anchors`）还要求模型的 A/B 候选把 `direction_id`
    指到其中一个。可 36 对真实包实测下来，**跨域对的候选有 19% 是零锚点**
    （同域对 3.8%）——两包离得越远，条目里越可能一个英文标识符都没有
    （全是中文散文），抓手就整个消失。

    零锚点候选不是「假」，它是「**本层算不出抓手**」。所以处置是**换位置**而不是删除：
    移到 `unanchored`，一条不丢，只是不再冒充可锚定落点、也不再占契约锚点的位置。
    丢掉才是把「本层算不出」偷换成「这条路不存在」。
    """
    bridge = bridge_of(cap_a, cap_b)
    shared = list(bridge.get('shared_axes') or [])
    failures = bridge_of_failures(cap_a, cap_b)

    out = {
        'version': COLLISION_VERSION,
        'candidates': [],
        'unanchored': [],
        'note': '',
        'semantic_verified': False,
        'meaning': '本地算出的可锚定落点。它不判断两个包该不该融合——'
                   '只如实说「这里有一个可核的接口」或「这里没算出来」。',
    }

    if not shared:
        out['note'] = ('两包在结构轴上没有任何共同项——本层给不出落点。'
                       '**这不是「不许模型试」**，调用方应回落到自由探索。')
        return out

    items_a = cap_a.get('items') or {}
    items_b = cap_b.get('items') or {}
    seq = 0
    produced = []

    # ── 第 1 步：失效面优先 ──
    for gap in failures.get('gaps') or []:
        side = gap.get('side')
        term = gap.get('scenario_term')
        # 抓手只取**规则侧**条目的标识符。场景侧（条件分支）常常因为
        # `max_items_per_axis` 截断而没进 `items`，取它的标识符会引进来路不明的词。
        rule = _first(items_a, 'constraints') or _first(items_b, 'constraints')
        seq += 1
        produced.append(_candidate(
            f'c{seq}',
            side_a={'role': 'rule_source', 'text': str((rule or {}).get('text', ''))[:160],
                    'evidence': (rule or {}).get('evidence')},
            side_b={'role': 'scenario', 'text': str((gap.get('from_side') or {}).get('text', ''))[:160],
                    'evidence': (gap.get('from_side') or {}).get('evidence')},
            gap=str(gap.get('why') or gap.get('gap') or f'{side} 的场景 {term} 未被覆盖'),
            why=('这是「A 在 B 的某环境下没有可援引规则」的结构事实，'
                 '不是「两处像」的推测——所以它可以当碰撞的落点。'),
            anchors=sorted(markers(str((rule or {}).get('text', ''))))[:6],
            cap_a=cap_a, cap_b=cap_b,
        ))

    # ── 第 2 步：机制配对补齐 ──
    #
    # **触发条件是「失效面一个抓手都没拿到」，不是「失效面一条都没有」**（2026-10-04 修）。
    #
    # 原来的判据是 `if not produced:`——只要失效面**产出了任何东西**，
    # 退化路径就整个被跳过。那在词表只认 `X == "ProperNoun"` 的年代看不出问题
    # （那时失效面几乎产不出东西）；词表放宽之后它立刻变成真回归：
    # `sandbox-design-discipline` 含 `macos`，于是它参与的 8 个对**全部**
    # 产出一个**没有抓手**的失效面候选 → 退化路径被跳过 →
    # 原本每对 6 个有抓手的候选变成 **0 个**。
    #
    # 那是拿一个查无实据的结果顶掉了六个可用的——正是本项目反复拒绝的
    # 「把『算不出』偷换成『这条路不存在』」的镜像版本。
    #
    # 修法：**按可用性排序，不按存在性排序。** 失效面仍是更强的路径，
    # 但它得先拿出抓手；拿不出就让位，而它自己**一条不丢**地留在 `unanchored`。
    if not any(c['anchors'] for c in produced):
        for axis_a in _RULE_AXES:
            for entry_a in (items_a.get(axis_a) or [])[:2]:
                for axis_b in _TARGET_AXES:
                    entry_b = _first(items_b, axis_b)
                    if not entry_b:
                        continue
                    seq += 1
                    produced.append(_candidate(
                        f'c{seq}',
                        side_a={'role': 'rule', 'text': str(entry_a.get('text', ''))[:160],
                                'evidence': entry_a.get('evidence')},
                        side_b={'role': 'action', 'text': str(entry_b.get('text', ''))[:160],
                                'evidence': entry_b.get('evidence')},
                        gap=_degenerate_gap(axis_a, entry_a, axis_b, entry_b),
                        why=('退化路径：两包没有环境分支可比，退化为「规则 × 动作」配对。'
                             '它比失效面弱——只说明有接口，不说明那里真的缺东西。'),
                        anchors=sorted(markers(str(entry_a.get('text', ''))
                                                + str(entry_b.get('text', ''))))[:6],
                        cap_a=cap_a, cap_b=cap_b,
                    ))

    out['candidates'] = [c for c in produced if c['anchors']]
    out['unanchored'] = [c for c in produced if not c['anchors']]
    if not out['candidates']:
        out['note'] = ('有共同结构轴，但本层没能给出**可核抓手**——'
                       '调用方应回落到自由探索。'
                       + (f'另有 {len(out["unanchored"])} 个落点算出来了但没有可核抓手，'
                          '已列在 unanchored 里。' if out['unanchored'] else ''))
        return out
    out['note'] = (f'基于 {len(shared)} 个共同结构轴（{"、".join(shared)}）'
                   f'给出 {len(out["candidates"])} 个可锚定落点。'
                   + (f'另有 {len(out["unanchored"])} 个落点没有可核抓手，'
                      '已单列在 unanchored，不作为契约锚点。'
                      if out['unanchored'] else ''))
    return out


def bridge_of(cap_a: dict, cap_b: dict) -> dict:
    from .capability import bridge
    return bridge(cap_a, cap_b)


def bridge_of_failures(cap_a: dict, cap_b: dict) -> dict:
    from .capability import bridge_failures
    return bridge_failures(cap_a, cap_b)
