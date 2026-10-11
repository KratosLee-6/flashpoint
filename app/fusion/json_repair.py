"""严格 JSON 的两处语法瑕疵：**能唯一确定含义的才修，猜的一律不修。**

    from app.fusion.json_repair import repair
    ok, text, changes = repair(raw)

## 为什么需要它（2026-10-03 实测）

MiniMax M3 在 `thinking: disabled` 下跑完整流程六次，**四次栽在严格 JSON 的语法上**：

    Illegal trailing comma before end of object  (char 29486)
    Expecting ',' delimiter                       (char 8843)

前夜已经修掉了 markdown 围栏（`engine.unfence`），那之后 JSON 语法类
**仍占 4/6**——是当前最高频的单一失败原因，比形状类高一个量级。

## 本模块的立场：**修 ≠ 猜**

「容错」这个词很容易滑向「尽量把它弄成能解析的样子」。那正是本项目反复拒绝的做法——
`parse_json` 的报错文案写着 "no fallback draft"，重复键检查也还在，
两处都是在拒绝「从一堆文本里猜哪段是答案」。

所以这里只做**两种改法**，且各自都要能证明「只有一种可能含义」：

| 瑕疵 | 改法 | 为什么不算猜 |
|---|---|---|
| **尾随逗号** `{"a":1,}` | 删掉那个 `,` | 严格 JSON 里它非法，所以原文**只能**表示 `{"a":1}`。没有第二种读法。 |
| **对象成员间缺逗号** `{"a":1 "b":2}` | 补一个 `,` | 下一个 token 是 `字符串 + 冒号`，那**只能**是下一个成员的键。`{"a":1 "b":2}` 没有任何别的合法读法。 |

**明确不做的**：

- ❌ **数组元素间缺逗号**：`[1 2]` 补成 `[1,2]` 是一种读法，
  但 `[12]` 写成 `[1 2]` 也是一种。**有两种读法就是猜，不修。**
- ❌ 单引号、注释、未加引号的键、尾随分号……
- ❌ 任何「截断后补齐闭合符号」

遇到不修的情况就**如实报错**，把原文里的位置交出去——
本项目已经因为「报错文案把三种原因混成一句话」吃过亏
（`Invalid or ambiguous JSON` 同时对应围栏、尾随逗号、缺逗号）。

## 关键实现约束：**字符串边界**

整个修复建立在**词法扫描**上，而不是正则替换。字符串字面量被当作**一个原子 token**，
所以下面这段里的 `,` `}` 一个都不会被动：

```json
{"quote": "示例代码：\\n\\`\\`\\`json\\n{\\\"a\\": 1,}\\n\\`\\`\\`"}
```

这是「完美解决」里最要紧的一条：**证据引文完全可能包含 markdown 围栏和 JSON 片段**
（`test_skeleton.py` 里就有这么一条守卫），正则替换会把它们改坏。

## 改了什么必须留下痕迹

`repair()` 返回 `changes`，调用方必须把它带进结果里。
**静默修好等于放宽校验**——那正是本项目在装配层刚犯过一次的错
（把 `baselines` 填回去，结果一条契约检查变成了永远不会失败的空断言）。
"""
from __future__ import annotations

import json

from .packages import FusionError

_WS = ' \t\n\r'


def _tokenize(text: str):
    """把 JSON 文本切成 `(起点, kind, text)` 序列。

    `kind` ∈ `ws` / `str` / `punct` / `lit`，**起点是字符偏移**——
    改痕报告的 `at` 用它，复核时才能在原文里直接定位。
    （2026-10-04：第一版返回的是 token 序号，于是真实样本上
      「改了几处」全对、「改在哪」全错——而那正是唯一有用的那部分。）

    **字符串是原子 token**——这是整个模块的安全基础。
    """
    out = []
    i, n = 0, len(text)
    while i < n:
        start = i
        ch = text[i]
        if ch in _WS:
            j = i
            while j < n and text[j] in _WS:
                j += 1
            out.append((start, 'ws', text[i:j]))
            i = j
        elif ch == '"':
            j = i + 1
            closed = False
            while j < n:
                if text[j] == '\\':
                    j += 2
                    continue
                if text[j] == '"':
                    j += 1
                    closed = True
                    break
                j += 1
            if not closed:
                # **没有闭合引号**：不是瑕疵，是根本性的坏文本。标记出来交给上层。
                out.append((start, 'lit', text[i:]))
                return out
            out.append((start, 'str', text[i:j]))
            i = j
        elif ch in '{}[],:':
            out.append((start, 'punct', ch))
            i += 1
        else:
            j = i
            while j < n and text[j] not in _WS and text[j] not in '{}[],:"':
                j += 1
            out.append((start, 'lit', text[i:j]))
            i = j
    return out


def _meaningful(tokens):
    """`(下标, 起点, token)` 列表，跳过空白——修复只看结构，不看排版。"""
    return [(i, start, (kind, value))
            for i, (start, kind, value) in enumerate(tokens) if kind != 'ws']


def _is_value_end(tok) -> bool:
    return tok[0] in ('str', 'lit') or tok in (('punct', '}'), ('punct', ']'))


def _is_key(token, tokens, at) -> bool:
    """这个字符串 token 是不是**对象的键**——判据是它后面紧跟冒号。

    `tokens` 的元素是 `(起点, kind, text)`，所以 kind 在 `[1]`。
    """
    if token[0] != 'str':
        return False
    for k in range(at + 1, len(tokens)):
        if tokens[k][1] == 'ws':
            continue
        return tokens[k][1] == 'punct' and tokens[k][2] == ':'
    return False


