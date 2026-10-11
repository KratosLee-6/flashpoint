#!/usr/bin/env python3
"""证据重锚定 —— 把 LLM 输出中的近似引文修回真实文件内容（36 号文 M2 管道层）。

背景（2026-09-25 E1 冒烟实测）：免费档 GLM 模型在融合方案的 evidence 引用上
有系统性转写误差：丢失 markdown 代码围栏（```python → python）、丢行首 **、
跨区块压缩、行号区间偏大。这些是**抄写保真问题**，不是证据捏造。

本模块的诚实边界：
  · 只在「模型给出的行号区间」内重锚，不扩大搜索范围；
  · 行级覆盖率 ≥0.95（quote 的归一化行能精确/近似落到真实区间）→ 判定转写误差；
  · 收紧优先：多个可行窗口取**最短**的连续子区间（证据区间不被模型放大）；
  · 全部失败 → 保留原样交由 contract.validate_result 拒绝（防幻觉校验零放宽）；
  · `min_similarity=0.90` 这个阈值**从未被标定过**，它当初只是一个猜测。
    2026-10-01 真实 `glm-4.7` 采样 6 次得到 2 次通过，失败样本里修不动的引文
    相似度落在 0.639–0.84，两次成功的运行则完全没有落在这一区间——
    **它就是当前卡点**。但**不得下调**：0.9 以上的引文是「抄错几个字」，
    0.6–0.8 的可能是「凭空编造」，而两者的正确处理相反。
    要下调必须先看清那些引文的内容（拒绝原因里已带 quote 摘要），
    确认是转写误差而非捏造。标定数据见 evaluations/e2-glm47-audit.json。
  · sha256 指纹一律以真实文件为准覆盖（模型不得自带指纹）。
"""
from __future__ import annotations

import difflib
import re

_FENCE = re.compile(r'^\s*`{3,}\w*\s*$')
_REF_KEYS = {'parent', 'path', 'start_line', 'end_line', 'quote'}

# 省略行：模型抄长代码块时会把中间内容缩成一行 `...` / `…` / `// ...`。
# 它不承载内容，比对时应当**整行丢弃**（而不是当成"抄错了这一行"）。
_ELISION = re.compile(r'^\s*(?:\.{3}|…|//\s*\.\.\.|/\*\s*\.\.\.\s*\*/)\s*$')
# 围栏残留：模型常把 ```` ```python ```` 抄成裸的 `python`。
# 真正的围栏行已被 _FENCE 剥掉，剩下这些**只有语言名**的行同样是排版噪声。
_FENCE_LANG_ONLY = re.compile(
    r'^\s*(?:python|javascript|typescript|js|ts|tsx|jsx|json|yaml|yml|bash|sh|shell|zsh|'
    r'powershell|ps1|sql|html|css|java|go|rust|ruby|php|text|txt|diff|ini|toml|xml)\s*$',
    re.IGNORECASE)


def _norm(s: str) -> str:
    """归一化：剥代码围栏行、剥围栏残留行、剥省略行、去空白差异。

    2026-10-01 真实采样（`evaluations/e2-glm47-audit.json`）把这三类的代价量化了：
    失败样本里修不动的引文，相似度落在 0.469–0.683，逐条打开看**全部指向真实内容**——
    两条是丢了 ```` ```python ```` 的围栏、两条是用 `...` 省略了中间行。
    **结论是转写假象，不是捏造证据。**

    但修法不是把相似度判据从 0.90 降下来——那是钝器，会把真正的凭空编造一起放进来
    （这正是 `ThresholdIsNotSilentlyTunableTests` 锁住的那件事）。
    这里做的是**定向去噪**：把三种已知的排版噪声整行丢掉，剩下的内容仍按原判据比对。
    捏造的引文不会因为剥掉这三类噪声就对上真实文件。
    """
    return '\n'.join(ln.strip() for ln in s.splitlines()
                     if ln.strip() and not _FENCE.match(ln)
                     and not _ELISION.match(ln) and not _FENCE_LANG_ONLY.match(ln))


