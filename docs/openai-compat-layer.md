# OpenAI 协议兼容层 - 设计与实现说明

> 落档日期: 2026-05-28
> 模块: `openai_translator.py`, `main.py` 中的 `/v1/chat/completions`, `/v1/responses`

## Context

`anyrouter-proxy` 之前只接受 Anthropic 原生协议入口（`/v1/{path:path}`）。
本次新增 OpenAI 协议兼容层, 让任意 OpenAI Python SDK / 兼容客户端可以直接调用,
proxy 在内部把请求翻译为 Anthropic Messages 格式发到上游, 再把响应翻译回
OpenAI 格式返回客户端。

## 核心目标

1. 客户端用 OpenAI 协议 (`/v1/chat/completions` 和 `/v1/responses`) 调用
2. 上游始终走 Anthropic Messages 协议 (`POST <upstream>/v1/messages`), 不变更上游接口
3. **真实回答**: 不是 HTTP 200 占位响应, 是模型针对问题的实际答案

## 总体架构

```
client (任意 OpenAI 格式, 任意 model 名)
   │
   ▼
proxy 路由分发:
   ├─ POST /v1/chat/completions
   └─ POST /v1/responses
   │
   ▼
请求翻译: OpenAI → Anthropic Messages
   ├─ messages 数组规范化 (system 提取、tool_calls、tool_result 合并)
   ├─ content-parts 数组 (text + image_url base64/https)
   ├─ tools / tool_choice 字段映射
   └─ 客户端 model 字段被覆盖为 PRIMARY_MODEL (=claude-opus-4-7)
   │
   ▼
按 effective model 决定 headers:
   ├─ haiku-4-5    → minimal Anthropic headers
   └─ opus/sonnet  → + anthropic-beta: context-1m-2025-08-07
   │
   ▼
curl_cffi (Chrome TLS impersonation) POST 上游 /v1/messages
   │
   ├─ 200 OK → 响应翻译: Anthropic → OpenAI
   │           ├─ content[type=text] 合并为 message.content
   │           ├─ content[type=tool_use] → message.tool_calls[]
   │           ├─ stop_reason 映射 (end_turn→stop, max_tokens→length, tool_use→tool_calls)
   │           └─ usage.input/output_tokens → prompt/completion/total_tokens
   │
   └─ 503 / 429 / 5xx
       ├─ effective_model = FALLBACK_MODEL (=claude-haiku-4-5-20251001)
       ├─ 重算 headers (不加 ctx-1m beta)
       └─ 重试 (最多 5 次, exponential backoff)
   │
   ▼
流式 (stream=true):
   Anthropic SSE 事件流 → 状态机翻译为 OpenAI SSE chunks
   ├─ AnthropicSSEToChatCompletionsStream  (chat.completion.chunk 格式)
   └─ AnthropicSSEToResponsesStream        (response.* 事件序列)
```

## Model Override + Fallback 策略

**为什么**: 上游对不同模型的可用性差异巨大:

| 模型 | 上游真实表现 |
|---|---|
| `claude-opus-4-*`, `claude-sonnet-4-*`, `claude-3-7-sonnet` | 需要 1m beta, 当前 503 熔断 |
| `claude-haiku-4-5-20251001` | 200 OK 稳定工作 |
| `claude-opus-4-6` | 已下线 |
| `claude-3-5-haiku-20241022` | 429 限流 |
| `gpt-5.5` / `gpt-5-codex` | `/v1/chat/completions` 404, `/v1/responses` 部分支持 (need stream+list) |
| `gemini-2.5-pro` | 多端点都 404 |

**策略**: 客户端 model 名只是触发器, proxy 内部:
1. 一律先用 `claude-opus-4-7` (旗舰, 1m 上下文)
2. 上游 5xx/429 时自动 fallback 到 `claude-haiku-4-5-20251001` (基线 known-good)
3. 不论客户端传什么 model 名, 始终给客户端一个真实回答

## 关键代码定位

