"""**本地完整性校验器**——不看 `finish_reason`，只看内容。

    from app.fusion.completeness import classify
    report = classify(raw_text, skeleton(packet))

## 它为什么必须存在（2026-10-05 两份实测共同逼出来的）

**一、`finish_reason` 在 MiniMax-M3 上没有任何分辨力。**
官方枚举只有 `stop` / `length` / `content_filter` / `tool_calls`，
`stop` 的定义就是「自然结束」，**没有任何取值表达「输出不完整」**。
而我们 6 份失败样本的 `finish_reason` **全部是 `stop`**——
也就是说：**只看 `finish_reason`，失败样本和成功样本在这个字段上完全一样。**

**二、约束解码会把响亮的失败变成安静的失败。**
P0-C 的 A/B 实测（`evaluations/ab-carrier.txt`）：换成 function-call 承载后
`json_syntax` 从 13/16 归零，**但 `accepted` 仍是 0**——失败换成了
「JSON 合法但内容缺失」（`profiles` 只有 1 个、`candidates` 只有 1 个、
`direction_reviews` 整个没有）。以前靠语法错误计数就能看见模型崩了，
现在只能靠「必需项数量不够」才发现。

## 它**只分类，不拦截**

这是本模块唯一的硬约束：返回的 `verdict` 用来**解释失败**，
**绝不参与接受/拒绝的判定**。任何「因为它说不完整所以拒收」的用法都是错的——
那会让校验变松或变紧，而本项目的纪律是不许单方面改动校验强度。
`test_completeness.py` 有一条测试专门钉死这一点。

## 三条判据，全部只看内容

1. **能不能解析**——`unfence` 之后能否 `json.loads`。
2. **必需项够不够**——`profiles` / `candidates` / `direction_reviews`
   三个顶层数组的数量是否达到骨架钉死的值。
3. **断点在哪儿**——解析失败时，`len(text) - exc.pos` 是多少。
   这一条是**新增信息**所在：它把「写到文末没闭合」和
   「写到中途失步然后自己重新同步」分成两类，
   而这两种成因对应**不同的修法**（前者是长度，后者是结构漂移）。
"""
from __future__ import annotations

import json
from typing import Any, NamedTuple

#: 顶层必须与骨架数量一致的三个数组
SECTIONS = ('direction_reviews', 'profiles', 'candidates')

#: 判据 3 的阈值（字符）。超过它算「中段失步」，否则算「停在尾部」。
#: 依据：6 份真实失败样本的 from_end 分别是 12695 / 6974 / 2 / 3 / 25090 / 0，
#: 中间没有任何值落在 4～6974 之间，所以 2000 不是拍出来的数。
MID_DOC_TAIL = 2000

#: **不要**靠 json 的报错文案去分「截断」与「失步」——实测做不到：
#:   砍尾部 30 字符 → "Expecting ',' delimiter"  pos=末尾  from_end=0
#:   中段失步样例    → "Expecting ',' delimiter"  pos=近末尾 from_end=11
#:   另一中段样例    → "Expecting value"          pos=很靠前 from_end=6008
#: 同一句文案同时出现在尾部截断和中段失步上，**唯一能分开它们的是
#: `from_end`（断点后面还剩多少字符）**。这条来之不易，不要再退回按文案分类。
#: 唯一按文案分的一类：`Extra data`——那是「合法 JSON 后面跟了一截废话」，
#: 不是不完整，是另一回事。
TRAILING_JUNK_MSG = 'Extra data'


class Fingerprint(NamedTuple):
    """一次失败的**确定性**指纹——不调 LLM、不猜、不补写。

    字段：
    ``verdict``       判据类别（见 Verdict）
    ``seam_key``      断在哪个字段路径上（拿扫描器逐字符推进算出来，不是猜的）
    ``chars_total``   全文长度
    ``chars_to_seam`` 从文首到断点的距离
    ``chars_after``   断点后面还剩多少
    ``required``      骨架钉死的三个顶层数量
    ``got``           实际写出来的数量（只统计**已经能解析出来**的那些）
    """
    verdict: str
    seam_key: str | None
    chars_total: int
    chars_to_seam: int
    chars_after: int
    required: dict
    got: dict


#: 顶层容器键——用来在 key 路径里认数组下标
_SECTIONS = ('direction_reviews', 'profiles', 'candidates')


