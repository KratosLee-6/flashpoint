"""厂商目录与协议适配层：**接哪几家 AI 都能用同一套调用**。

## 为什么要这一层（2026-10-02 机主裁定：全套）

现状只认 OpenAI 兼容：`Authorization: Bearer` + `choices[0].message.content`
+ 端点必须以 `/chat/completions` 结尾。这挡住了 **Anthropic 与 Gemini**——
它们的请求体、鉴权头、响应结构、结束原因字段**全都不一样**。

| 协议 | 端点 | 鉴权 | 响应取文本 | 结束原因 |
|---|---|---|---|---|
| `openai` | `…/v1/chat/completions` | `Authorization: Bearer` | `choices[0].message.content` | `choices[0].finish_reason` |
| `anthropic` | `…/v1/messages` | `x-api-key` + `anthropic-version` | `content[]` 里 type=text 的 `text` 拼接 | `stop_reason` |
| `gemini` | `…/models/{model}:generateContent` | `x-goog-api-key`（或 `?key=`） | `candidates[0].content.parts[].text` 拼接 | `candidates[0].finishReason` |

## 三条硬纪律

1. **目录是纯本地常量**，不联网、不调 API、不含任何凭据。
   它只是「端点长什么样、参数有什么坑」的说明书。
2. **不整体回显供应商响应体**——只白名单提取可诊断的 `error.message`
   （沿用 `engine.provider_error` 的脱敏规则），凭据与提示词都不从错误通道漏出去。
3. **不自动重试、不自动降级到另一家**。失败就把供应商的原话告诉用户，
   由人决定换谁。产品纪律：连接检查必须先取得明确同意。

## 为什么要「目录」而不只是「自由填 URL」

普通用户不知道 DeepSeek 的端点是 `https://api.deepseek.com/v1/chat/completions`、
也不知道豆包的模型名带 `ep-` 前缀、更不知道某些家**会拒绝 `response_format`**。
目录把这些写成常量；但**保留「其他」自由填**，所以目录漏了新厂商也不会把用户挡在外面。

**目录里每条 `caveats` 都是实测或官方文档明说的**，不是猜的——写在界面上，
免得用户踩了坑不知道为什么。
"""
from __future__ import annotations

import re

# ── 协议定义 ────────────────────────────────────────────────
# 只列**请求构造与响应解析真正不同**的部分；相同的（都要 JSON、都要 HTTPS 或 loopback）
# 留在 engine 的既有纪律里，不在这里重复实现。

OPENAI = 'openai'
ANTHROPIC = 'anthropic'
GEMINI = 'gemini'
PROTOCOLS = (OPENAI, ANTHROPIC, GEMINI)

#: 各家的「已知参数差异」。**不自动应用**——只作为 caveat 展示给用户，
#: 因为「自动改参数」等于我们替供应商做决定，而我们没有它们的最新文档。
CAVEATS = {
    'json_mode_unsupported': '这家不支持 `response_format: json_object`，'
                             '请在「推理设置」里关掉结构化输出',
    'thinking_unsupported': '这家不认 `thinking` 参数，传了会 400',
    'always_thinking': '这家是始终思考型模型，不能关推理；只能选强度（low/high/max）',
    'key_optional': '本地模型可以不填 key',
    'model_prefix': '模型名需要带厂商前缀（见下方 models）',
    # 2026-10-02 实测到的真实差异，不是猜的：
    'max_completion_tokens': '这家**已弃用 `max_tokens`**，要发 '
                             '`max_completion_tokens`（本产品已按协议自动改名）；'
                             '它推荐的长度也比别家大一倍',
    'thinking_adaptive': '默认开 adaptive thinking，响应里可能带 `<think>` 块；'
                         '要拆到 reasoning_content 字段请开 reasoning_split',
    # 2026-10-02 实测出来的**操作前提**，不是文档抄的：
    'thinking_must_disable': '**请把「推理设置」选成「关闭」**。默认的自适应思考会把'
                             '输出预算吃光——同一个完整任务，实测关闭后 181 秒、'
                             'finish=stop、写出 12,487 token；不关则 16,384 的预算'
                             '全部被 `<think>` 文本用尽、finish=length（六次全截断）。'
                             '注意它的 `reasoning_tokens` 始终是 0——思考不单独计费，'
                             '所以**用量读数上看不出来**，只能看 finish_reason',
}


