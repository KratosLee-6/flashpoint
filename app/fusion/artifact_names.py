"""产出文件名的扩展名推断（机主 2026-10-01 授权，2026-10-01 实施）。

**背景（全部来自真实采样的原始输出）**：模型会写出 `audit_report_schema.` 这种
「点后面什么都没写」的名字。八批采样里它占 5/6，是最大阻塞。
同一份 content 显示输出**没有被截断**（finish_reason=stop、6776/8192 token、
reasoning_tokens 0、JSON 解析成功、截断点之后结构完整）——是模型主动写错。

**为什么需要推断**：模型没写扩展名，补成 .py / .json / .md **都没有直接依据**。
机主授权用 `purpose` 里的措辞来推断，因此本模块必须守住三条：

1. **只从明确的白名单关键词推断**，不做任何"看着像"的判断；
2. **推断结果必须留痕**：调用方要能说清「哪几个是推断出来的、依据是什么」；
3. **推断不出来就保持原样**，让契约照常拒绝——**不猜**。

这不是归位（归位的前提是"真值存在于某处"），是**有据可依的推断**，
所以推断出来的东西在结果与界面上都必须标成推断，不能混进模型原话里。
"""
from __future__ import annotations

import re

# 白名单：只认这些词，且必须**明确命中**才推断。宁可漏判，不可乱猜。
# 顺序有意义：先具体后宽泛。
_RULES: tuple[tuple[str, str, str], ...] = (
    # 「定义…标准 / 规则 / 结构 / 映射 / 指标」——这一组来自**同一次真实调用里的对照组**：
    #   写对的：purpose 含「实现」→ .py；含「检查清单」→ .md
    #   写断的：purpose 含「定义…分类标准」「定义…分类规则」「定义评估报告的结构」
    # 模型对「实现」「清单」有清晰映射，唯独对「定义类」没把握。
    # 注意：**不是「名字里带 schema」**——`evidence_classification_rules.` 名字里没有
    # schema 也照样断了。判据只能来自 purpose。
    (r'定义|结构|标准|规则|映射|指标|schema|数据格式', '.json',
     'purpose 在定义一份结构 / 标准 / 规则 / 映射（模型对此类没把握，同批的「实现」「清单」都写对了）'),
    (r'核心逻辑|脚本|命令行|工具实现|checker|检查器|验证器|scanner|扫描器|引擎|engine',
     '.py', 'purpose 提到逻辑 / 脚本 / 检查器 / 扫描器'),
    (r'报告|清单|检查表|说明|指南|文档|模板|checklist|readme|规范|流程',
     '.md', 'purpose 提到报告 / 清单 / 文档 / 模板'),
    (r'纯文本|文本文件', '.txt', 'purpose 提到纯文本'),
)

_TRUNCATED_TAIL = re.compile(r'\.(?=$|[/\s]|$)')


def infer_extension(path: str, purpose: str) -> dict | None:
    """给「点后面什么都没写」的产出文件名推断扩展名。

    返回 `{'extension': '.py', 'basis': '...'}`，或 **None**（表示不推断）。

    **不推断的情形**（原样返回 None，让契约照常拒绝）：
      · 路径本来就带扩展名 —— 不碰模型的原话
      · `purpose` 为空或没命中白名单 —— 没有依据就不猜
      · 路径本身有安全问题 —— 那是 `proposed_path` 的事，不在这里越权「修好」
    """
    if not isinstance(path, str) or not isinstance(purpose, str):
        return None
    name = path.strip()
    if not name or not _TRUNCATED_TAIL.search(name):
        return None
    stem = _TRUNCATED_TAIL.sub('', name)
    if not stem or '.' in stem.rsplit('/', 1)[-1]:
        return None                       # 已经有别的点，别动
    for pattern, extension, basis in _RULES:
        if re.search(pattern, purpose, re.IGNORECASE):
            return {'extension': extension, 'basis': basis,
                    'from': f'{name}', 'to': f'{stem}{extension}'}
    return None


def infer_artifacts(result: dict) -> dict:
    """就地补全候选里的 artifacts 路径，并返回可留痕的记录。

    返回：`{'applied': bool, 'inferred': [...], 'unresolved': [...]}`。
    `unresolved` 是**猜不出来**的路径——它们会被契约照常拒绝，这里如实记下来。
    """
    inferred, unresolved = [], []
    for candidate in result.get('candidates', []) or []:
        for artifact in candidate.get('artifacts', []) or []:
            path, purpose = artifact.get('path', ''), artifact.get('purpose', '')
            guess = infer_extension(path, purpose)
            if guess:
                # **只改 path，绝不在产物上多加字段。**
                # 契约的 artifacts 是 additionalProperties=False，多一个
                # `inferred_extension` 就会被 check_shape 拒掉——2026-10-01 亲历：
                # 九批采样 0/6，全部死于这一行自己加的痕迹字段。
                # 痕迹一律放在结果外层（artifact_names），不污染模型原话所在的对象。
                artifact['path'] = guess['to']
                inferred.append({**guess, 'candidate': candidate.get('title', '')})
            elif isinstance(path, str) and _TRUNCATED_TAIL.search(path):
                unresolved.append(path)
    return {'applied': bool(inferred), 'inferred': inferred, 'unresolved': unresolved}
