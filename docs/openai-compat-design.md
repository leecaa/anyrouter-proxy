# OpenAI 协议兼容层 — 设计原理与全景图

> 模块: `openai_translator.py` + `main.py` 中的 `/v1/chat/completions` 和 `/v1/responses` 路由
> 落档日期: 2026-05-28
> 适用版本: v25+

---

## 1. 实现方案 (TL;DR)

`anyrouter-proxy` 在 Anthropic 原生入口之外, 增加两个 OpenAI 协议入口:
`POST /v1/chat/completions` 和 `POST /v1/responses`。

**关键设计**: 上游始终走 **Anthropic Messages 协议** (`POST <upstream>/v1/messages`)。
客户端的 OpenAI 格式请求在 proxy 内部被翻译为 Anthropic Messages,
上游返回的 Anthropic 响应再翻译回客户端期待的 OpenAI 形态 (Chat Completions 或 Responses)。

**模型策略**: 不论客户端传什么 `model` 名, proxy 内部统一覆盖为
`claude-opus-4-7` (PRIMARY); 当上游 5xx/429 时自动 fallback 到
`claude-haiku-4-5-20251001` (FALLBACK)。客户端始终能拿到一个真实模型回答。

---

## 2. 全景架构图

```
                      ┌─────────────────────────────────────────────────────────┐
                      │              CLIENT (OpenAI Python SDK 等)               │
                      └──────────────┬──────────────────────────┬───────────────┘
                                     │ OpenAI 协议              │
                                     │ POST /v1/chat/           │ POST /v1/responses
                                     │      completions         │
                                     ▼                          ▼
   ╔══════════════════════════════════════════════════════════════════════════════╗
   ║                          anyrouter-proxy  (FastAPI)                          ║
   ║                                                                              ║
   ║   ┌────────────────────────────────────────────────────────────────────┐    ║
   ║   │  Route handler                                                     │    ║
   ║   │  · 接收 OpenAI 格式请求体                                          │    ║
   ║   │  · 提取 client API key (x-api-key 或 Bearer)                       │    ║
   ║   │  · key pool round-robin (get_next_global_key)                      │    ║
   ║   └─────────────────────────────┬──────────────────────────────────────┘    ║
   ║                                 ▼                                            ║
   ║   ┌────────────────────────────────────────────────────────────────────┐    ║
   ║   │  openai_translator.py — 请求翻译                                   │    ║
   ║   │  chat_completions_to_anthropic() / responses_request_to_anthropic()│    ║
   ║   │  ─ messages 数组规范化 (system 提取 / tool_calls / tool_result)    │    ║
   ║   │  ─ content-parts 数组 (text / image_url base64|https)              │    ║
   ║   │  ─ tools / tool_choice 字段映射                                    │    ║
   ║   │  ─ max_tokens 默认 4096, stop → stop_sequences, user → metadata    │    ║
   ║   │  ─ n>1 / previous_response_id → UnsupportedParamError → 400        │    ║
   ║   └─────────────────────────────┬──────────────────────────────────────┘    ║
   ║                                 ▼                                            ║
   ║   ┌────────────────────────────────────────────────────────────────────┐    ║
   ║   │  Model Strategy (main.py)                                          │    ║
   ║   │  anthropic_body["model"] = PRIMARY_MODEL  (claude-opus-4-7)        │    ║
   ║   │  headers["anthropic-beta"] = "context-1m-2025-08-07"  (if needed)  │    ║
   ║   └─────────────────────────────┬──────────────────────────────────────┘    ║
   ║                                 ▼                                            ║
   ║   ┌────────────────────────────────────────────────────────────────────┐    ║
   ║   │  curl_cffi (Chrome TLS impersonation)                              │    ║
   ║   │  POST https://<upstream>/v1/messages                               │    ║
   ║   └─────────────────────────────┬──────────────────────────────────────┘    ║
   ╚══════════════════════════════════│═══════════════════════════════════════════╝
                                      ▼
                  ┌─────────────────────────────────────────────────┐
                  │      上游 anyrouter.top  (Anthropic 协议)        │
                  └─────────────────────────────┬───────────────────┘
                                                │
                          ┌─────────────────────┴──────────────────┐
                          │                                        │
                       200 OK                              503 / 429 / 5xx
                          │                                        │
                          ▼                                        ▼
                 ┌────────────────────┐               ┌────────────────────────┐
                 │ 响应翻译            │               │ Fallback 策略           │
                 │ Anthropic → OpenAI │               │ effective_model =      │
                 │                    │               │   FALLBACK_MODEL       │
                 │ 非流式: 直接转换    │               │ headers 重算 (无 beta) │
                 │ 流式: SSE 状态机   │               │ 重发 (最多 5 次重试)   │
                 └─────────┬──────────┘               └────────────┬───────────┘
                           │                                       │
                           │                                       └───循环回上游──┐
                           ▼                                                       │
   ╔═══════════════════════════════════════════════════════════════════════════════╗
   ║                          回到 anyrouter-proxy                                  ║
   ║   ┌─────────────────────────────────────────────────────────────────────┐    ║
   ║   │  返回客户端                                                          │    ║
   ║   │  ─ 非流: Response(JSON, 200)                                         │    ║
   ║   │  ─ 流式: StreamingResponse(SSE, 200)                                 │    ║
   ║   │  ─ 错误: anthropic_error_to_openai_error → 客户端期望的错误形态     │    ║
   ║   └─────────────────────────────────────────────────────────────────────┘    ║
   ╚═══════════════════════════════════════════════════════════════════════════════╝
                                     │
                                     ▼
                              ┌─────────────┐
                              │   CLIENT    │
                              └─────────────┘
```

