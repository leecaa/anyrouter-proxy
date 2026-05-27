"""OpenAI ⇄ Anthropic protocol translator.

Lightweight translator used by anyrouter-proxy to accept OpenAI-formatted
client requests at `/v1/chat/completions` and `/v1/responses`, then forward
them to upstream `https://anyrouter.top/v1/messages` in Anthropic Messages
format. The upstream is ALWAYS Anthropic; this module does the bidirectional
conversion.

Pure functions + two SSE state machines. No HTTP, no I/O — easily unit-testable.

Design references (algorithm only, no code copied):
- litellm/llms/anthropic/chat/transformation.py (OpenAI→Anthropic mapping table)
- 1rgs/claude-code-proxy server.py (SSE state machine pattern, mirrored direction)
"""
from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, Iterable


class UnsupportedParamError(Exception):
    """Raised when an OpenAI request param has no Anthropic equivalent and
    cannot be silently dropped (e.g. n>1, previous_response_id). The route
    handler should map this to HTTP 400 with type=invalid_request_error.
    """

    def __init__(self, message: str, *, param: str | None = None):
        super().__init__(message)
        self.param = param


# ============================================================================
# Internal helpers
# ============================================================================


_DATA_URL_RE = re.compile(
    r"^data:(?P<media>[^;,]+);base64,(?P<data>.+)$", re.DOTALL
)


def _transform_content_part(part: dict) -> dict:
    """Transform a single OpenAI content-part dict into an Anthropic block."""
    if not isinstance(part, dict):
        # Tolerate raw strings as text content
        return {"type": "text", "text": str(part)}

    ptype = part.get("type")
    if ptype == "text":
        block: dict[str, Any] = {"type": "text", "text": part.get("text", "")}
        if "cache_control" in part:
            block["cache_control"] = part["cache_control"]
        return block

    if ptype == "image_url":
        image_url = part.get("image_url") or {}
        url = image_url.get("url", "") if isinstance(image_url, dict) else str(image_url)
        m = _DATA_URL_RE.match(url or "")
        if m:
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": m.group("media"),
                    "data": m.group("data"),
                },
            }
        return {
            "type": "image",
            "source": {"type": "url", "url": url},
        }

    if ptype == "input_text":
        return {"type": "text", "text": part.get("text", "")}

    if ptype == "output_text":
        return {"type": "text", "text": part.get("text", "")}

    if ptype == "input_image":
        # Responses API style
        url = part.get("image_url") or ""
        if isinstance(url, dict):
            url = url.get("url", "")
        m = _DATA_URL_RE.match(url or "")
        if m:
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": m.group("media"),
                    "data": m.group("data"),
                },
            }
        return {"type": "image", "source": {"type": "url", "url": url}}

    # Unknown part type — best-effort passthrough as text
    if "text" in part:
        return {"type": "text", "text": part.get("text", "")}
    return {"type": "text", "text": json.dumps(part)}