def _c(*ids):
    return [CAVEATS[i] for i in ids]


# ── 厂商目录（纯本地常量，无凭据）──────────────────────────
#
# `endpoint` 允许用 `{model}` 占位（Gemini 把模型名放在路径里）。
# `models` 是**建议值**，不是穷举——用户总能自由填别的。
# `protocol` 决定请求怎么发、响应怎么读。
# `key_required: False` 只给本地运行时（Ollama / vLLM / LM Studio）。

CATALOG = [
    # ── 国内主力（OpenAI 兼容，零适配成本）──
    {'id': 'deepseek', 'label': 'DeepSeek（深度求索）', 'protocol': OPENAI,
     'endpoint': 'https://api.deepseek.com/v1/chat/completions',
     'models': ['deepseek-chat', 'deepseek-reasoner'],
     'caveats': [], 'docs': 'https://platform.deepseek.com/api-docs/'},
    {'id': 'zhipu', 'label': '智谱 GLM（通用 API）', 'protocol': OPENAI,
     'endpoint': 'https://open.bigmodel.cn/api/paas/v4/chat/completions',
     'models': ['glm-4.7', 'glm-4.5-air', 'glm-5.3'],
     'caveats': _c('always_thinking'), 'docs': 'https://open.bigmodel.cn/dev/api'},
    # 2026-10-02 实测：买了 GLM Coding Plan 却用通用端点，**报的是「余额不足」**
    # （HTTP 429），而账号余额其实没扣错——是**端点用错了**。
    # 智谱 FAQ 原文：「为什么购买了编码套餐还报错 1113 余额不足？——必须配置特定 baseurl」。
    # 换到 `/api/coding/paas/v4` 后同一个 key 立刻连通，thinking=low / high 都通过。
    # **这条错误信息具有极强的误导性**，所以单独列一条目录项而不是塞进 note。
    {'id': 'zhipu-coding', 'label': '智谱 GLM（Coding Plan 套餐）', 'protocol': OPENAI,
     'endpoint': 'https://open.bigmodel.cn/api/coding/paas/v4/chat/completions',
     'models': ['glm-5.3', 'glm-5-Turbo', 'glm-4.7'],
     'caveats': _c('always_thinking'),
     'docs': 'https://docs.bigmodel.cn/cn/coding-plan/quick-start',
     'note': '**买了 Coding Plan 就必须用这个端点**。用上面的通用端点会报'
             '「余额不足/无可用资源包」——那不是余额问题，是端点不对，'
             '同时把套餐扣成按量计费。套餐额度只认这个路径。'},
    {'id': 'moonshot', 'label': 'Kimi / 月之暗面', 'protocol': OPENAI,
     'endpoint': 'https://api.moonshot.cn/v1/chat/completions',
     'models': ['moonshot-v1-8k', 'kimi-k2-0905-preview'],
     'caveats': [], 'docs': 'https://platform.moonshot.cn/docs/api/chat'},
    {'id': 'qwen', 'label': '通义千问（阿里云 DashScope 兼容模式）', 'protocol': OPENAI,
     'endpoint': 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',
     'models': ['qwen-plus', 'qwen-max', 'qwen-turbo'],
     'caveats': [], 'docs': 'https://help.aliyun.com/zh/dashscope/'},
    {'id': 'doubao', 'label': '豆包（火山方舟）', 'protocol': OPENAI,
     'endpoint': 'https://ark.cn-beijing.volces.com/api/v3/chat/completions',
     'models': ['ep-20250101-placeholder'],
     'caveats': _c('model_prefix'),
     'docs': 'https://www.volcengine.com/docs/82379',
     'note': '豆包的模型名是**你自己在控制台创建的推理接入点 ID**（形如 ep-…），'
             '不是模型代号——请从控制台复制填进来。'},
    # 2026-10-02 查官方文档后重写。**原来这条是错的**：
    #   旧：api.minimax.chat/v1/text/chatcompletion_v2 + 模型 abab6.5s-chat
    #       ——该端点已被官方标记 deprecated，模型名也过时。
    #   新：api.minimax.io/v1/chat/completions（国际站）/
    #       api.minimax.cn/v1/chat/completions（国内站），模型 MiniMax-M3。
    # 关键坑：**`max_tokens` 在 M3 上已弃用，要用 `max_completion_tokens`**，
    # 推荐 131072、上限 524288——**比我们给别家的上限大一倍**。
    # 见 `max_tokens_field()`：按协议改名，否则请求会被拒或被静默忽略。
    {'id': 'minimax', 'label': 'MiniMax（国际站 api.minimax.io）', 'protocol': OPENAI,
     'endpoint': 'https://api.minimax.io/v1/chat/completions',
     'models': ['MiniMax-M3', 'MiniMax-M2.7', 'MiniMax-M2.5'],
     'caveats': _c('max_completion_tokens', 'thinking_adaptive', 'thinking_must_disable'),
     'docs': 'https://platform.minimax.io/docs/guides/text-generation',
     'note': '**M3 请把推理设置选成「关闭」**（实测数据见 caveat）。'
             '它默认开 adaptive thinking，响应里会带 `<think>` 块；'
             '要拆到 reasoning_content 字段请开 thinking.reasoning_split。'},
    {'id': 'minimax-cn', 'label': 'MiniMax（国内站 api.minimax.cn）', 'protocol': OPENAI,
     'endpoint': 'https://api.minimax.cn/v1/chat/completions',
     'models': ['MiniMax-M3'],
     'caveats': _c('max_completion_tokens', 'thinking_adaptive', 'thinking_must_disable'),
     'docs': 'https://platform.minimax.cn/docs/api-reference/text-chat',
     'note': '**国内账号用这个端点**。国际站的 key 打这里会 401——**地域搞错是很常见的坑**。'
             '**M3 请把推理设置选成「关闭」**：默认的自适应思考会把输出预算吃光。'},

    # ── 聚合平台（一个 key 多家，模型名带厂商前缀）──
    {'id': 'openrouter', 'label': 'OpenRouter（聚合）', 'protocol': OPENAI,
     'endpoint': 'https://openrouter.ai/api/v1/chat/completions',
     'models': ['deepseek/deepseek-chat', 'anthropic/claude-sonnet-4'],
     'caveats': _c('model_prefix'),
     'docs': 'https://openrouter.ai/docs',
     'note': '模型名格式是 `厂商/模型`，例如 `anthropic/claude-sonnet-4`。'},
    {'id': 'siliconflow', 'label': '硅基流动 SiliconFlow（聚合）', 'protocol': OPENAI,
     'endpoint': 'https://api.siliconflow.cn/v1/chat/completions',
     'models': ['deepseek-ai/DeepSeek-V3', 'Qwen/Qwen2.5-72B-Instruct'],
     'caveats': _c('model_prefix'), 'docs': 'https://docs.siliconflow.cn'},

    # ── 国外（非 OpenAI 兼容，需要适配层）──
    {'id': 'anthropic', 'label': 'Anthropic Claude', 'protocol': ANTHROPIC,
     'endpoint': 'https://api.anthropic.com/v1/messages',
     'models': ['claude-sonnet-4-5', 'claude-opus-4-1', 'claude-haiku-4-5'],
     'caveats': [], 'docs': 'https://docs.anthropic.com/en/api/messages',
     'note': 'Anthropic 不认 `response_format`；本产品靠提示词要求它只回 JSON。'},
    {'id': 'gemini', 'label': 'Google Gemini', 'protocol': GEMINI,
     'endpoint': 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
     'models': ['gemini-2.5-pro', 'gemini-2.5-flash'],
     'caveats': [], 'docs': 'https://ai.google.dev/api/generate-content',
     'note': 'Gemini 把模型名放在**路径里**，且 system 提示词单独放 `systemInstruction`。'},

    # ── 本地（loopback + http 已被 engine 放行，key 可不填）──
    {'id': 'ollama', 'label': 'Ollama（本地）', 'protocol': OPENAI,
     'endpoint': 'http://127.0.0.1:11434/v1/chat/completions',
     'models': ['qwen2.5:7b', 'llama3.1:8b'],
     'caveats': _c('key_optional', 'model_prefix'),
     'docs': 'https://github.com/ollama/ollama',
     'note': '模型名要用 Ollama 的 `名称:标签` 形式；先 `ollama pull` 拉下来。'},
    {'id': 'lmstudio', 'label': 'LM Studio（本地）', 'protocol': OPENAI,
     'endpoint': 'http://127.0.0.1:1234/v1/chat/completions',
     'models': [], 'caveats': _c('key_optional'), 'docs': 'https://lmstudio.ai/docs',
     'note': '模型名是 LM Studio 里显示的名字，需先在它的 Local Server 页启动服务。'},
    {'id': 'vllm', 'label': 'vLLM / 任意 OpenAI 兼容自建服务', 'protocol': OPENAI,
     'endpoint': 'http://127.0.0.1:8000/v1/chat/completions',
     'models': [], 'caveats': _c('key_optional'),
     'note': '自建服务端口自己改；同样只支持 loopback 明文 HTTP。'},
]