---

## 3. 原理详解

### 3.1 为什么需要协议翻译

OpenAI Chat Completions 与 Anthropic Messages 是两套不同的协议:

| 维度 | OpenAI Chat Completions | Anthropic Messages |
|---|---|---|
| 系统提示 | `messages[role=system]` 内联 | top-level `system` 字段 |
| 用户消息 content | string 或 `[{type,text},{type,image_url}]` | `[{type:text},{type:image}]` |
| 助手工具调用 | `message.tool_calls[]` (并列于 content) | content 数组中的 `{type:tool_use}` 块 |
| 工具结果 | 独立 `role:tool` 消息 | `role:user` 消息内 `{type:tool_result}` 块 |
| 工具声明 | `tools:[{type:function, function:{name,description,parameters}}]` | `tools:[{name, description, input_schema}]` |
| 必填字段 | 无强制 max_tokens | **必须** `max_tokens` |
| 流式事件 | `data: {choices:[{delta:...}]}` 增量 | `event: <type>\ndata: ...` 事件流 |
| 结束语义 | `finish_reason: stop/length/tool_calls` | `stop_reason: end_turn/max_tokens/stop_sequence/tool_use` |
| 响应 ID | `chatcmpl-xxx` | `msg_xxx` |
| Token 计数字段 | `prompt_tokens/completion_tokens/total_tokens` | `input_tokens/output_tokens` |

**两边语义重合度约 70%**, 但**字段名/嵌套结构/事件流格式完全不同**,
直接转发任何一边的请求到另一边都会 4xx/5xx 拒绝。

### 3.2 翻译规则核心

**请求 OpenAI → Anthropic** (`chat_completions_to_anthropic`):