def repair(text: str):
    """返回 `(是否可解析, 修好的文本, 改了什么)`。

    已经是合法 JSON 时**原样返回**，`changes` 为空——不做任何无意义的重写。
    修不好时返回 `(False, 原文, changes)`，由调用方决定怎么报错。
    """
    if not isinstance(text, str):
        return False, text, []
    try:
        json.loads(text)
        return True, text, []
    except (ValueError, RecursionError):
        pass

    tokens = _tokenize(text)
    keep = [True] * len(tokens)
    inserts = {}          # token 下标 → 要在它之前插入的文本
    changes = []

    sig = _meaningful(tokens)
    n = len(sig)
    for k in range(n):
        idx, start, tok = sig[k]
        nxt = sig[k + 1] if k + 1 < n else None

        # ── 删尾随逗号：`...,` 后面紧跟 `}` 或 `]` ──
        # 注意 `nxt` 是 `(下标, 起点, token)`，它的 token 在 `[2]`。
        # （2026-10-03：这里第一次写成 `nxt[1] in ('}', ']')`——拿 token 元组和字符串比，
        #   恒为假，于是尾随逗号一条也删不掉，而**测试是绿的**——
        #   因为那时候还没写测试，只有合成样本，别人的测试才是真的。）
        if tok == ('punct', ',') and nxt is not None and nxt[2][1] in ('}', ']'):
            keep[idx] = False
            changes.append({'at': start, 'kind': 'trailing_comma', 'char': ','})

        # ── 补对象成员间的缺逗号：`"a":1` 后面直接跟 `"b":` ──
        # **只在这一种情形下补。** 数组里的 `[1 2]` 有两种读法，那是猜，不补。
        if nxt is not None and keep[idx] and _is_value_end(tok):
            if tok[1] not in (',', ':') and not (tok[0] == 'punct' and tok[1] in '{['):
                if _is_key(nxt[2], tokens, nxt[0]):
                    inserts[nxt[0]] = ','
                    changes.append({'at': nxt[1], 'kind': 'missing_comma', 'char': ','})

    if not changes:
        return False, text, []

    pieces = []
    for i, (_start, kind, value) in enumerate(tokens):
        if i in inserts:
            pieces.append(inserts[i])
        if keep[i]:
            pieces.append(value)
    fixed = ''.join(pieces)

    try:
        json.loads(fixed)
    except (ValueError, RecursionError) as exc:
        # 修完还是不行 → 说明它不属于本模块能唯一判定的两类。
        # **不硬来**：把原文和真因交回去。
        return False, text, []
    return True, fixed, changes


def _why_unrepairable(text: str) -> str:
    """把 **json 自己的报错**带出来——**只带类型、位置、消息，不带任何内容**。

    ## 为什么必须带（2026-10-04 实测）

    M3 第四轮的失败现场是：

        "Windows 分支条件（platform.system() == \\"Windows"）—— 作为 AST 场景",

    模型把 `"Windows"` 的**开引号转义了、闭引号忘了转义**，字符串提前结束。
    而 `json` 报的是 **`Expecting ',' delimiter`**——**完全误导**：
    真实原因是引号，不是逗号。当时正是「只看这一句报错」把排查带偏的。

    ## 边界：绝不回显内容

    供应商响应体**不整体回显**（可能含提示词或凭据）。
    `json.JSONDecodeError` 的 `msg` 与 `pos` **本身不含内容**，
    这里也只取这两个字段，**不碰 `exc.doc`**——即使某天 json 的报错文案变了，
    也不会顺手把正文带出去。
    """
    try:
        json.loads(text)
        return ''                                  # 竟然能解析？那是别的问题，不加这句
    except json.JSONDecodeError as exc:
        # **只取 msg / lineno / colno / pos**，不取 doc（那是原文）。
        return (' json 自己的判定：%s（第 %d 行第 %d 列，字符 %d）。'
                % (exc.msg, exc.lineno, exc.colno, exc.pos))
    except (ValueError, RecursionError) as exc:
        return ' json 解析器报的是 %s' % type(exc).__name__


def parse_lenient(text: str, reject_duplicates=True, reject_constant=True):
    """`parse_json` 的容错版：能修就修，修完的**改痕必须由调用方留下**。

    返回 `(对象, changes)`。`changes` 非空就意味着**这个结果不是模型原样写出来的**，
    必须让它出现在结果里——否则就是静悄悄放宽了校验。
    """
    ok, fixed, changes = repair(text)
    if not ok:
        raise FusionError(
            'Invalid or ambiguous JSON, and the defect is not one this product repairs '
            '(only trailing commas and missing commas between object members are repaired; '
            'guesses are refused). See changes for what was tried, if any.'
            + _why_unrepairable(text))
    if reject_duplicates or reject_constant:
        def _hook(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('Duplicate JSON key: %r' % key)
                result[key] = value
            return result

        def _const(value):
            raise ValueError('Non-finite JSON number: %r' % value)

        kwargs = {}
        if reject_duplicates:
            kwargs['object_pairs_hook'] = _hook
        if reject_constant:
            kwargs['parse_constant'] = _const
        try:
            return json.loads(fixed, **kwargs), changes
        except (ValueError, RecursionError) as exc:
            raise FusionError(
                'JSON repaired but then rejected by the strict checks: %s' % exc) from None
    return json.loads(fixed), changes