BY_ID = {p['id']: p for p in CATALOG}


# ── 端点校验：按协议放宽，而不是一刀切 ─────────────────────

_ENDPOINT_RULES = {
    # OpenAI 兼容的**形状不止一种**：DeepSeek/Qwen/Moonshot 是 `/v1/chat/completions`，
    # 而 MiniMax 曾经是 `/text/chatcompletion_v2`（该端点现已废弃，目录改用
    # `/v1/chat/completions`）。所以规则放宽成「路径里带一个 chat 端点」，
    # 而不是死抠 `/chat/completions`——**新旧两种形状都放行**，
    # 免得因为路径写法不同就把一家能用的拒之门外。
    OPENAI: (r'/chat(compl)?[^/]*$|/chat/completions/?$', '完整 HTTPS 的 chat 端点'),
    ANTHROPIC: (r'/v1/messages/?$', '完整 HTTPS 的 /v1/messages 端点'),
    GEMINI: (r':generateContent$', '完整 HTTPS 的 …/models/<模型>:generateContent 端点'),
}


def protocol_for(endpoint: str) -> str:
    """从端点形状**推断**协议；认不出来就说不知道，不硬套 OpenAI。

    宁可说「不知道」也别猜错——猜错的后果是拿 OpenAI 的请求体去打
    `/v1/messages`，拿到的 400 会让用户以为是自己 key 的问题。
    """
    for name, (pattern, _hint) in _ENDPOINT_RULES.items():
        if re.search(pattern, endpoint or ''):
            return name
    return ''