def _walk_refs(obj):
    """深度产出所有形如 evidence 引用的 dict（键集合判定，与嵌套位置无关）。"""
    if isinstance(obj, dict):
        if _REF_KEYS <= set(obj.keys()):
            yield obj
        for v in obj.values():
            yield from _walk_refs(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_refs(v)


def _line_match_ratio(quote: str, seg: str, per_line: float = 0.85) -> float:
    """行级覆盖率：quote 的归一化行中，能在 seg 归一化行里精确或近似
    （相似度 ≥per_line）找到的占比。"""
    seg_lines = set(_norm(seg).splitlines())
    q_lines = [l for l in _norm(quote).splitlines() if l]
    if not q_lines:
        return 0.0
    hit = 0
    for l in q_lines:
        if l in seg_lines:
            hit += 1
            continue
        best = max((difflib.SequenceMatcher(None, l, s).ratio() for s in seg_lines),
                   default=0.0)
        if best >= per_line:
            hit += 1
    return hit / len(q_lines)


def _char_coverage(quote: str, seg: str) -> float:
    """字符覆盖率：quote 中能按序对齐进 seg 的字符占比（1.0 = 纯删减/错字级差异）。
    对短行删减型转写鲁棒（行级度量对短行太苛刻）；对长 quote 的结构性失真不敏感
    ——所以只作行级度量的补充通道。"""
    nq, ns = _norm(quote), _norm(seg)
    if not nq:
        return 0.0
    sm = difflib.SequenceMatcher(None, nq, ns)
    return sum(b.size for b in sm.get_matching_blocks()) / len(nq)


def strip_relocation_marks(result: dict) -> int:
    """把 `line_range_relocated` 这个**记账键**从引文条目上摘掉，返回摘掉了几处。

    ## 为什么必须摘（2026-10-04 抓到）

    `reanchor_evidence` 是**就地修复**，它给归位过的引文打上
    `line_range_relocated = True` 做留痕。于是出现了这个自伤：

        reanchor 归位成功 → 留下记账键
          → validate_result 的 no_extra_fields 拒绝「多出 line_range_relocated」
          → **每一次成功的归位，都保证我们自己拒绝这份结果**

    三个模型（`glm-4.7` / `glm-5.3` / `MiniMax M3`）的真实采样里都出现过
    `多出 ['line_range_relocated']` 这个拒绝理由——**那不是模型多写了字段，
    是本产品自己加的。**

    ## 为什么不改成「把记账键加进 schema」

    那样等于给 `no_extra_fields` 开口子：模型多写一个字段也会被当成合法。
    **契约必须继续要求 evidence 恰好那六个键。**
    记账信息归记账，形状归形状——摘掉才是对的。

    留痕并没有丢：`reanchor_evidence` 的返回值里有归位计数，
    而 `engine` 会把整个 reanchor 报告带进结果。
    """
    removed = 0
    for ref in _walk_refs(result):
        if 'line_range_relocated' in ref:
            del ref['line_range_relocated']
            removed += 1
    return removed


def reanchor_evidence(result: dict, files: dict,
                      min_coverage: float = 0.95,
                      min_similarity: float = 0.90) -> tuple[int, list]:
    """就地修复 result 中全部引用。files: {(parent, path): file_entry}。

    返回 (锚定数, 拒绝列表)。拒绝列表项 (parent, path, 原因)。
    """
    fixed, rejected = 0, []
    for ref in _walk_refs(result):
        f = files.get((ref.get('parent'), ref.get('path')))
        if f is None:
            continue                                   # 文件级错误由契约拒绝
        lines = f['text'].splitlines()
        s, e = ref['start_line'], ref['end_line']
        if not (1 <= s <= e <= len(lines)):
            rejected.append((ref['parent'], ref['path'], 'range_invalid'))
            continue
        if ref['quote'] in '\n'.join(lines[s-1:e]):
            if ref.get('sha256') != f['sha256']:
                ref['sha256'] = f['sha256']
            continue                                   # 本来就准
        # 收紧+区间覆盖：两条通道
        # ① 字符覆盖率对【模型给的完整区间】：quote 几乎全部按序落在区间内
        #    （删减型转写：跳行/丢围栏）→ 直接用真实区间文本覆盖 quote，行号
        #    保持模型给的（它指向的就是这个区间）。
        # ② 否则滑动窗口（行数==quote 行数）找最优子区间，行级平均相似度定档。
        seg_full = '\n'.join(lines[s-1:e])
        if _char_coverage(_norm(ref['quote']), _norm(seg_full)) >= min_coverage:
            ref['quote'] = seg_full
            ref['sha256'] = f['sha256']
            fixed += 1
            continue
        q_lines = [l for l in _norm(ref['quote']).splitlines() if l]
        qn = len(q_lines)
        best = (0.0, None)
        for a in range(s, e + 2 - qn):
            b = a + qn - 1
            sub = '\n'.join(lines[a-1:b])
            sub_lines = _norm(sub).splitlines()
            if not sub_lines:
                continue
            total = 0.0
            for l in q_lines:
                total += max((difflib.SequenceMatcher(None, l, s2).ratio()
                              for s2 in sub_lines), default=0.0)
            avg = total / len(q_lines)
            if avg > best[0]:
                best = (avg, (a, b, sub))
        if best[0] >= min_similarity:
            a, b, sub = best[1]
            ref['start_line'], ref['end_line'], ref['quote'] = a, b, sub
            ref['sha256'] = f['sha256']
            fixed += 1
            continue

        # ── 区间指错了：按内容到全文里找一次（机主 2026-10-01 授权）──────────
        #
        # 为什么需要：2026-10-01 逐条核实真实采样里修不动的引文，
        # 第 54/57/194/202 行**全部真实存在**，相似度却低到 0~0.7——
        # 因为模型给的**行号区间指向了错误的位置**，而本模块被设计成
        # 「只在模型声明的区间内收紧」，区间错了就修不动。
        #
        # 边界（这正是它与「放宽」的区别）：
        #   · 只在**声明区间内找不到**时才启用，不覆盖原有路径；
        #   · **必须唯一命中**才归位——同一段文字在文件里出现多处时**不归位**，
        #     归到哪一处都是猜，宁可让契约拒绝；
        #   · 判据与区间内完全一致（同一个 min_similarity），不为归位降低标准；
        #   · 捏造的引文在全文里也找不到，照旧被拒；「真行 + 假注释」凑不成
        #     真实窗口，同样被拒。**这三类防线都不因归位而放松。**
        hit = _unique_whole_file(lines, ref['quote'], min_similarity)
        if hit:
            a, b, sub = hit
            ref['start_line'], ref['end_line'], ref['quote'] = a, b, sub
            ref['sha256'] = f['sha256']
            ref['line_range_relocated'] = True   # 留痕：这次行号是重新定位的
            fixed += 1
            continue

        if True:
            # 把引文摘要带上：只有相似度无法判断这是「抄错几个字」还是「凭空编造」，
            # 而这两者的处理完全相反（前者可降阈值修复，后者降阈值就是给幻觉开门）。
            # 截断到 120 字符，只为诊断，不为回显。
            rejected.append((ref['parent'], ref['path'],
                             f'avg={round(best[0], 3)} quote={ref["quote"][:120]!r}'))
    return fixed, rejected

def _unique_whole_file(lines, quote, min_similarity):
    """在全文里找与 quote 足够相似的窗口，**只在唯一命中时返回**。

    同一段文字在文件里出现多处就返回 None——那说明它不是唯一的，
    归到哪一处都是猜。宁可让契约拒绝。
    """
    q_lines = [l for l in _norm(quote).splitlines() if l]
    if not q_lines:
        return None
    span = len(q_lines)
    hits = []
    for a in range(1, max(1, len(lines) - span + 2)):
        b = a + span - 1
        sub_lines = _norm('\n'.join(lines[a - 1:b])).splitlines()
        if not sub_lines:
            continue
        total = sum(max((difflib.SequenceMatcher(None, l, s).ratio() for s in sub_lines),
                        default=0.0) for l in q_lines)
        if total / len(q_lines) >= min_similarity:
            hits.append((a, b, '\n'.join(lines[a - 1:b])))
            if len(hits) > 1:
                return None          # 不唯一 → 不归位
    return hits[0] if hits else None