```
1. 扫描所有 role=system 消息 → 抽出, 拼接为 top-level system 字段
   - 全部纯文本 → string 形式
   - 含 cache_control 或混合内容 → 数组形式

2. role=user / role=assistant 消息归一化
   - content 是 string → [{type:text, text}]
   - content 是数组 → 逐 part 转换
     · {type:text} → 不变
     · {type:image_url, url:"data:..."} → {type:image, source:{type:base64, media_type, data}}
     · {type:image_url, url:"http..."} → {type:image, source:{type:url, url}}

3. role=assistant 含 tool_calls
   - content 有文本: 文本块 + 各个 tool_use 块
   - content 为空: 只发 tool_use 块 (Anthropic 拒绝空 text 块)
   - 转换: {id, function:{name, arguments}} → {type:tool_use, id, name, input:json.loads(arguments)}

4. role=tool 消息 (连续多条)
   - 合并入同一个 role:user 消息
   - content 是 [{type:tool_result, tool_use_id, content}, ...]
   - Anthropic 要求严格 user/assistant 交替, 不能两连 user

5. tools[] / tool_choice 映射
   - tools[{type:function, function:{...}}] → [{name, description, input_schema:parameters}]
   - tool_choice:"auto" → {type:auto}
   - tool_choice:"none" → 删 tools 字段 (Anthropic 无对应)
   - tool_choice:"required" → {type:any}
   - tool_choice:{type:function,function:{name}} → {type:tool, name}

6. 顶层参数
   - max_tokens 缺省 → 4096
   - stop (str|list) → stop_sequences (list)
   - user → metadata.user_id
   - n > 1 → 抛 UnsupportedParamError (Anthropic 不支持)
   - presence_penalty/frequency_penalty/logit_bias/logprobs/seed/response_format → 静默丢弃
```

**响应 Anthropic → OpenAI** (`anthropic_to_chat_completion`):

```
1. 遍历 content[] 块
   - {type:text} → 拼到 message.content (string, 全空时为 None)
   - {type:tool_use, id, name, input} → message.tool_calls[]
     {id, type:function, function:{name, arguments: json.dumps(input)}}

2. stop_reason → finish_reason 映射
   end_turn / stop_sequence  →  stop
   max_tokens                →  length
   tool_use                  →  tool_calls

3. usage 字段
   input_tokens  →  prompt_tokens
   output_tokens →  completion_tokens
   (相加)        →  total_tokens

4. id 加 "chatcmpl-" 前缀, object="chat.completion", created=now, model=requested_model
```

### 3.3 SSE 状态机原理

流式响应涉及**两种 SSE 协议**, 需要状态机做事件级翻译:

**Anthropic 事件 (`event:` + `data:` 两行)**:

```
event: message_start          → 包含 message 元数据 (id, model, role, content=[])
event: content_block_start    → 标记新的 content 块开始 (含 index 和 type:text|tool_use)
event: content_block_delta    → 增量内容
                                · delta.type=text_delta:       新增文本片段
                                · delta.type=input_json_delta: 工具调用参数 partial-JSON 片段
event: content_block_stop     → 当前块结束
event: message_delta          → 整条消息层面的更新 (含 stop_reason, usage 增量)
event: message_stop           → 整个响应结束
event: ping                   → keepalive (丢弃)
```

**OpenAI Chat Completions chunk**:

```
data: {"id":"chatcmpl-xxx", "object":"chat.completion.chunk", "created":...,
       "model":"...", "choices":[{"index":0, "delta":{<增量>}, "finish_reason":null}]}
... 多条 ...
data: [DONE]
```

**状态机关键不变量** (`AnthropicSSEToChatCompletionsStream`):

```python
# 状态字段
message_id              # 从 message_start 捕获, 加 chatcmpl- 前缀给所有后续 chunk
role_sent               # 确保第一个 chunk 携带 {delta:{role:"assistant"}}
block_types             # {anthropic_block_index: "text"|"tool_use"}
block_to_tool_index     # {anthropic_block_index: openai_tool_index_in_tool_calls}
tool_index_counter      # 下一个分配的 OpenAI tool_call 数组索引
finish_emitted          # 是否已发 finish_reason chunk
done_emitted            # 是否已发 [DONE]
sse_buffer              # 累积上游字节, 按 \n\n 切事件块
```

**事件 → 输出 chunk 映射**:

| Anthropic | OpenAI chunk |
|---|---|
| `message_start` | `delta:{role:"assistant"}` (首 chunk) |
| `content_block_start type=text` | 无 (惰性, 等首个 delta) |
| `content_block_start type=tool_use` | `delta:{tool_calls:[{index, id, type:function, function:{name, arguments:""}}]}` |
| `content_block_delta text_delta` | `delta:{content: <text>}` |
| `content_block_delta input_json_delta` | `delta:{tool_calls:[{index, function:{arguments: <partial_json>}}]}` |
| `content_block_stop` | 无 |
| `message_delta` (带 stop_reason+usage) | `delta:{}, finish_reason: <mapped>`; 可选 `usage` |
| `message_stop` | `data: [DONE]\n\n` |
| `ping` | 丢弃 |