def check_endpoint(endpoint: str, protocol: str = '') -> str:
    """校验端点。`protocol` 省略时按形状推断；推断不出则拒绝并说清要求。

    沿用 `engine.check_endpoint` 的全部安全纪律：https 或数值 loopback、
    禁止 URL 里带用户名密码 / query / fragment。
    """
    import ipaddress
    from urllib.parse import urlsplit

    if protocol and protocol not in PROTOCOLS:
        raise ValueError(f'未知协议 {protocol}')
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise ValueError('端点不能为空')
    u = urlsplit(endpoint)
    try:
        loopback = bool(u.hostname and ipaddress.ip_address(u.hostname).is_loopback)
    except ValueError:
        loopback = False
    if (not u.hostname or u.username is not None or u.password is not None
            or u.query or u.fragment
            or (u.scheme != 'https' and not (u.scheme == 'http' and loopback))):
        raise ValueError('端点必须是完整 HTTPS，或数值 loopback 上的 HTTP；'
                         '不能带用户名、密码、query 或 fragment')
    try:
        u.port
    except ValueError as exc:
        raise ValueError('端点端口非法') from exc

    resolved = protocol or protocol_for(endpoint)
    if not resolved:
        hints = '；'.join(h for _p, h in _ENDPOINT_RULES.values())
        raise ValueError(f'认不出这是哪家的端点。要求形如：{hints}。'
                         f'也可以在「协议」里显式选一个。')
    pattern, _hint = _ENDPOINT_RULES[resolved]
    if not re.search(pattern, endpoint):
        raise ValueError(f'端点与所选协议 {resolved} 不符（要求 {_hint}）')
    return endpoint