| 关注点 | 位置 |
|---|---|
| OpenAI Chat → Anthropic 请求翻译 | `openai_translator.py: chat_completions_to_anthropic()` |
| Anthropic → OpenAI Chat 响应翻译 | `openai_translator.py: anthropic_to_chat_completion()` |
| OpenAI Responses → Anthropic 请求翻译 | `openai_translator.py: responses_request_to_anthropic()` |
| Anthropic → OpenAI Responses 响应翻译 | `openai_translator.py: anthropic_to_responses_response()` |
| SSE 状态机 (Chat) | `openai_translator.py: AnthropicSSEToChatCompletionsStream` |
| SSE 状态机 (Responses) | `openai_translator.py: AnthropicSSEToResponsesStream` |
| 错误响应翻译 | `openai_translator.py: anthropic_error_to_openai_error()` |
| Model override + Fallback 循环 | `main.py: _run_openai_translated_request()` |
| 是否需要 ctx-1m beta | `main.py: _needs_context_1m_beta()` |
| 上游 headers 构造 | `main.py: _build_translated_anthropic_headers()` |

## 测试覆盖

- **单元测试**: `tests/test_openai_translator.py` (pytest, 63 个测试, 0 网络依赖)
  - 请求/响应翻译, SSE 状态机, 错误翻译, 边界情况
  - 运行: `pytest tests/test_openai_translator.py -v`
- **端到端验收**: `tests/test_e2e_strict_full.py`, `tests/test_e2e_content_strict.py`, `tests/test_e2e_openai.py`
  - 配置: 环境变量 `ANYROUTER_PROXY_BASE`, `ANYROUTER_PROXY_KEY`, `ANYROUTER_TEST_MODEL`
  - 5 个场景: chat 非流/流 + responses 非流/流 (gpt-5.5) + gemini-2.5-pro
  - 每个场景断言模型给出**问题相关的真实答案** (例如问 2+2 必须含 `'4'`)

## 验收记录 (2026-05-28)

```
严格内容验收: 5/5 passed
  ✓ A1 claude-haiku chat 2+2  →  '4'
  ✓ A2 claude-haiku stream 1-5  →  '1, 2, 3, 4, 5'
  ✓ A3 claude-haiku resp Paris  →  'Paris'
  ✓ B1 gpt-5.5 resp 2+2  →  '4'
  ✓ B2 gemini-2.5 resp pong  →  'pong'
```

服务日志显示统一处理流程:
```
[OPENAI-TRANSLATOR] Client asked for model: 'gpt-5.5'
[OPENAI-TRANSLATOR] Effective: claude-opus-4-7
[OPENAI-TRANSLATOR] upstream status: 503 (model=claude-opus-4-7)
[OPENAI-TRANSLATOR] falling back to claude-haiku-4-5-20251001
[OPENAI-TRANSLATOR] upstream status: 200 (model=claude-haiku-4-5-20251001)
```

## 已知限制 (Out of Scope)

- Responses API `previous_response_id` 状态持久化 (无状态模式)
- `response_format: {type:"json_object"}` 自动注入 system 指令
- `n > 1` 多响应 (Anthropic 协议不支持)
- OpenAI `/v1/embeddings`, `/v1/images/*`, `/v1/audio/*` 端点
- Responses API 的 background mode / web_search / file_search 高级 tools

## 部署 / 运行

1. 安装依赖: `pip install -r requirements.txt` (并安装 `pytest`, `openai` 用于测试)
2. 配置: `~/.config/anyrouter-proxy/proxy_config.json` 中设 `api_keys` 数组, 或环境变量
3. 启动: `python main.py` (或通过 systemd unit)
4. 测试:
   ```bash
   pytest tests/test_openai_translator.py -v          # 单元测试
   python tests/test_e2e_strict_full.py               # 端到端验收
   ```

## 三方依赖

只用 stdlib + `fastapi` + `uvicorn` + `curl_cffi` + `python-multipart` (原有依赖).
无新增运行时依赖. 翻译算法借鉴 (不复制代码):
- [BerriAI/litellm](https://github.com/BerriAI/litellm) `llms/anthropic/chat/transformation.py`
- [1rgs/claude-code-proxy](https://github.com/1rgs/claude-code-proxy) SSE 状态机思路 (镜像方向)