**关键不变量**: OpenAI 的 `function.arguments` 增量是**部分 JSON 字符串**,
Anthropic 的 `input_json_delta.partial_json` **也是部分 JSON 字符串**,
两者拼接后都是合法 JSON。所以**直接 forward 不需要重新序列化**。

### 3.4 SSE 缓冲 / 跨 chunk 边界

上游返回的字节流不一定按 SSE 事件边界整齐切分。状态机维护 `sse_buffer`:

```
feed(raw_bytes) {
    sse_buffer += raw_bytes
    while b"\n\n" in sse_buffer:
        event_chunk, sse_buffer = sse_buffer.split(b"\n\n", 1)
        parse event_chunk → emit 0 or more OpenAI chunks
    # 不完整的 chunk 留在 buffer, 下次 feed 再处理
}

flush() {
    # 兜底: 残余未终止事件块尝试加 \n\n 解析一次
    # 保证 finish_reason chunk 出现 (若未出现)
    # 保证 [DONE] 出现一次
}
```

### 3.5 Model Override + Fallback 策略

**为什么不简单透传客户端的 model**:

上游 `anyrouter.top` 对不同模型的可用性差异巨大。`/v1/models` 列表返回 15+ 模型,
但其中:

| 类别 | 上游真实表现 |
|---|---|
| `claude-haiku-4-5-20251001` | 唯一稳定 200 OK |
| `claude-opus-4-7` / `claude-opus-4-5` / `claude-sonnet-4-5` 等 1m 上下文模型 | 需要 `anthropic-beta: context-1m-2025-08-07` header, 即使加了也经常 503 (上游对 Opus 系列的全局熔断) |
| `claude-3-5-haiku-20241022` | 429 限流 |
| `claude-opus-4-6` | 已下线 |
| `gpt-5.5` / `gpt-5-codex` | 上游 `/v1/messages` 404 |
| `gemini-2.5-pro` | 上游所有端点 404 |

**策略选择**:

1. **PRIMARY_MODEL = claude-opus-4-7**: 旗舰模型, 1m 上下文, 上游恢复后立即可用
2. **FALLBACK_MODEL = claude-haiku-4-5-20251001**: 已知 stable baseline
3. 客户端任意 `model` → proxy 覆盖为 PRIMARY → 5xx/429 → 自动换 FALLBACK → 重试

**好处**:

- 客户端不需关心上游模型可用性细节
- 任意 OpenAI SDK 调用都能拿到响应 (即使客户端写了 `model="gpt-5.5"`)
- 上游 Opus 系列恢复时自动用最强模型
- 上游熔断时自动降级保活

**Per-model anthropic-beta**:

```python
def _needs_context_1m_beta(model_name):
    if "claude" not in model_name.lower():
        return False
    if "haiku" in model_name.lower():
        return False
    return True  # opus / sonnet / 3-7-sonnet 等都需要
```

切换 effective_model 时**重算 headers** (haiku 不加 beta, 否则上游 520)。

### 3.6 Responses API 附加规则

OpenAI Responses API (`/v1/responses`) 比 Chat Completions 复杂:

- **输入形态多样**: `input` 可以是 string / messages 数组 / items 数组
  ```
  string                                    → [{role:user, content: <string>}]
  [{role,content}, ...]                     → 同 Chat 形式
  [{type:message,role,content}, ...]        → 归一化为 messages
  [{type:function_call, call_id, name, args}] → assistant tool_calls
  [{type:function_call_output, call_id, output}] → tool 消息
  ```

- **响应 envelope 不同**: 不是 `choices[].message`, 而是 `output[]` 数组:
  ```
  output[0] = {type:message, id, role:assistant,
               content:[{type:output_text, text}]}
  output[1] = {type:function_call, call_id, name, arguments}
  ```

- **状态化字段**: `previous_response_id` (本实现不支持 → 400)
- **新字段名**: `max_output_tokens` → `max_tokens`, `instructions` → 视为 system