# ── 请求构造 ──────────────────────────────────────────────

#: **同一概念、不同参数名**的一家人。2026-10-02 查 MiniMax 官方文档才发现：
#: 它已弃用 `max_tokens`，改叫 `max_completion_tokens`——
#: 照旧名发过去，要么被 400 拒，要么**被静默忽略然后用默认长度**，
#: 表现成「模型怎么老是写一半」。这类差异是「看起来在正常工作」的重灾区。
#:
#: **只有一条判据：`max_tokens_field()`。** 2026-10-02 这里一度存在两套机制
#: （按协议查表 + 按域名判断），而按协议查表**对 MiniMax 会给出错误答案**——
#: MiniMax 用的就是 `openai` 协议，协议根本区分不出它。
#: 同一件事两套机制、其中一套还是错的，等于给未来埋雷，所以合并成一条。
MAX_TOKENS_FIELD = {
    OPENAI: 'max_tokens',
    ANTHROPIC: 'max_tokens',
    GEMINI: None,          # 走 generationConfig.maxOutputTokens
}

#: **协议区分不出来的差异，只能按端点认。** 键是小写域名片段。
_ENDPOINT_MAX_TOKENS_FIELD = {
    'minimax.io': 'max_completion_tokens',
    'minimax.cn': 'max_completion_tokens',
}


def max_tokens_field(endpoint: str = '', protocol: str = ''):
    """这个端点该用哪个长度参数名。认不出就按 OpenAI 形状（保守）。

    **顺序不能反**：先按域名（协议分辨不出的差异），再按协议（通用差异）。
    """
    host = (endpoint or '').lower()
    for needle, field in _ENDPOINT_MAX_TOKENS_FIELD.items():
        if needle in host:
            return field
    proto = protocol or protocol_for(endpoint or '')
    return MAX_TOKENS_FIELD.get(proto, MAX_TOKENS_FIELD[OPENAI])


def _openai_request(messages, model, max_tokens, extra, endpoint=''):
    body = {'model': model, 'messages': messages, 'stream': False}
    # 长度参数名按端点走——**不能写死 `max_tokens`**。
    # 用显式参数判断端点，**不用模块级全局**：全局会让并发调用互相污染
    # （2026-10-02 已有一次「共享状态坑」的教训）。
    field = max_tokens_field(endpoint, OPENAI)
    if field:
        body[field] = max_tokens
    body.update(extra or {})
    return {}, body


def _anthropic_request(messages, model, max_tokens, extra, endpoint=''):
    """Anthropic：`system` 单独一条，`content` 是数组，**没有** response_format。"""
    system = '\n\n'.join(m['content'] for m in messages if m.get('role') == 'system')
    turns = [{'role': m['role'], 'content': [{'type': 'text', 'text': m['content']}]}
             for m in messages if m.get('role') != 'system']
    body = {'model': model, 'messages': turns, 'max_tokens': max_tokens}
    if system:
        body['system'] = system
    body.update(extra or {})
    # Anthropic 的版本头是必填的，不给就 400
    return {'x-api-key': '__KEY__', 'anthropic-version': '2023-06-01'}, body


def _gemini_request(messages, model, max_tokens, extra, endpoint=''):
    """Gemini：`systemInstruction` 单独字段，模型名在**路径**里，没有 max_tokens 上限字段。"""
    system = '\n\n'.join(m['content'] for m in messages if m.get('role') == 'system')
    contents = [{'role': 'model' if m.get('role') == 'assistant' else 'user',
                 'parts': [{'text': m['content']}]}
                for m in messages if m.get('role') != 'system']
    body = {'contents': contents, 'generationConfig': {'maxOutputTokens': max_tokens}}
    if system:
        body['systemInstruction'] = {'parts': [{'text': system}]}
    body.update(extra or {})
    return {'x-goog-api-key': '__KEY__'}, body