def _transform_system_content(content: Any) -> Any:
    """Transform a system message's content. Returns either a string (when
    pure-text and no cache_control), or a list of {type:"text", text, ...}
    blocks (preserving cache_control)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        blocks: list[dict] = []
        all_simple_text = True
        for part in content:
            if isinstance(part, dict):
                if part.get("type") in ("text", "input_text", "output_text"):
                    block: dict[str, Any] = {
                        "type": "text",
                        "text": part.get("text", ""),
                    }
                    if "cache_control" in part:
                        block["cache_control"] = part["cache_control"]
                        all_simple_text = False
                    blocks.append(block)
                else:
                    blocks.append(_transform_content_part(part))
                    all_simple_text = False
            else:
                blocks.append({"type": "text", "text": str(part)})
        if all_simple_text and blocks:
            # Could collapse to single string, but the spec wants an array if
            # mixed; pure-text-only → return single concatenated string.
            return "\n".join(b["text"] for b in blocks)
        return blocks
    # Fallback
    return str(content) if content is not None else ""


def _normalize_user_assistant_content(content: Any) -> list[dict]:
    """Convert OpenAI user/assistant content to Anthropic content blocks list."""
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [_transform_content_part(p) for p in content]
    return [{"type": "text", "text": str(content)}]


def _convert_tool_choice(tool_choice: Any) -> tuple[dict | None, bool]:
    """Convert OpenAI tool_choice to Anthropic. Returns (anthropic_tool_choice,
    drop_tools). drop_tools=True means caller should drop the `tools` field
    entirely (for tool_choice='none')."""
    if tool_choice is None:
        return None, False
    if isinstance(tool_choice, str):
        if tool_choice == "auto":
            return {"type": "auto"}, False
        if tool_choice == "none":
            return None, True
        if tool_choice == "required":
            return {"type": "any"}, False
        return {"type": "auto"}, False
    if isinstance(tool_choice, dict):
        if tool_choice.get("type") == "function":
            fn = tool_choice.get("function") or {}
            name = fn.get("name") if isinstance(fn, dict) else None
            if name:
                return {"type": "tool", "name": name}, False
        # Already in Anthropic shape? Pass through.
        if tool_choice.get("type") in ("auto", "any", "tool"):
            return tool_choice, False
    return None, False


def _convert_tools(tools: Any) -> list[dict]:
    """Convert OpenAI tools array to Anthropic tools array."""
    if not isinstance(tools, list):
        return []
    out: list[dict] = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        # OpenAI Chat shape: {"type":"function", "function":{name, description, parameters}}
        if t.get("type") == "function" and isinstance(t.get("function"), dict):
            fn = t["function"]
            entry: dict[str, Any] = {
                "name": fn.get("name", ""),
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            }
            if fn.get("description"):
                entry["description"] = fn["description"]
            out.append(entry)
        # Responses API shape: {"type":"function", "name", "description", "parameters"}
        elif t.get("type") == "function" and "name" in t:
            entry = {
                "name": t.get("name", ""),
                "input_schema": t.get("parameters") or {"type": "object", "properties": {}},
            }
            if t.get("description"):
                entry["description"] = t["description"]
            out.append(entry)
        # Already Anthropic shape
        elif "name" in t and "input_schema" in t:
            out.append(t)
    return out


def _build_anthropic_messages(messages: list[dict]) -> tuple[list[dict], Any]:
    """Build Anthropic messages array (and system field) from OpenAI messages.

    Returns (messages, system_field_or_None).
    Handles:
    - role=system → top-level system
    - role=user/assistant → content blocks
    - role=assistant with tool_calls → tool_use blocks
    - consecutive role=tool → merged into one user message with tool_result blocks
    """
    system_parts: list[Any] = []  # collected raw content from each system msg
    a_messages: list[dict] = []
    pending_tool_results: list[dict] = []

    def flush_tool_results() -> None:
        if pending_tool_results:
            a_messages.append({"role": "user", "content": list(pending_tool_results)})
            pending_tool_results.clear()

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")

        if role == "system":
            flush_tool_results()
            system_parts.append(content)
            continue

        if role == "tool":
            # Collect into pending tool_results; will be flushed when next
            # non-tool message appears, or at end.
            tool_call_id = msg.get("tool_call_id", "")
            tool_content = content
            # Normalize tool_content: Anthropic accepts string or list of blocks
            if isinstance(tool_content, list):
                # Convert OpenAI parts to text blocks if needed
                normalized_blocks: list[dict] = []
                for p in tool_content:
                    if isinstance(p, dict) and p.get("type") in ("text", "input_text", "output_text"):
                        normalized_blocks.append({"type": "text", "text": p.get("text", "")})
                    elif isinstance(p, dict):
                        normalized_blocks.append(_transform_content_part(p))
                    else:
                        normalized_blocks.append({"type": "text", "text": str(p)})
                tool_block: dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": tool_call_id,
                    "content": normalized_blocks,
                }
            else:
                tool_block = {
                    "type": "tool_result",
                    "tool_use_id": tool_call_id,
                    "content": "" if tool_content is None else str(tool_content),
                }
            pending_tool_results.append(tool_block)
            continue

        # Any non-tool role: flush pending tool results into a user message first
        flush_tool_results()

        if role == "assistant":
            blocks: list[dict] = []
            # Text content (only emit if non-empty and not None)
            if isinstance(content, str) and content:
                blocks.append({"type": "text", "text": content})
            elif isinstance(content, list) and content:
                blocks.extend(_normalize_user_assistant_content(content))
            # tool_calls
            tool_calls = msg.get("tool_calls") or []
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or {}
                name = fn.get("name", "") if isinstance(fn, dict) else ""
                raw_args = fn.get("arguments", "") if isinstance(fn, dict) else ""
                try:
                    parsed_input = json.loads(raw_args) if raw_args else {}
                except (json.JSONDecodeError, TypeError):
                    parsed_input = {"_raw": raw_args}
                blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id", ""),
                    "name": name,
                    "input": parsed_input,
                })
            if blocks:
                a_messages.append({"role": "assistant", "content": blocks})
            # If no blocks at all, drop the message (Anthropic rejects empty content)
            continue

        if role == "user":
            blocks = _normalize_user_assistant_content(content)
            if blocks:
                a_messages.append({"role": "user", "content": blocks})
            continue

        # Unknown role — best-effort: treat as user
        blocks = _normalize_user_assistant_content(content)
        if blocks:
            a_messages.append({"role": "user", "content": blocks})

    # Flush trailing tool results
    flush_tool_results()

    # Build system field
    system_field: Any = None
    if system_parts:
        # If all parts are simple strings, concatenate; otherwise build array.
        all_strings = all(isinstance(p, str) for p in system_parts)
        if all_strings:
            system_field = "\n\n".join(p for p in system_parts if p)
        else:
            blocks_out: list[dict] = []
            for p in system_parts:
                transformed = _transform_system_content(p)
                if isinstance(transformed, str):
                    if transformed:
                        blocks_out.append({"type": "text", "text": transformed})
                elif isinstance(transformed, list):
                    blocks_out.extend(transformed)
            system_field = blocks_out if blocks_out else None

    return a_messages, system_field


# ============================================================================
# Chat Completions ⇄ Anthropic
# ============================================================================


def chat_completions_to_anthropic(
    openai_body: dict, *, default_max_tokens: int = 4096
) -> dict:
    """Translate an OpenAI Chat Completions request body into an Anthropic
    Messages request body.
    """
    if not isinstance(openai_body, dict):
        raise UnsupportedParamError("request body must be a JSON object")

    # n>1 is unsupported
    n = openai_body.get("n")
    if isinstance(n, int) and n > 1:
        raise UnsupportedParamError(
            "OpenAI 'n' > 1 not supported by Anthropic Messages API", param="n"
        )

    messages = openai_body.get("messages") or []
    if not isinstance(messages, list):
        raise UnsupportedParamError("'messages' must be a list", param="messages")

    a_messages, system_field = _build_anthropic_messages(messages)

    a_body: dict[str, Any] = {
        "model": openai_body.get("model", ""),
        "messages": a_messages,
        "max_tokens": openai_body.get("max_tokens") or default_max_tokens,
    }
    if system_field is not None and system_field != "":
        a_body["system"] = system_field

    # Pass-through simple scalars
    for k in ("temperature", "top_p", "stream", "top_k"):
        if k in openai_body and openai_body[k] is not None:
            a_body[k] = openai_body[k]

    # stop → stop_sequences (always list)
    stop = openai_body.get("stop")
    if stop is not None:
        if isinstance(stop, str):
            a_body["stop_sequences"] = [stop]
        elif isinstance(stop, list):
            a_body["stop_sequences"] = [str(s) for s in stop if s is not None]

    # user → metadata.user_id
    user = openai_body.get("user")
    if user:
        a_body["metadata"] = {"user_id": str(user)}

    # tools / tool_choice
    tools = openai_body.get("tools")
    tool_choice_raw = openai_body.get("tool_choice")
    a_tool_choice, drop_tools = _convert_tool_choice(tool_choice_raw)
    if tools and not drop_tools:
        converted = _convert_tools(tools)
        if converted:
            a_body["tools"] = converted
    if a_tool_choice and not drop_tools and a_body.get("tools"):
        a_body["tool_choice"] = a_tool_choice

    # Silently drop unsupported params:
    # presence_penalty, frequency_penalty, logit_bias, logprobs, seed,
    # response_format, stream_options, n, parallel_tool_calls
    return a_body


def anthropic_to_chat_completion(a_body: dict, requested_model: str) -> dict:
    """Translate a non-streaming Anthropic Messages response into an OpenAI
    Chat Completions response."""
    if not isinstance(a_body, dict):
        a_body = {}

    content_blocks = a_body.get("content") or []
    text_parts: list[str] = []
    tool_calls: list[dict] = []
    for block in content_blocks:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text_parts.append(block.get("text", ""))
        elif btype == "tool_use":
            try:
                args_str = json.dumps(block.get("input", {}))
            except (TypeError, ValueError):
                args_str = "{}"
            tool_calls.append({
                "id": block.get("id", ""),
                "type": "function",
                "function": {
                    "name": block.get("name", ""),
                    "arguments": args_str,
                },
            })

    joined_text = "".join(text_parts)
    message: dict[str, Any] = {
        "role": "assistant",
        "content": joined_text if joined_text else None,
    }
    if tool_calls:
        message["tool_calls"] = tool_calls

    stop_reason_map = {
        "end_turn": "stop",
        "stop_sequence": "stop",
        "max_tokens": "length",
        "tool_use": "tool_calls",
    }
    finish_reason = stop_reason_map.get(a_body.get("stop_reason"), "stop")

    usage_src = a_body.get("usage") or {}
    prompt_tokens = int(usage_src.get("input_tokens") or 0)
    completion_tokens = int(usage_src.get("output_tokens") or 0)

    return {
        "id": f"chatcmpl-{a_body.get('id', '')}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": requested_model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


# ----------------------------------------------------------------------------
# SSE parsing helper
# ----------------------------------------------------------------------------


def _parse_sse_blocks(buffer: bytes) -> tuple[list[tuple[str | None, str]], bytes]:
    """Split buffer on b"\\n\\n", parse each block into (event, data_str).
    Returns (events, remainder_buffer)."""
    events: list[tuple[str | None, str]] = []
    parts = buffer.split(b"\n\n")
    # Last element is the (possibly incomplete) tail
    remainder = parts[-1]
    for raw_block in parts[:-1]:
        if not raw_block.strip():
            continue
        event_name: str | None = None
        data_lines: list[str] = []
        try:
            text = raw_block.decode("utf-8", errors="replace")
        except Exception:
            continue
        for line in text.split("\n"):
            line = line.rstrip("\r")
            if not line or line.startswith(":"):
                continue
            if line.startswith("event:"):
                event_name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:"):].lstrip())
        data_str = "\n".join(data_lines)
        events.append((event_name, data_str))
    return events, remainder


_STOP_REASON_MAP = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",
}


class AnthropicSSEToChatCompletionsStream:
    """State machine: Anthropic SSE event stream → OpenAI Chat Completions SSE chunks."""

    def __init__(self, requested_model: str, *, include_usage: bool = False):
        self.requested_model = requested_model
        self.include_usage = include_usage
        self.message_id: str | None = None
        self.created_ts: int = int(time.time())
        self.role_sent: bool = False
        self.block_types: dict[int, str] = {}
        self.block_to_tool_index: dict[int, int] = {}
        self.tool_index_counter: int = 0
        self.finish_emitted: bool = False
        self.done_emitted: bool = False
        self.sse_buffer: bytes = b""
        # Captured for include_usage emission
        self._final_stop_reason: str | None = None
        self._final_usage: dict[str, int] | None = None
        self._saw_message_delta: bool = False

    # -- internal helpers --

    def _wrap_chunk(self, delta: dict, finish_reason: str | None = None,
                    usage: dict | None = None) -> bytes:
        chunk: dict[str, Any] = {
            "id": self.message_id or "chatcmpl-unknown",
            "object": "chat.completion.chunk",
            "created": self.created_ts,
            "model": self.requested_model,
            "choices": [
                {
                    "index": 0,
                    "delta": delta,
                    "finish_reason": finish_reason,
                }
            ],
        }
        if usage is not None:
            chunk["usage"] = usage
        return b"data: " + json.dumps(chunk).encode("utf-8") + b"\n\n"

    def _handle_event(self, event_name: str | None, data_str: str) -> list[bytes]:
        if not data_str:
            return []
        # ping events sometimes have no JSON; skip
        if event_name == "ping":
            return []
        try:
            data = json.loads(data_str)
        except (json.JSONDecodeError, ValueError):
            return []

        evtype = event_name or data.get("type")

        out: list[bytes] = []

        if evtype == "message_start":
            msg = data.get("message") or {}
            aid = msg.get("id", "")
            self.message_id = f"chatcmpl-{aid}"
            # Capture upstream model if we didn't have one (we keep requested_model though)
            # Initial usage may appear here
            usage_src = msg.get("usage") or {}
            if usage_src:
                pin = int(usage_src.get("input_tokens") or 0)
                pout = int(usage_src.get("output_tokens") or 0)
                self._final_usage = {
                    "prompt_tokens": pin,
                    "completion_tokens": pout,
                    "total_tokens": pin + pout,
                }
            self.role_sent = True
            out.append(self._wrap_chunk({"role": "assistant"}))
            return out

        if evtype == "content_block_start":
            idx = data.get("index", 0)
            block = data.get("content_block") or {}
            btype = block.get("type")
            self.block_types[idx] = btype or ""
            if btype == "tool_use":
                tool_idx = self.tool_index_counter
                self.block_to_tool_index[idx] = tool_idx
                self.tool_index_counter += 1
                out.append(self._wrap_chunk({
                    "tool_calls": [
                        {
                            "index": tool_idx,
                            "id": block.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": block.get("name", ""),
                                "arguments": "",
                            },
                        }
                    ]
                }))
            # text: no output (lazy)
            return out

        if evtype == "content_block_delta":
            idx = data.get("index", 0)
            delta = data.get("delta") or {}
            dtype = delta.get("type")
            if dtype == "text_delta":
                text = delta.get("text", "")
                out.append(self._wrap_chunk({"content": text}))
            elif dtype == "input_json_delta":
                partial = delta.get("partial_json", "")
                tool_idx = self.block_to_tool_index.get(idx, 0)
                out.append(self._wrap_chunk({
                    "tool_calls": [
                        {
                            "index": tool_idx,
                            "function": {"arguments": partial},
                        }
                    ]
                }))
            return out

        if evtype == "content_block_stop":
            return out

        if evtype == "message_delta":
            self._saw_message_delta = True
            delta = data.get("delta") or {}
            stop_reason = delta.get("stop_reason")
            mapped = _STOP_REASON_MAP.get(stop_reason, "stop") if stop_reason else "stop"
            self._final_stop_reason = mapped
            # Update usage if present
            usage_src = data.get("usage") or {}
            if usage_src:
                # Anthropic message_delta typically contains output_tokens only
                pin = (self._final_usage or {}).get("prompt_tokens", 0)
                pout = int(usage_src.get("output_tokens") or
                           (self._final_usage or {}).get("completion_tokens", 0))
                if "input_tokens" in usage_src:
                    pin = int(usage_src.get("input_tokens") or 0)
                self._final_usage = {
                    "prompt_tokens": pin,
                    "completion_tokens": pout,
                    "total_tokens": pin + pout,
                }
            usage_payload = self._final_usage if self.include_usage else None
            out.append(self._wrap_chunk({}, finish_reason=mapped, usage=usage_payload))
            self.finish_emitted = True
            return out

        if evtype == "message_stop":
            if not self.done_emitted:
                out.append(b"data: [DONE]\n\n")
                self.done_emitted = True
            return out

        # error event
        if evtype == "error":
            # Surface error as a finish reason chunk + DONE
            if not self.finish_emitted:
                out.append(self._wrap_chunk({}, finish_reason="stop"))
                self.finish_emitted = True
            if not self.done_emitted:
                out.append(b"data: [DONE]\n\n")
                self.done_emitted = True
            return out

        # Unknown event: drop
        return out

    def feed(self, raw: bytes) -> list[bytes]:
        """Feed upstream raw bytes; return zero or more OpenAI SSE chunks."""
        if not raw:
            return []
        self.sse_buffer += raw
        events, remainder = _parse_sse_blocks(self.sse_buffer)
        self.sse_buffer = remainder
        out: list[bytes] = []
        for event_name, data_str in events:
            out.extend(self._handle_event(event_name, data_str))
        return out

    def flush(self) -> list[bytes]:
        """Drain remaining state. Ensure [DONE] is emitted if not already."""
        out: list[bytes] = []
        # Try to drain any complete event left in buffer (rare — usually buffer
        # holds a partial tail). If buffer ends without \n\n, we won't parse it.
        if self.sse_buffer:
            # Try to parse as if a trailing \n\n existed (best-effort)
            tail = self.sse_buffer + b"\n\n"
            events, _ = _parse_sse_blocks(tail)
            self.sse_buffer = b""
            for event_name, data_str in events:
                out.extend(self._handle_event(event_name, data_str))
        if not self.finish_emitted and not self._saw_message_delta:
            out.append(self._wrap_chunk({}, finish_reason="stop"))
            self.finish_emitted = True
        if not self.done_emitted:
            out.append(b"data: [DONE]\n\n")
            self.done_emitted = True
        return out


# ============================================================================
# Responses API ⇄ Anthropic
# ============================================================================


def _normalize_responses_input(input_field: Any) -> list[dict]:
    """Normalize Responses API `input` to OpenAI-chat-style messages list."""
    if input_field is None:
        return []
    if isinstance(input_field, str):
        return [{"role": "user", "content": input_field}]
    if not isinstance(input_field, list):
        return [{"role": "user", "content": str(input_field)}]

    out: list[dict] = []
    for item in input_field:
        if not isinstance(item, dict):
            out.append({"role": "user", "content": str(item)})
            continue

        itype = item.get("type")

        # Chat-style {role, content}
        if "role" in item and itype is None:
            content = item.get("content")
            if isinstance(content, list):
                # Parts may be {type:"input_text"/"output_text", text}
                normalized_parts: list[dict] = []
                for p in content:
                    if isinstance(p, dict) and p.get("type") in ("input_text", "output_text", "text"):
                        normalized_parts.append({"type": "text", "text": p.get("text", "")})
                    elif isinstance(p, dict) and p.get("type") in ("input_image", "image_url"):
                        normalized_parts.append(p)
                    elif isinstance(p, dict):
                        normalized_parts.append(p)
                    else:
                        normalized_parts.append({"type": "text", "text": str(p)})
                out.append({"role": item["role"], "content": normalized_parts})
            else:
                out.append({"role": item["role"], "content": content})
            continue

        # Item-style {type:"message", role, content}
        if itype == "message":
            content = item.get("content")
            if isinstance(content, list):
                normalized_parts = []
                for p in content:
                    if isinstance(p, dict) and p.get("type") in ("input_text", "output_text", "text"):
                        normalized_parts.append({"type": "text", "text": p.get("text", "")})
                    elif isinstance(p, dict):
                        normalized_parts.append(p)
                    else:
                        normalized_parts.append({"type": "text", "text": str(p)})
                out.append({"role": item.get("role", "user"), "content": normalized_parts})
            else:
                out.append({"role": item.get("role", "user"),
                            "content": content if content is not None else ""})
            continue

        if itype == "function_call":
            call_id = item.get("call_id") or item.get("id") or ""
            name = item.get("name", "")
            arguments = item.get("arguments", "")
            out.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }],
            })
            continue

        if itype == "function_call_output":
            call_id = item.get("call_id") or item.get("id") or ""
            output = item.get("output", "")
            out.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": output if isinstance(output, str) else json.dumps(output),
            })
            continue

        # Unknown item types — best-effort skip
    return out


def responses_request_to_anthropic(
    openai_body: dict, *, default_max_tokens: int = 4096
) -> dict:
    """Translate an OpenAI Responses API request body into an Anthropic
    Messages request body."""
    if not isinstance(openai_body, dict):
        raise UnsupportedParamError("request body must be a JSON object")

    if openai_body.get("previous_response_id"):
        raise UnsupportedParamError(
            "previous_response_id is not supported (no server-side persistence)",
            param="previous_response_id",
        )

    # Build chat-style messages from input
    messages: list[dict] = []

    instructions = openai_body.get("instructions")
    if instructions:
        messages.append({"role": "system", "content": instructions})

    messages.extend(_normalize_responses_input(openai_body.get("input")))

    # Build a chat_completions-compatible body, then call the existing translator.
    chat_body: dict[str, Any] = {
        "model": openai_body.get("model", ""),
        "messages": messages,
    }

    # max_output_tokens → max_tokens
    if "max_output_tokens" in openai_body and openai_body["max_output_tokens"] is not None:
        chat_body["max_tokens"] = openai_body["max_output_tokens"]
    elif "max_tokens" in openai_body and openai_body["max_tokens"] is not None:
        chat_body["max_tokens"] = openai_body["max_tokens"]

    for k in ("temperature", "top_p", "stream", "tools", "tool_choice",
              "stop", "user", "metadata"):
        if k in openai_body and openai_body[k] is not None:
            chat_body[k] = openai_body[k]

    return chat_completions_to_anthropic(chat_body, default_max_tokens=default_max_tokens)


def anthropic_to_responses_response(a_body: dict, requested_model: str) -> dict:
    """Translate a non-streaming Anthropic Messages response into an OpenAI
    Responses API response."""
    if not isinstance(a_body, dict):
        a_body = {}

    content_blocks = a_body.get("content") or []
    text_parts: list[str] = []
    tool_items: list[dict] = []
    for block in content_blocks:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text_parts.append(block.get("text", ""))
        elif btype == "tool_use":
            try:
                args_str = json.dumps(block.get("input", {}))
            except (TypeError, ValueError):
                args_str = "{}"
            tool_items.append({
                "type": "function_call",
                "call_id": block.get("id", ""),
                "name": block.get("name", ""),
                "arguments": args_str,
            })

    output: list[dict] = []
    joined_text = "".join(text_parts)
    msg_id = a_body.get("id", "")
    if joined_text:
        output.append({
            "type": "message",
            "id": f"msg_{msg_id}" if not msg_id.startswith("msg_") else msg_id,
            "role": "assistant",
            "content": [{"type": "output_text", "text": joined_text}],
        })
    output.extend(tool_items)

    usage_src = a_body.get("usage") or {}
    input_tokens = int(usage_src.get("input_tokens") or 0)
    output_tokens = int(usage_src.get("output_tokens") or 0)

    # Strip msg_ prefix if present to avoid double-prefix
    bare_id = msg_id.replace("msg_", "") if isinstance(msg_id, str) else ""

    return {
        "id": f"resp_{bare_id}",
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "model": requested_model,
        "output": output,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    }


class AnthropicSSEToResponsesStream:
    """State machine: Anthropic SSE → OpenAI Responses API SSE events."""

    def __init__(self, requested_model: str):
        self.requested_model = requested_model
        self.message_id: str | None = None
        self.response_id: str = f"resp_{uuid.uuid4().hex[:24]}"
        self.created_ts: int = int(time.time())
        self.sequence_number: int = 0
        self.output_index: int = 0
        self.content_index: int = 0
        self.block_types: dict[int, str] = {}
        self.block_to_output_index: dict[int, int] = {}
        # Per-block state: index → {kind, item_id, name?, accumulated_text/args}
        self._block_state: dict[int, dict] = {}
        self.created_emitted: bool = False
        self.sse_buffer: bytes = b""
        self.completed_emitted: bool = False
        # Accumulators for final response.completed event
        self._output_items_completed: list[dict] = []
        self._final_stop_reason: str | None = None
        self._final_usage: dict[str, int] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }

    # -- internal helpers --

    def _next_seq(self) -> int:
        n = self.sequence_number
        self.sequence_number += 1
        return n

    def _emit(self, event_name: str, payload: dict) -> bytes:
        payload = dict(payload)
        payload.setdefault("type", event_name)
        payload["sequence_number"] = self._next_seq()
        return (
            b"event: " + event_name.encode("utf-8") +
            b"\ndata: " + json.dumps(payload).encode("utf-8") +
            b"\n\n"
        )

    def _response_skeleton(self, status: str = "in_progress") -> dict:
        return {
            "id": self.response_id,
            "object": "response",
            "created_at": self.created_ts,
            "status": status,
            "model": self.requested_model,
            "output": [],
            "usage": None,
        }

    def _ensure_created(self) -> list[bytes]:
        if self.created_emitted:
            return []
        self.created_emitted = True
        skeleton = self._response_skeleton("in_progress")
        return [
            self._emit("response.created", {"response": skeleton}),
            self._emit("response.in_progress", {"response": skeleton}),
        ]

    def _handle_event(self, event_name: str | None, data_str: str) -> list[bytes]:
        if not data_str:
            return []
        if event_name == "ping":
            return []
        try:
            data = json.loads(data_str)
        except (json.JSONDecodeError, ValueError):
            return []

        evtype = event_name or data.get("type")
        out: list[bytes] = []

        if evtype == "message_start":
            msg = data.get("message") or {}
            self.message_id = msg.get("id", "")
            usage_src = msg.get("usage") or {}
            if usage_src:
                pin = int(usage_src.get("input_tokens") or 0)
                pout = int(usage_src.get("output_tokens") or 0)
                self._final_usage = {
                    "input_tokens": pin,
                    "output_tokens": pout,
                    "total_tokens": pin + pout,
                }
            out.extend(self._ensure_created())
            return out

        if evtype == "content_block_start":
            out.extend(self._ensure_created())
            idx = data.get("index", 0)
            block = data.get("content_block") or {}
            btype = block.get("type")
            self.block_types[idx] = btype or ""
            output_idx = self.output_index
            self.block_to_output_index[idx] = output_idx
            self.output_index += 1

            if btype == "text":
                item_id = f"msg_{self.message_id or uuid.uuid4().hex[:12]}_{output_idx}"
                self._block_state[idx] = {
                    "kind": "text",
                    "item_id": item_id,
                    "output_index": output_idx,
                    "content_index": 0,
                    "text": "",
                }
                item = {
                    "type": "message",
                    "id": item_id,
                    "status": "in_progress",
                    "role": "assistant",
                    "content": [],
                }
                out.append(self._emit("response.output_item.added", {
                    "output_index": output_idx,
                    "item": item,
                }))
                out.append(self._emit("response.content_part.added", {
                    "item_id": item_id,
                    "output_index": output_idx,
                    "content_index": 0,
                    "part": {"type": "output_text", "text": "", "annotations": []},
                }))
            elif btype == "tool_use":
                item_id = block.get("id", "") or f"call_{uuid.uuid4().hex[:12]}"
                name = block.get("name", "")
                self._block_state[idx] = {
                    "kind": "tool_use",
                    "item_id": item_id,
                    "output_index": output_idx,
                    "name": name,
                    "arguments": "",
                }
                item = {
                    "type": "function_call",
                    "id": item_id,
                    "call_id": item_id,
                    "name": name,
                    "arguments": "",
                    "status": "in_progress",
                }
                out.append(self._emit("response.output_item.added", {
                    "output_index": output_idx,
                    "item": item,
                }))
            return out

        if evtype == "content_block_delta":
            idx = data.get("index", 0)
            delta = data.get("delta") or {}
            dtype = delta.get("type")
            state = self._block_state.get(idx)
            if state is None:
                return out
            if dtype == "text_delta":
                text = delta.get("text", "")
                state["text"] += text
                out.append(self._emit("response.output_text.delta", {
                    "item_id": state["item_id"],
                    "output_index": state["output_index"],
                    "content_index": state.get("content_index", 0),
                    "delta": text,
                }))
            elif dtype == "input_json_delta":
                partial = delta.get("partial_json", "")
                state["arguments"] += partial
                out.append(self._emit("response.function_call_arguments.delta", {
                    "item_id": state["item_id"],
                    "output_index": state["output_index"],
                    "delta": partial,
                }))
            return out

        if evtype == "content_block_stop":
            idx = data.get("index", 0)
            state = self._block_state.get(idx)
            if state is None:
                return out
            if state["kind"] == "text":
                text = state["text"]
                item_id = state["item_id"]
                output_idx = state["output_index"]
                out.append(self._emit("response.output_text.done", {
                    "item_id": item_id,
                    "output_index": output_idx,
                    "content_index": state.get("content_index", 0),
                    "text": text,
                }))
                out.append(self._emit("response.content_part.done", {
                    "item_id": item_id,
                    "output_index": output_idx,
                    "content_index": state.get("content_index", 0),
                    "part": {"type": "output_text", "text": text, "annotations": []},
                }))
                completed_item = {
                    "type": "message",
                    "id": item_id,
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text, "annotations": []}],
                }
                out.append(self._emit("response.output_item.done", {
                    "output_index": output_idx,
                    "item": completed_item,
                }))
                self._output_items_completed.append(completed_item)
            elif state["kind"] == "tool_use":
                args = state["arguments"]
                item_id = state["item_id"]
                output_idx = state["output_index"]
                out.append(self._emit("response.function_call_arguments.done", {
                    "item_id": item_id,
                    "output_index": output_idx,
                    "arguments": args,
                }))
                completed_item = {
                    "type": "function_call",
                    "id": item_id,
                    "call_id": item_id,
                    "name": state.get("name", ""),
                    "arguments": args,
                    "status": "completed",
                }
                out.append(self._emit("response.output_item.done", {
                    "output_index": output_idx,
                    "item": completed_item,
                }))
                self._output_items_completed.append(completed_item)
            return out

        if evtype == "message_delta":
            delta = data.get("delta") or {}
            stop_reason = delta.get("stop_reason")
            if stop_reason:
                self._final_stop_reason = _STOP_REASON_MAP.get(stop_reason, "stop")
            usage_src = data.get("usage") or {}
            if usage_src:
                pin = self._final_usage.get("input_tokens", 0)
                if "input_tokens" in usage_src:
                    pin = int(usage_src.get("input_tokens") or 0)
                pout = int(usage_src.get("output_tokens") or
                           self._final_usage.get("output_tokens", 0))
                self._final_usage = {
                    "input_tokens": pin,
                    "output_tokens": pout,
                    "total_tokens": pin + pout,
                }
            return out

        if evtype == "message_stop":
            if not self.completed_emitted:
                final_response = {
                    "id": self.response_id,
                    "object": "response",
                    "created_at": self.created_ts,
                    "status": "completed",
                    "model": self.requested_model,
                    "output": list(self._output_items_completed),
                    "usage": dict(self._final_usage),
                }
                out.append(self._emit("response.completed", {"response": final_response}))
                self.completed_emitted = True
            return out

        if evtype == "error":
            if not self.completed_emitted:
                err_payload = data.get("error") or {}
                out.append(self._emit("response.failed", {
                    "response": {
                        "id": self.response_id,
                        "object": "response",
                        "status": "failed",
                        "error": err_payload,
                    }
                }))
                self.completed_emitted = True
            return out

        return out

    def feed(self, raw: bytes) -> list[bytes]:
        if not raw:
            return []
        self.sse_buffer += raw
        events, remainder = _parse_sse_blocks(self.sse_buffer)
        self.sse_buffer = remainder
        out: list[bytes] = []
        for event_name, data_str in events:
            out.extend(self._handle_event(event_name, data_str))
        return out

    def flush(self) -> list[bytes]:
        out: list[bytes] = []
        if self.sse_buffer:
            tail = self.sse_buffer + b"\n\n"
            events, _ = _parse_sse_blocks(tail)
            self.sse_buffer = b""
            for event_name, data_str in events:
                out.extend(self._handle_event(event_name, data_str))
        if not self.completed_emitted:
            final_response = {
                "id": self.response_id,
                "object": "response",
                "created_at": self.created_ts,
                "status": "completed",
                "model": self.requested_model,
                "output": list(self._output_items_completed),
                "usage": dict(self._final_usage),
            }
            out.append(self._emit("response.completed", {"response": final_response}))
            self.completed_emitted = True
        return out


# ============================================================================
# Error response translation
# ============================================================================


def anthropic_error_to_openai_error(
    a_err: dict | bytes | str, target_shape: str
) -> dict:
    """Translate an Anthropic error body to OpenAI error body shape.

    Tolerant: accepts dict, bytes, or str; falls back to using raw content
    as message if structured parsing fails.
    """
    parsed: Any = None
    raw_text: str = ""

    if isinstance(a_err, dict):
        parsed = a_err
    elif isinstance(a_err, (bytes, bytearray)):
        try:
            raw_text = bytes(a_err).decode("utf-8", errors="replace")
        except Exception:
            raw_text = repr(a_err)
        try:
            parsed = json.loads(raw_text)
        except (json.JSONDecodeError, ValueError):
            parsed = None
    elif isinstance(a_err, str):
        raw_text = a_err
        try:
            parsed = json.loads(a_err)
        except (json.JSONDecodeError, ValueError):
            parsed = None
    else:
        raw_text = str(a_err)
        parsed = None

    message: str = ""
    err_type: str = "upstream_error"

    if isinstance(parsed, dict):
        err_obj = parsed.get("error")
        if isinstance(err_obj, dict):
            message = str(err_obj.get("message") or "")
            err_type = str(err_obj.get("type") or "upstream_error")
        else:
            # Maybe already in OpenAI shape, or unknown
            if "message" in parsed:
                message = str(parsed.get("message") or "")
            if "type" in parsed and isinstance(parsed.get("type"), str):
                t = parsed.get("type")
                if t and t != "error":
                    err_type = t

    if not message:
        message = raw_text or "upstream error"

    result = {
        "error": {
            "message": message,
            "type": err_type,
            "code": None,
            "param": None,
        }
    }
    # Informational tag for debugging — does not affect SDK parsing
    if target_shape:
        result["error"]["request_type"] = target_shape
    return result


# ============================================================================
# Public exports
# ============================================================================

__all__ = [
    "UnsupportedParamError",
    "chat_completions_to_anthropic",
    "anthropic_to_chat_completion",
    "AnthropicSSEToChatCompletionsStream",
    "responses_request_to_anthropic",
    "anthropic_to_responses_response",
    "AnthropicSSEToResponsesStream",
    "anthropic_error_to_openai_error",
]