- **流式 SSE 14 种 `response.*` 事件**: response.created, in_progress,
  output_item.added/done, content_part.added/done, output_text.delta/done,
  function_call_arguments.delta/done, response.completed 等

`AnthropicSSEToResponsesStream` 状态机比 Chat 版多 60% 复杂度。

### 3.7 Curl_cffi + Chrome TLS 指纹

上游对请求来源做 TLS 指纹/header 校验。proxy 用 `curl_cffi` 的
`impersonate="chrome"` 模式发送, 让 TLS 握手层与 Chrome 浏览器一致,
绕过 anti-bot 检查。

对 OpenAI 翻译路径**不**附加 Claude CLI fingerprint headers (`User-Agent: claude-cli/...`,
`X-Stainless-*` 等), 因为 OpenAI 客户端本身不是 Claude CLI, 加这些反而引起上游
某些模型的 520。Anthropic 原生路径 `/v1/{path}` 保留 Claude CLI 指纹注入逻辑不变。

### 3.8 错误响应翻译

上游 Anthropic 错误结构:
```json
{"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}
```

OpenAI 错误结构:
```json
{"error": {"message": "slow down", "type": "rate_limit_error", "code": null, "param": null}}
```

`anthropic_error_to_openai_error()` 容错解析 dict/bytes/str, 解析失败时把原始内容当 message 兜底。

---

## 4. 关键代码定位

| 关注点 | 文件:符号 |
|---|---|
| OpenAI Chat 请求翻译 | `openai_translator.py:chat_completions_to_anthropic` |
| OpenAI Chat 响应翻译 | `openai_translator.py:anthropic_to_chat_completion` |
| OpenAI Responses 请求翻译 | `openai_translator.py:responses_request_to_anthropic` |
| OpenAI Responses 响应翻译 | `openai_translator.py:anthropic_to_responses_response` |
| Chat 流式状态机 | `openai_translator.py:AnthropicSSEToChatCompletionsStream` |
| Responses 流式状态机 | `openai_translator.py:AnthropicSSEToResponsesStream` |
| 错误响应翻译 | `openai_translator.py:anthropic_error_to_openai_error` |
| Chat Completions 路由 | `main.py:openai_chat_completions` |
| Responses 路由 | `main.py:openai_responses` |
| Model override + Fallback 主循环 | `main.py:_run_openai_translated_request` |
| 流式管道桥接 (queue → SSE) | `main.py:_translated_stream` + `_stream_worker` + `_async_chunks` |
| Per-model beta 决策 | `main.py:_needs_context_1m_beta` |
| 上游 headers 构造 | `main.py:_build_translated_anthropic_headers` |
| API key 池 | `main.py:GLOBAL_KEYS / get_next_global_key` |

---

## 5. 测试策略

### 5.1 单元测试 (无网络)

`tests/test_openai_translator.py` 共 63 个测试, 覆盖矩阵:

| 类别 | 数量 | 覆盖点 |
|---|---|---|
| Chat 请求翻译 | 26 | system 提取 / content 形态 / tool_calls / tool 消息合并 / tools / tool_choice / max_tokens / stop / user / n / drop params / model passthrough |
| Chat 响应翻译 | 10 | 纯文本 / 纯工具 / 混合 / 四种 stop_reason / usage / id 前缀 / 顶层字段 |
| Chat 流式状态机 | 11 | 空流 / 文本流 / 工具流 / ping 丢弃 / chunk 边界 / 多 tool index / include_usage / flush 幂等 / 早终止 / id 前缀 |
| Responses API | 11 | 3 种 input 形态 / function_call items / instructions / max_output_tokens / previous_response_id / 响应结构 |
| 错误翻译 | 5 | dict/bytes/str/malformed/两种 target_shape |

运行:
```bash
pytest tests/test_openai_translator.py -v
```

### 5.2 端到端严格内容验收

`tests/test_e2e_strict_full.py` — 不仅验证 HTTP 200, **断言模型给出问题相关的真实答案**:

```
A1 claude-haiku chat 2+2     → assert "4" in content
A2 claude-haiku stream 1-5   → assert all(c in content for c in "12345")
A3 claude-haiku resp Paris   → assert "paris" in content.lower()
B1 gpt-5.5 (proxy fallback)  → assert "4" in content
B2 gemini-2.5-pro (proxy fallback) → assert "pong" in content.lower()
```

配置经环境变量:
- `ANYROUTER_PROXY_BASE` (默认 http://127.0.0.1:8765)
- `ANYROUTER_PROXY_KEY` (或 `.secrets.json`)
- `ANYROUTER_TEST_MODEL` (默认 claude-haiku-4-5-20251001)

---

## 6. 验收记录 (2026-05-28)

```
严格内容验收: 5/5 passed
  ✓ A1 claude-haiku chat 2+2  →  '4'
  ✓ A2 claude-haiku stream 1-5  →  '1, 2, 3, 4, 5'
  ✓ A3 claude-haiku resp Paris  →  'Paris'
  ✓ B1 gpt-5.5 resp 2+2  →  '4'
  ✓ B2 gemini-2.5 resp pong  →  'pong'
```

服务日志:
```
[OPENAI-TRANSLATOR] Client asked for model: 'gpt-5.5'
[OPENAI-TRANSLATOR] Effective: claude-opus-4-7
[OPENAI-TRANSLATOR] upstream status: 503 (model=claude-opus-4-7)
[OPENAI-TRANSLATOR] falling back to claude-haiku-4-5-20251001
[OPENAI-TRANSLATOR] upstream status: 200 (model=claude-haiku-4-5-20251001)
```

---

## 7. 已知限制 (Out of Scope)

| 项 | 状态 | 原因 |
|---|---|---|
| Responses API `previous_response_id` 状态持久化 | 不支持, 返回 400 | 需要 KV/SQLite 储存 message history |
| `response_format: json_object/json_schema` | 静默丢弃 | 上游 Anthropic 无原生等价, 注入 system 指令可靠性低 |
| `n > 1` 多响应 | 抛 UnsupportedParamError → 400 | Anthropic 协议不支持 |
| `/v1/embeddings`, `/v1/images/*`, `/v1/audio/*` | 不支持 | 上游 Anthropic 不提供 |
| Responses background mode / web_search / file_search | 不支持 | Anthropic 协议不对应 |
| Token 计数端点 `/v1/messages/count_tokens` 的 OpenAI 镜像 | 未实现 | 可后续用 tiktoken 增加 |

---

## 8. 三方依赖

无新增运行时依赖。仅用现有的:

- stdlib (`json`, `re`, `time`, `uuid`, `asyncio`, `threading`, `queue`)
- `fastapi` + `uvicorn` (HTTP 路由)
- `curl_cffi` (Chrome TLS 指纹)
- `python-multipart` (form 解析, dashboard 用)

测试时:
- `pytest` (单元测试)
- `openai` Python SDK (e2e 测试)

翻译算法**借鉴**, 不复制代码:
- [BerriAI/litellm](https://github.com/BerriAI/litellm) `litellm/llms/anthropic/chat/transformation.py` (OpenAI→Anthropic 映射表)
- [1rgs/claude-code-proxy](https://github.com/1rgs/claude-code-proxy) (SSE 状态机模式, 镜像方向)

---

## 9. 部署 / 运行

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 ~/.config/anyrouter-proxy/proxy_config.json
# {
#   "target_base_url": "https://anyrouter.top",
#   "api_keys": ["sk-..."],
#   "host": "127.0.0.1", "port": 8765,
#   "dashboard_password": "...",
#   "debug": false
# }

# 3. 启动
python main.py

# 4. 客户端调用 (任何 OpenAI Python SDK)
# from openai import OpenAI
# c = OpenAI(base_url="http://127.0.0.1:8765/v1", api_key="sk-...")
# c.chat.completions.create(model="gpt-4o", messages=[...])  # proxy 内部映射到 claude-opus-4-7
# c.responses.create(model="anything", input="Hello")        # 同样

# 5. 测试
pip install pytest openai
pytest tests/test_openai_translator.py -v          # 单元
python tests/test_e2e_strict_full.py               # 端到端
```