_BUILDERS = {OPENAI: _openai_request, ANTHROPIC: _anthropic_request, GEMINI: _gemini_request}

#: **只认 OpenAI 形状的字段**。给别的协议发过去会被 400，
#: 而 400 的文案通常指向 key 或配额——**用户会去换 key，真因是请求形状**。
_OPENAI_ONLY_FIELDS = ('response_format', 'stream', 'frequency_penalty',
                       'presence_penalty', 'logprobs', 'seed')


def build_request(protocol, messages, model, max_tokens, extra=None, endpoint=''):
    """返回 `(headers, body, url)`。`__KEY__` 是**占位符**，由 `install_key` 最后一刻替换。

    **会主动剔除 OpenAI 独有的字段**——调用方自己传 `response_format` 进来也一样剔。
    只在 `chat_complete` 那里防是不够的：那层本来就不传，但它是个纯函数、
    守卫可以直接断言「发给 Anthropic 的 body 里绝不该有 `response_format`」。
    """
    if extra and protocol != OPENAI:
        extra = {k: v for k, v in extra.items() if k not in _OPENAI_ONLY_FIELDS}
    headers, body = _BUILDERS[protocol](messages, model, max_tokens, extra, endpoint)
    url = ''
    return headers, body, url


def install_key(headers, api_key, protocol, endpoint=''):
    """把占位符换成真 key。**只在这一刻，key 才进入请求头**。"""
    if not api_key:
        return dict(headers)
    out = dict(headers)
    if protocol == ANTHROPIC:
        out['x-api-key'] = api_key
    elif protocol == GEMINI:
        out['x-goog-api-key'] = api_key
    else:
        out['Authorization'] = f'Bearer {api_key}'
    return out


# ── 响应解析：三家取文本的形状完全不同 ──────────────────────

#: 一些家把思考过程**直接塞进 `content`**，用 `<think>…</think>` 包起来。
#: 2026-10-02 实测 MiniMax M3：
#:   content = '<think>The user is asking me to return…</think>\n\n{"ok":true}'
#:   usage.completion_tokens_details.reasoning_tokens = 0
#: ——它**没有**把思考单独计费，而是原样写进正文。
#: 不剥就直接 `json.loads` 必然失败，而且报错会指向「模型返回的不是 JSON」，
#: **完全看不出真因是前面多了一段思考**。
_THINK = re.compile(r'<think>.*?</think>', re.S | re.I)
#: 数**开标签**用——不能靠「末尾有没有 `</think>`」来判断。
#: 2026-10-02 踩过的坑：先写成 `<think>.*\Z` 判「未闭合」，
#: 结果它连**已经闭合**的那对标签也匹配（`.*\Z` 吃掉到结尾的一切），
#: 于是只要正文以 `<think>` 开头就一律抛「思考没结束」——
#: **剥离功能等于完全没生效**，而且失败得很像模型出了问题。
#: 数开闭标签的个数是唯一不会被这种自欺骗的判据。
_THINK_OPEN_TAG = re.compile(r'<think>', re.I)
_THINK_CLOSE_TAG = re.compile(r'</think>', re.I)


def strip_thinking(text: str):
    """剥掉 `<think>…</think>`。返回 `(正文, 剥了多少对, 是否有未闭合的思考块)`。

    「未闭合」= 开标签比闭标签多（模型被截断在思考里，正文根本不存在）。
    多对、多余的闭标签都按能剥的都剥掉处理。
    """
    if not text:
        return text, 0, False
    cleaned = _THINK.sub('', text).strip()
    paired = len(_THINK.findall(text))
    dangling = len(_THINK_OPEN_TAG.findall(text)) > len(_THINK_CLOSE_TAG.findall(text))
    return cleaned, paired, dangling