def fingerprint(raw: str, pre: dict, seam_key: str | None = None) -> Fingerprint:
    """在 `classify` 的结论之上，补上「断在哪个字段」和「差多少」。

    `seam_key` 传进来就用；没传就退化成 `None`——
    **宁可少一个字段，也不用猜出来的路径冒充真值。**
    """
    v = classify(raw, pre)
    text_len = len(_unfence(raw))
    to_seam = (text_len - v.from_end) if v.from_end is not None else text_len
    got = {s: 0 for s in _SECTIONS}
    obj = _maybe(_unfence(raw))
    if isinstance(obj, dict):
        for s in _SECTIONS:
            val = obj.get(s)
            if isinstance(val, list):
                got[s] = len(val)
    return Fingerprint(verdict=v.verdict, seam_key=seam_key,
                       chars_total=text_len, chars_to_seam=to_seam,
                       chars_after=v.from_end or 0,
                       required=required_counts(pre), got=got)


def _unfence(raw: str) -> str:
    from .engine import unfence
    return unfence(raw)


def _maybe(text: str):
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def summarize(fp: Fingerprint, per_field_target: int = 400) -> str:
    """把指纹压成一段**给人看的摘要**——将来注入提示词时用它。

    刻意**不含任何模型原文**：一是省上下文，二是避免把客户材料带进下一轮。
    """
    short = [s for s, need in fp.required.items() if fp.got.get(s, 0) < need]
    lines = ['上一次尝试中断了。']
    if fp.seam_key:
        lines.append('断点：在写 %s 的时候。' % fp.seam_key)
    lines.append('已写 %d 字符，断点后面还有 %d 字符。'
                 % (fp.chars_to_seam, fp.chars_after))
    if short:
        lines.append('三个必需项里 %s 数量不够。'
                     % '、'.join('%s(%d/%d)' % (s, fp.got.get(s, 0), fp.required[s])
                                 for s in short))
    lines.append('本次请优先补齐上面这些项，每个自由文本字段控制在 %d 字以内。'
                 % per_field_target)
    return '\n'.join(lines)


class Verdict(NamedTuple):
    """一次判定的结论。`from_end` 是**新增信息**所在，见模块文档判据 3。"""
    verdict: str
    parsed: bool
    reason: str
    from_end: int | None
    missing: tuple
    short: tuple

    def as_dict(self) -> dict[str, Any]:
        d = self._asdict()
        d['missing'] = list(self.missing)
        d['short'] = list(self.short)
        return d


def required_counts(pre: dict) -> dict:
    """从骨架里取三个顶层数组各自的应有数量。"""
    return {s: len(pre.get(s) or []) for s in SECTIONS}


def _section_status(obj, required: dict) -> tuple:
    missing, short = [], []
    for name, need in required.items():
        got = obj.get(name)
        if not isinstance(got, list):
            missing.append(name)
        elif need and len(got) < need:
            short.append('%s(%d<%d)' % (name, len(got), need))
    return tuple(missing), tuple(short)


def classify(raw: str, pre: dict) -> Verdict:
    """给一次模型输出一个**诊断标签**。

    `raw` 是未经任何修复的原始正文；`pre` 是 `skeleton(packet)`，提供应有数量。
    """
    from .engine import unfence
    text = unfence(raw)
    required = required_counts(pre)

    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        from_end = len(text) - (exc.pos or 0)
        if TRAILING_JUNK_MSG in exc.msg:
            # 合法 JSON 后面跟了一截废话——**不是不完整**，别混进 incomplete
            return Verdict('trailing_junk', False, exc.msg, from_end, (), ())
        if from_end > MID_DOC_TAIL:
            # 断点后面还有一大截——模型失步后自己重新同步了，**不是**没写完
            verdict = 'incomplete_mid_document'
        else:
            verdict = 'incomplete_at_tail'
        return Verdict(verdict=verdict, parsed=False, reason=exc.msg,
                       from_end=from_end, missing=(), short=())

    if not isinstance(obj, dict):
        return Verdict('malformed_other', True, '顶层不是对象', 0, (), ())

    missing, short = _section_status(obj, required)
    if missing:
        verdict = 'incomplete_missing_sections'
        reason = '缺少顶层数组 %s' % '、'.join(missing)
    elif short:
        verdict = 'incomplete_too_few_items'
        reason = '数量不够：%s' % '、'.join(short)
    else:
        verdict = 'complete'
        reason = '必需项数量齐全'
    return Verdict(verdict, True, reason, None, missing, short)