def parse_response(protocol, data):
    """统一成 `{'text', 'finish_reason', 'tool_calls'}`。

    **`finish_reason` 语义必须归一**——调用方现在只判两件事：
    「输出被截断了吗」与「它是不是要调工具」。两边术语不同但意思对得上。
    """
    if not isinstance(data, dict):
        raise ValueError('供应商返回的不是 JSON 对象')

    if protocol == OPENAI:
        choices = data.get('choices')
        if not isinstance(choices, list) or not choices:
            raise ValueError('响应里没有 choices——这不像 OpenAI 兼容格式')
        item = choices[0]
        msg = item.get('message') or {}
        raw_text = msg.get('content') or ''
        # **剥掉塞在正文里的思考块。** MiniMax M3 实测就是这样：
        # content 以 '<think>…</think>' 开头，后面才是真正的 JSON。
        text, closed, dangling = strip_thinking(raw_text)
        if dangling:
            # 思考块没闭合 = 模型被截断在思考里。这种情况**不能**当正常结果用。
            raise ValueError('响应只在 <think> 思考块里就结束了，'
                             '没有正文——多半是输出预算被推理吃光')
        return {'text': text,
                'finish_reason': item.get('finish_reason') or '',
                'tool_calls': bool(msg.get('tool_calls')),
                # **2026-10-05 实测**：MiniMax-M3 的 function-call 参数**受 schema 约束**
                # （3/3 合法且完全合规，含「问题与 schema 无关」那一案；见
                # evaluations/toolcall-probe.json）。要换承载通道就靠这两个字段，
                # 所以必须把 arguments 原样带出来——`tool_calls` 那个 bool 不够用。
                'tool_arguments': [str((c.get('function') or {}).get('arguments') or '')
                                   for c in (msg.get('tool_calls') or [])
                                   if isinstance(c, dict)],
                'thinking_stripped': closed}

    if protocol == ANTHROPIC:
        if data.get('type') == 'error':
            raise ValueError('Anthropic 返回了 error 类型响应')
        blocks = data.get('content')
        if not isinstance(blocks, list):
            raise ValueError('响应里没有 content 数组——这不像 Anthropic Messages 格式')
        text = ''.join(b.get('text', '') for b in blocks if isinstance(b, dict)
                       and b.get('type') == 'text')
        return {'text': text,
                'finish_reason': data.get('stop_reason') or '',
                'tool_calls': any(isinstance(b, dict) and b.get('type') == 'tool_use'
                                  for b in blocks)}

    if protocol == GEMINI:
        cands = data.get('candidates')
        if not isinstance(cands, list) or not cands:
            raise ValueError('响应里没有 candidates——这不像 Gemini 格式')
        cand = cands[0]
        parts = ((cand.get('content') or {}).get('parts') or [])
        text = ''.join(p.get('text', '') for p in parts if isinstance(p, dict))
        return {'text': text,
                'finish_reason': cand.get('finishReason') or '',
                'tool_calls': bool(cand.get('functionCall'))}

    raise ValueError(f'未知协议 {protocol}')


#: 「被截断」在三家的叫法不一样，但都表示「没说完」。
TRUNCATED = {
    OPENAI: {'length', 'max_tokens'},
    ANTHROPIC: {'max_tokens'},
    GEMINI: {'MAX_TOKENS', 'max_tokens', 'PROHIBITED_CONTENT'},
}

#: 「**正常说完**」的叫法同样不一样——OpenAI 叫 `stop`，Anthropic 叫 `end_turn`，
#: Gemini 叫 `STOP`。
#:
#: **这一条不配好的后果是：Anthropic 的每一个正常结果都会被当成「输出不完整」而拒掉。**
#: 而报错说的是「输出被截断」，用户会去调 max_tokens——**而他的输出根本没被截断**。
#:
#: 注意 Anthropic 的 `max_tokens` **只出现在「被截断」里，不在「完成」里**：
#: 它的 `stop_reason` 有三个值，`max_tokens` 恰恰表示**因为撞到上限而停**。
COMPLETED = {
    OPENAI: {'stop', 'tool_calls'},
    ANTHROPIC: {'end_turn', 'stop_sequence', 'tool_use'},
    GEMINI: {'STOP', 'SAFETY', 'RECITATION'},
}


def completed(protocol, finish_reason):
    return (finish_reason or '') in COMPLETED.get(protocol, set())
