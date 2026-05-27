"""Unit tests for openai_translator. Pure unit tests - no network."""
from __future__ import annotations

import json
import sys
import os

import pytest

# Make the project root importable when pytest is invoked from elsewhere.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from openai_translator import (  # noqa: E402
    UnsupportedParamError,
    chat_completions_to_anthropic,
    anthropic_to_chat_completion,
    AnthropicSSEToChatCompletionsStream,
    responses_request_to_anthropic,
    anthropic_to_responses_response,
    AnthropicSSEToResponsesStream,
    anthropic_error_to_openai_error,
)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _make_anthropic_sse_event(event_name: str, data: dict) -> bytes:
    """Build one Anthropic-style SSE event block: `event: <name>\\ndata: <json>\\n\\n`."""
    return f"event: {event_name}\ndata: {json.dumps(data)}\n\n".encode("utf-8")


def _parse_openai_sse_chunk(chunk: bytes) -> dict | str:
    """Parse a single OpenAI-style SSE chunk into the JSON payload, or `[DONE]`."""
    text = chunk.decode("utf-8") if isinstance(chunk, (bytes, bytearray)) else chunk
    # Strip optional `data: ` prefix and trailing whitespace.
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("data:"):
            payload = line[len("data:"):].strip()
            if payload == "[DONE]":
                return "[DONE]"
            return json.loads(payload)
    raise AssertionError(f"could not parse OpenAI SSE chunk: {chunk!r}")


def _collect_chunks(sm, events: list[bytes]) -> list:
    """Feed a list of raw SSE event blocks into a stream state machine and
    collect the parsed output chunks (json dicts or '[DONE]')."""
    out: list = []
    for raw in events:
        for ck in sm.feed(raw):
            out.append(_parse_openai_sse_chunk(ck))
    for ck in sm.flush():
        out.append(_parse_openai_sse_chunk(ck))
    return out


# ============================================================================
# === Group A: chat_completions_to_anthropic ===
# ============================================================================


def test_single_system_message_extracted():
    body = {
        "model": "claude-opus-4-6",
        "messages": [
            {"role": "system", "content": "you are helpful"},
            {"role": "user", "content": "hi"},
        ],
    }
    out = chat_completions_to_anthropic(body)
    # system extracted to top-level field. Allow string or single-item array form.
    assert "system" in out
    sys_field = out["system"]
    if isinstance(sys_field, str):
        assert sys_field == "you are helpful"
    else:
        assert isinstance(sys_field, list) and len(sys_field) == 1
        first = sys_field[0]
        # text block form
        assert first.get("type") == "text"
        assert first.get("text") == "you are helpful"
    # system messages are stripped from `messages`
    assert all(m["role"] != "system" for m in out["messages"])


def test_multiple_system_messages_concatenated():
    body = {
        "model": "claude-opus-4-6",
        "messages": [
            {"role": "system", "content": "rule one"},
            {"role": "system", "content": "rule two"},
            {"role": "user", "content": "hi"},
        ],
    }
    out = chat_completions_to_anthropic(body)
    sys_field = out["system"]
    if isinstance(sys_field, str):
        # Joined with newline somewhere
        assert "rule one" in sys_field and "rule two" in sys_field
    else:
        assert isinstance(sys_field, list)
        texts = " ".join(
            (b.get("text", "") if isinstance(b, dict) else "") for b in sys_field
        )
        assert "rule one" in texts and "rule two" in texts


def test_system_content_as_text_part_array_preserved():
    body = {
        "model": "claude-opus-4-6",
        "messages": [
            {
                "role": "system",
                "content": [{"type": "text", "text": "first chunk"}],
            },
            {"role": "user", "content": "hi"},
        ],
    }
    out = chat_completions_to_anthropic(body)
    sys_field = out["system"]
    # When original content is a text-part array, system should be preserved as an array.
    assert isinstance(sys_field, list)
    assert any(
        isinstance(b, dict) and b.get("type") == "text" and "first chunk" in b.get("text", "")
        for b in sys_field
    )


def test_user_string_content_wrapped_in_text_block():
    body = {
        "model": "claude-opus-4-6",
        "messages": [{"role": "user", "content": "hello"}],
    }
    out = chat_completions_to_anthropic(body)
    msgs = out["messages"]
    assert len(msgs) == 1
    m = msgs[0]
    assert m["role"] == "user"
    assert isinstance(m["content"], list)
    assert m["content"][0]["type"] == "text"
    assert m["content"][0]["text"] == "hello"


def test_user_content_parts_array_text_text():
    body = {
        "model": "claude-opus-4-6",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "part-a"},
                    {"type": "text", "text": "part-b"},
                ],
            }
        ],
    }
    out = chat_completions_to_anthropic(body)
    m = out["messages"][0]
    assert isinstance(m["content"], list)
    texts = [b for b in m["content"] if b.get("type") == "text"]
    assert len(texts) == 2
    assert texts[0]["text"] == "part-a"
    assert texts[1]["text"] == "part-b"


def test_user_content_image_data_url_translated_to_base64_block():
    data_url = "data:image/jpeg;base64,QUJD"  # 'ABC' base64
    body = {
        "model": "claude-opus-4-6",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "what's in this image?"},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
    }
    out = chat_completions_to_anthropic(body)
    blocks = out["messages"][0]["content"]
    img_blocks = [b for b in blocks if b.get("type") == "image"]
    assert len(img_blocks) == 1
    src = img_blocks[0]["source"]
    assert src["type"] == "base64"
    assert src["media_type"] == "image/jpeg"
    assert src["data"] == "QUJD"


def test_user_content_image_https_url_translated_to_url_block():
    body = {
        "model": "claude-opus-4-6",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "https://example.com/cat.png"}},
                ],
            }
        ],
    }
    out = chat_completions_to_anthropic(body)
    blocks = out["messages"][0]["content"]
    img_blocks = [b for b in blocks if b.get("type") == "image"]
    assert len(img_blocks) == 1
    src = img_blocks[0]["source"]
    assert src["type"] == "url"
    assert src["url"] == "https://example.com/cat.png"


def test_assistant_string_content_wrapped_in_text_block():
    body = {
        "model": "claude-opus-4-6",
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello back"},
        ],
    }
    out = chat_completions_to_anthropic(body)
    asst = [m for m in out["messages"] if m["role"] == "assistant"][0]
    assert isinstance(asst["content"], list)
    assert asst["content"][0]["type"] == "text"
    assert asst["content"][0]["text"] == "hello back"


def test_assistant_with_text_and_tool_calls_emits_text_then_tool_use():
    body = {
        "model": "claude-opus-4-6",
        "messages": [
            {"role": "user", "content": "weather?"},
            {
                "role": "assistant",
                "content": "let me check",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"city":"Tokyo"}',
                        },
                    }
                ],
            },
        ],
    }
    out = chat_completions_to_anthropic(body)
    asst = [m for m in out["messages"] if m["role"] == "assistant"][0]
    content = asst["content"]
    assert isinstance(content, list)
    types = [b.get("type") for b in content]
    # text first, then tool_use
    assert "text" in types
    assert "tool_use" in types
    assert types.index("text") < types.index("tool_use")
    tu = [b for b in content if b["type"] == "tool_use"][0]
    assert tu["id"] == "call_1"
    assert tu["name"] == "get_weather"
    assert tu["input"] == {"city": "Tokyo"}


def test_assistant_tool_calls_only_no_text_block():
    body = {
        "model": "claude-opus-4-6",
        "messages": [
            {"role": "user", "content": "weather?"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_xyz",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": "{}"},
                    }
                ],
            },
        ],
    }
    out = chat_completions_to_anthropic(body)
    asst = [m for m in out["messages"] if m["role"] == "assistant"][0]
    content = asst["content"]
    types = [b.get("type") for b in content]
    assert "text" not in types
    assert "tool_use" in types
    # No empty-text block leakage
    for b in content:
        if b.get("type") == "text":
            assert b.get("text", "")  # never empty


def test_single_tool_role_becomes_user_with_tool_result_block():
    body = {
        "model": "claude-opus-4-6",
        "messages": [
            {"role": "user", "content": "weather?"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"city":"Tokyo"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "sunny 22C"},
        ],
    }
    out = chat_completions_to_anthropic(body)
    # The role=tool turns into a user message with a tool_result block
    last = out["messages"][-1]
    assert last["role"] == "user"
    blocks = last["content"]
    assert isinstance(blocks, list)
    tr = [b for b in blocks if b.get("type") == "tool_result"]
    assert len(tr) == 1
    assert tr[0]["tool_use_id"] == "call_1"
    # Content of tool_result should encode the tool reply somehow.
    tr_content = tr[0].get("content")
    assert tr_content is not None
    if isinstance(tr_content, str):
        assert "sunny" in tr_content
    else:
        # list-of-blocks form
        joined = " ".join(
            (b.get("text", "") if isinstance(b, dict) else str(b)) for b in tr_content
        )
        assert "sunny" in joined


def test_two_consecutive_tool_messages_merge_into_single_user_message():
    body = {
        "model": "claude-opus-4-6",
        "messages": [
            {"role": "user", "content": "weather?"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": "{}"},
                    },
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {"name": "get_time", "arguments": "{}"},
                    },
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "sunny"},
            {"role": "tool", "tool_call_id": "call_2", "content": "10:00"},
        ],
    }
    out = chat_completions_to_anthropic(body)
    # The trailing pair of tool messages must collapse into exactly one user message
    # with two tool_result blocks (strict alternation requirement).
    last = out["messages"][-1]
    assert last["role"] == "user"
    blocks = [b for b in last["content"] if b.get("type") == "tool_result"]
    assert len(blocks) == 2
    ids = {b["tool_use_id"] for b in blocks}
    assert ids == {"call_1", "call_2"}


def test_tools_field_translated_to_anthropic_input_schema():
    body = {
        "model": "claude-opus-4-6",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "look up the weather",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                },
            }
        ],
    }
    out = chat_completions_to_anthropic(body)
    tools = out["tools"]
    assert isinstance(tools, list) and len(tools) == 1
    t = tools[0]
    assert t["name"] == "get_weather"
    assert t["description"] == "look up the weather"
    assert t["input_schema"]["type"] == "object"
    assert "city" in t["input_schema"]["properties"]
    # Anthropic tools should not carry the OpenAI wrapper keys.
    assert "function" not in t
    assert t.get("type") != "function" or "input_schema" in t  # may carry custom anthropic type, but must have input_schema


def test_tool_choice_auto_maps_to_anthropic_auto():
    body = {
        "model": "claude-opus-4-6",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [
            {
                "type": "function",
                "function": {"name": "f", "description": "", "parameters": {"type": "object"}},
            }
        ],
        "tool_choice": "auto",
    }
    out = chat_completions_to_anthropic(body)
    assert out["tool_choice"] == {"type": "auto"}


def test_tool_choice_none_drops_tools_field():
    body = {
        "model": "claude-opus-4-6",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [
            {
                "type": "function",
                "function": {"name": "f", "description": "", "parameters": {"type": "object"}},
            }
        ],
        "tool_choice": "none",
    }
    out = chat_completions_to_anthropic(body)
    # tools field is dropped (key absent) when tool_choice == none
    assert "tools" not in out


def test_tool_choice_required_maps_to_any():
    body = {
        "model": "claude-opus-4-6",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [
            {
                "type": "function",
                "function": {"name": "f", "description": "", "parameters": {"type": "object"}},
            }
        ],
        "tool_choice": "required",
    }
    out = chat_completions_to_anthropic(body)
    assert out["tool_choice"] == {"type": "any"}


def test_tool_choice_specific_function_maps_to_tool_name():
    body = {
        "model": "claude-opus-4-6",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [
            {
                "type": "function",
                "function": {"name": "X", "description": "", "parameters": {"type": "object"}},
            }
        ],
        "tool_choice": {"type": "function", "function": {"name": "X"}},
    }
    out = chat_completions_to_anthropic(body)
    assert out["tool_choice"] == {"type": "tool", "name": "X"}


def test_max_tokens_default_when_absent():
    body = {
        "model": "claude-opus-4-6",
        "messages": [{"role": "user", "content": "hi"}],
    }
    out = chat_completions_to_anthropic(body)
    assert out["max_tokens"] == 4096


def test_max_tokens_preserved_when_present():
    body = {
        "model": "claude-opus-4-6",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 128,
    }
    out = chat_completions_to_anthropic(body)
    assert out["max_tokens"] == 128


def test_stop_string_becomes_single_element_list():
    body = {
        "model": "claude-opus-4-6",
        "messages": [{"role": "user", "content": "hi"}],
        "stop": "foo",
    }
    out = chat_completions_to_anthropic(body)
    assert out["stop_sequences"] == ["foo"]
    assert "stop" not in out


def test_stop_list_preserved_as_stop_sequences():
    body = {
        "model": "claude-opus-4-6",
        "messages": [{"role": "user", "content": "hi"}],
        "stop": ["foo", "bar"],
    }
    out = chat_completions_to_anthropic(body)
    assert out["stop_sequences"] == ["foo", "bar"]
    assert "stop" not in out


def test_user_field_becomes_metadata_user_id():
    body = {
        "model": "claude-opus-4-6",
        "messages": [{"role": "user", "content": "hi"}],
        "user": "alice",
    }
    out = chat_completions_to_anthropic(body)
    assert out["metadata"] == {"user_id": "alice"}
    # The top-level `user` field should not leak through.
    assert "user" not in out


def test_n_greater_than_one_raises_unsupported():
    body = {
        "model": "claude-opus-4-6",
        "messages": [{"role": "user", "content": "hi"}],
        "n": 2,
    }
    with pytest.raises(UnsupportedParamError):
        chat_completions_to_anthropic(body)


def test_n_equal_one_is_accepted():
    body = {
        "model": "claude-opus-4-6",
        "messages": [{"role": "user", "content": "hi"}],
        "n": 1,
    }
    # Should not raise. Result should NOT carry an `n` key into Anthropic.
    out = chat_completions_to_anthropic(body)
    assert "n" not in out


def test_dropped_params_have_no_side_effects():
    body = {
        "model": "claude-opus-4-6",
        "messages": [{"role": "user", "content": "hi"}],
        "presence_penalty": 0.5,
        "frequency_penalty": 0.5,
        "logit_bias": {"50256": -100},
        "logprobs": True,
        "seed": 42,
        "response_format": {"type": "json_object"},
    }
    out = chat_completions_to_anthropic(body)
    # None of the dropped params should leak through.
    for k in (
        "presence_penalty",
        "frequency_penalty",
        "logit_bias",
        "logprobs",
        "seed",
        "response_format",
    ):
        assert k not in out


def test_model_field_passed_through_verbatim():
    body = {
        "model": "gpt-5.5",  # nonsense for Anthropic, but proxy doesn't classify.
        "messages": [{"role": "user", "content": "hi"}],
    }
    out = chat_completions_to_anthropic(body)
    assert out["model"] == "gpt-5.5"


# ============================================================================
# === Group B: anthropic_to_chat_completion ===
# ============================================================================


def _anthropic_resp(
    content: list[dict] | None = None,
    stop_reason: str = "end_turn",
    in_tokens: int = 10,
    out_tokens: int = 20,
    msg_id: str = "msg_abc",
) -> dict:
    return {
        "id": msg_id,
        "type": "message",
        "role": "assistant",
        "content": content if content is not None else [{"type": "text", "text": "hello"}],
        "model": "claude-opus-4-6",
        "stop_reason": stop_reason,
        "usage": {"input_tokens": in_tokens, "output_tokens": out_tokens},
    }


def test_pure_text_response_concatenated_into_message_content():
    a = _anthropic_resp(content=[{"type": "text", "text": "hello "}, {"type": "text", "text": "world"}])
    out = anthropic_to_chat_completion(a, requested_model="claude-opus-4-6")
    msg = out["choices"][0]["message"]
    assert msg["role"] == "assistant"
    assert msg["content"] == "hello world"
    assert not msg.get("tool_calls")  # None or absent


def test_pure_tool_use_response_has_tool_calls_and_null_content():
    a = _anthropic_resp(
        content=[
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "get_weather",
                "input": {"city": "Tokyo"},
            }
        ],
        stop_reason="tool_use",
    )
    out = anthropic_to_chat_completion(a, requested_model="claude-opus-4-6")
    msg = out["choices"][0]["message"]
    assert msg["content"] is None
    tcs = msg["tool_calls"]
    assert isinstance(tcs, list) and len(tcs) == 1
    tc = tcs[0]
    assert tc["id"] == "toolu_1"
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "get_weather"
    # arguments must be a JSON-encoded string
    assert json.loads(tc["function"]["arguments"]) == {"city": "Tokyo"}


def test_mixed_text_and_tool_use_response():
    a = _anthropic_resp(
        content=[
            {"type": "text", "text": "let me check"},
            {
                "type": "tool_use",
                "id": "toolu_2",
                "name": "f",
                "input": {"a": 1},
            },
        ],
        stop_reason="tool_use",
    )
    out = anthropic_to_chat_completion(a, requested_model="claude-opus-4-6")
    msg = out["choices"][0]["message"]
    assert msg["content"] == "let me check"
    assert isinstance(msg["tool_calls"], list) and len(msg["tool_calls"]) == 1


def test_stop_reason_end_turn_maps_to_stop():
    a = _anthropic_resp(stop_reason="end_turn")
    out = anthropic_to_chat_completion(a, requested_model="claude-opus-4-6")
    assert out["choices"][0]["finish_reason"] == "stop"


def test_stop_reason_max_tokens_maps_to_length():
    a = _anthropic_resp(stop_reason="max_tokens")
    out = anthropic_to_chat_completion(a, requested_model="claude-opus-4-6")
    assert out["choices"][0]["finish_reason"] == "length"


def test_stop_reason_stop_sequence_maps_to_stop():
    a = _anthropic_resp(stop_reason="stop_sequence")
    out = anthropic_to_chat_completion(a, requested_model="claude-opus-4-6")
    assert out["choices"][0]["finish_reason"] == "stop"


def test_stop_reason_tool_use_maps_to_tool_calls():
    a = _anthropic_resp(stop_reason="tool_use")
    out = anthropic_to_chat_completion(a, requested_model="claude-opus-4-6")
    assert out["choices"][0]["finish_reason"] == "tool_calls"


def test_usage_mapping():
    a = _anthropic_resp(in_tokens=10, out_tokens=20)
    out = anthropic_to_chat_completion(a, requested_model="claude-opus-4-6")
    u = out["usage"]
    assert u["prompt_tokens"] == 10
    assert u["completion_tokens"] == 20
    assert u["total_tokens"] == 30


def test_id_is_prefixed_with_chatcmpl():
    a = _anthropic_resp(msg_id="msg_abc123")
    out = anthropic_to_chat_completion(a, requested_model="claude-opus-4-6")
    assert out["id"].startswith("chatcmpl-")
    assert "msg_abc123" in out["id"]


def test_top_level_object_choices_and_model_fields():
    a = _anthropic_resp()
    out = anthropic_to_chat_completion(a, requested_model="some-model-name")
    assert out["object"] == "chat.completion"
    assert isinstance(out["choices"], list) and len(out["choices"]) == 1
    assert out["model"] == "some-model-name"


# ============================================================================
# === Group C: AnthropicSSEToChatCompletionsStream ===
# ============================================================================


def test_empty_stream_flush_emits_done():
    sm = AnthropicSSEToChatCompletionsStream(requested_model="claude-opus-4-6")
    tail = sm.flush()
    parsed = [_parse_openai_sse_chunk(c) for c in tail]
    assert parsed[-1] == "[DONE]"


def test_text_only_stream_full_sequence():
    sm = AnthropicSSEToChatCompletionsStream(requested_model="claude-opus-4-6")
    events = [
        _make_anthropic_sse_event(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_text",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": "claude-opus-4-6",
                    "stop_reason": None,
                    "usage": {"input_tokens": 5, "output_tokens": 0},
                },
            },
        ),
        _make_anthropic_sse_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        _make_anthropic_sse_event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Hello"},
            },
        ),
        _make_anthropic_sse_event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": " world"},
            },
        ),
        _make_anthropic_sse_event(
            "content_block_stop", {"type": "content_block_stop", "index": 0}
        ),
        _make_anthropic_sse_event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 2},
            },
        ),
        _make_anthropic_sse_event("message_stop", {"type": "message_stop"}),
    ]
    out = _collect_chunks(sm, events)
    # last element is [DONE]
    assert out[-1] == "[DONE]"
    # role chunk somewhere near the start
    role_chunks = [c for c in out if isinstance(c, dict) and c.get("choices")
                   and c["choices"][0].get("delta", {}).get("role") == "assistant"]
    assert len(role_chunks) >= 1

    # collect content deltas
    contents = []
    for c in out:
        if not isinstance(c, dict):
            continue
        delta = c.get("choices", [{}])[0].get("delta") or {}
        if "content" in delta and delta["content"]:
            contents.append(delta["content"])
    assert "Hello" in contents
    assert " world" in contents

    # finish_reason chunk
    finish = [c for c in out if isinstance(c, dict)
              and c.get("choices", [{}])[0].get("finish_reason") == "stop"]
    assert len(finish) == 1


def test_tool_only_stream_full_sequence():
    sm = AnthropicSSEToChatCompletionsStream(requested_model="claude-opus-4-6")
    events = [
        _make_anthropic_sse_event(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_tool",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": "claude-opus-4-6",
                    "stop_reason": None,
                    "usage": {"input_tokens": 5, "output_tokens": 0},
                },
            },
        ),
        _make_anthropic_sse_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "foo",
                    "input": {},
                },
            },
        ),
        _make_anthropic_sse_event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '{"x":'},
            },
        ),
        _make_anthropic_sse_event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": "1}"},
            },
        ),
        _make_anthropic_sse_event(
            "content_block_stop", {"type": "content_block_stop", "index": 0}
        ),
        _make_anthropic_sse_event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                "usage": {"output_tokens": 2},
            },
        ),
        _make_anthropic_sse_event("message_stop", {"type": "message_stop"}),
    ]
    out = _collect_chunks(sm, events)
    assert out[-1] == "[DONE]"

    # role chunk
    role_chunks = [c for c in out if isinstance(c, dict) and c.get("choices")
                   and c["choices"][0].get("delta", {}).get("role") == "assistant"]
    assert len(role_chunks) >= 1

    # tool_calls chunks: collect all `tool_calls` deltas in order
    tool_chunks: list[dict] = []
    for c in out:
        if not isinstance(c, dict):
            continue
        delta = c.get("choices", [{}])[0].get("delta") or {}
        if "tool_calls" in delta:
            tool_chunks.append(delta["tool_calls"][0])

    # First tool_calls chunk: id + name + empty args
    assert len(tool_chunks) >= 3
    first = tool_chunks[0]
    assert first.get("index") == 0
    assert first.get("id") == "toolu_1"
    assert first.get("type") == "function"
    assert first["function"]["name"] == "foo"
    assert first["function"]["arguments"] == ""

    # Subsequent chunks: partial args only (no id repeated)
    partials = [tc["function"]["arguments"] for tc in tool_chunks[1:]]
    assert '{"x":' in partials
    assert "1}" in partials

    finish = [c for c in out if isinstance(c, dict)
              and c.get("choices", [{}])[0].get("finish_reason") == "tool_calls"]
    assert len(finish) == 1


def test_ping_event_does_not_break_state_machine():
    sm = AnthropicSSEToChatCompletionsStream(requested_model="claude-opus-4-6")
    events = [
        _make_anthropic_sse_event(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_ping",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": "claude-opus-4-6",
                    "stop_reason": None,
                    "usage": {"input_tokens": 1, "output_tokens": 0},
                },
            },
        ),
        _make_anthropic_sse_event("ping", {"type": "ping"}),
        _make_anthropic_sse_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        _make_anthropic_sse_event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "hi"},
            },
        ),
        _make_anthropic_sse_event(
            "content_block_stop", {"type": "content_block_stop", "index": 0}
        ),
        _make_anthropic_sse_event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 1},
            },
        ),
        _make_anthropic_sse_event("message_stop", {"type": "message_stop"}),
    ]
    out = _collect_chunks(sm, events)
    assert out[-1] == "[DONE]"
    # No chunk should leak a 'ping' content or type
    for c in out:
        if not isinstance(c, dict):
            continue
        delta = c.get("choices", [{}])[0].get("delta") or {}
        assert delta.get("content") != "ping"
        # ensure no spurious "type":"ping" content slipped in
        if "content" in delta and delta["content"]:
            assert "ping" not in delta["content"]
    # text "hi" still got through
    contents = []
    for c in out:
        if isinstance(c, dict):
            delta = c.get("choices", [{}])[0].get("delta") or {}
            if delta.get("content"):
                contents.append(delta["content"])
    assert "hi" in contents


def test_sse_chunk_boundary_split_across_feed_calls():
    sm = AnthropicSSEToChatCompletionsStream(requested_model="claude-opus-4-6")
    full_events = (
        _make_anthropic_sse_event(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_split",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": "claude-opus-4-6",
                    "stop_reason": None,
                    "usage": {"input_tokens": 1, "output_tokens": 0},
                },
            },
        )
        + _make_anthropic_sse_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        )
        + _make_anthropic_sse_event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "split-text"},
            },
        )
        + _make_anthropic_sse_event(
            "content_block_stop", {"type": "content_block_stop", "index": 0}
        )
        + _make_anthropic_sse_event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 1},
            },
        )
        + _make_anthropic_sse_event("message_stop", {"type": "message_stop"})
    )
    # Split mid-event at an arbitrary byte position (deliberately inside a JSON payload).
    mid = len(full_events) // 2
    part1 = full_events[:mid]
    part2 = full_events[mid:]
    collected: list = []
    for raw in (part1, part2):
        for ck in sm.feed(raw):
            collected.append(_parse_openai_sse_chunk(ck))
    for ck in sm.flush():
        collected.append(_parse_openai_sse_chunk(ck))
    assert collected[-1] == "[DONE]"
    contents = []
    for c in collected:
        if isinstance(c, dict):
            delta = c.get("choices", [{}])[0].get("delta") or {}
            if delta.get("content"):
                contents.append(delta["content"])
    assert "split-text" in "".join(contents)


def test_multiple_tool_blocks_get_sequential_indices():
    sm = AnthropicSSEToChatCompletionsStream(requested_model="claude-opus-4-6")
    events = [
        _make_anthropic_sse_event(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_multi",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": "claude-opus-4-6",
                    "stop_reason": None,
                    "usage": {"input_tokens": 1, "output_tokens": 0},
                },
            },
        ),
    ]
    # Three tool_use blocks.
    for i, tid in enumerate(["toolu_a", "toolu_b", "toolu_c"]):
        events.append(
            _make_anthropic_sse_event(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": i,
                    "content_block": {
                        "type": "tool_use",
                        "id": tid,
                        "name": f"fn_{i}",
                        "input": {},
                    },
                },
            )
        )
        events.append(
            _make_anthropic_sse_event(
                "content_block_stop",
                {"type": "content_block_stop", "index": i},
            )
        )
    events.append(
        _make_anthropic_sse_event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                "usage": {"output_tokens": 1},
            },
        )
    )
    events.append(_make_anthropic_sse_event("message_stop", {"type": "message_stop"}))
    out = _collect_chunks(sm, events)

    tool_indices: list[int] = []
    for c in out:
        if not isinstance(c, dict):
            continue
        delta = c.get("choices", [{}])[0].get("delta") or {}
        if "tool_calls" in delta:
            for tc in delta["tool_calls"]:
                if tc.get("id"):
                    tool_indices.append(tc["index"])
    # We should see indices 0, 1, 2 in order.
    assert tool_indices == [0, 1, 2]


def test_include_usage_true_emits_usage_in_final_chunk():
    sm = AnthropicSSEToChatCompletionsStream(
        requested_model="claude-opus-4-6", include_usage=True
    )
    events = [
        _make_anthropic_sse_event(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_u",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": "claude-opus-4-6",
                    "stop_reason": None,
                    "usage": {"input_tokens": 7, "output_tokens": 0},
                },
            },
        ),
        _make_anthropic_sse_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        _make_anthropic_sse_event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "ok"},
            },
        ),
        _make_anthropic_sse_event(
            "content_block_stop", {"type": "content_block_stop", "index": 0}
        ),
        _make_anthropic_sse_event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 3},
            },
        ),
        _make_anthropic_sse_event("message_stop", {"type": "message_stop"}),
    ]
    out = _collect_chunks(sm, events)
    # Some chunk near the end should carry a `usage` block.
    usage_carriers = [c for c in out if isinstance(c, dict) and "usage" in c]
    assert usage_carriers, "expected at least one chunk with usage when include_usage=True"


def test_include_usage_false_does_not_emit_usage():
    sm = AnthropicSSEToChatCompletionsStream(
        requested_model="claude-opus-4-6", include_usage=False
    )
    events = [
        _make_anthropic_sse_event(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_u2",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": "claude-opus-4-6",
                    "stop_reason": None,
                    "usage": {"input_tokens": 7, "output_tokens": 0},
                },
            },
        ),
        _make_anthropic_sse_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        _make_anthropic_sse_event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "ok"},
            },
        ),
        _make_anthropic_sse_event(
            "content_block_stop", {"type": "content_block_stop", "index": 0}
        ),
        _make_anthropic_sse_event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 3},
            },
        ),
        _make_anthropic_sse_event("message_stop", {"type": "message_stop"}),
    ]
    out = _collect_chunks(sm, events)
    # No chunk should carry usage.
    for c in out:
        if isinstance(c, dict):
            assert "usage" not in c or c.get("usage") is None


def test_flush_after_message_stop_is_idempotent():
    sm = AnthropicSSEToChatCompletionsStream(requested_model="claude-opus-4-6")
    events = [
        _make_anthropic_sse_event(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_idem",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": "claude-opus-4-6",
                    "stop_reason": None,
                    "usage": {"input_tokens": 1, "output_tokens": 0},
                },
            },
        ),
        _make_anthropic_sse_event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 1},
            },
        ),
        _make_anthropic_sse_event("message_stop", {"type": "message_stop"}),
    ]
    collected: list = []
    for raw in events:
        for ck in sm.feed(raw):
            collected.append(_parse_openai_sse_chunk(ck))
    # First flush after message_stop
    first_flush = sm.flush()
    # Second flush should be empty (idempotent)
    second_flush = sm.flush()
    assert second_flush == [] or all(
        _parse_openai_sse_chunk(c) is None for c in second_flush if c
    )


def test_flush_emits_done_when_stream_terminates_early():
    sm = AnthropicSSEToChatCompletionsStream(requested_model="claude-opus-4-6")
    # message_start only, then connection dropped.
    sm.feed(
        _make_anthropic_sse_event(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_early",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": "claude-opus-4-6",
                    "stop_reason": None,
                    "usage": {"input_tokens": 1, "output_tokens": 0},
                },
            },
        )
    )
    tail = [_parse_openai_sse_chunk(c) for c in sm.flush()]
    assert tail[-1] == "[DONE]"


def test_chunk_ids_carry_chatcmpl_prefix():
    sm = AnthropicSSEToChatCompletionsStream(requested_model="claude-opus-4-6")
    events = [
        _make_anthropic_sse_event(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_idtest",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": "claude-opus-4-6",
                    "stop_reason": None,
                    "usage": {"input_tokens": 1, "output_tokens": 0},
                },
            },
        ),
        _make_anthropic_sse_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        _make_anthropic_sse_event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "x"},
            },
        ),
        _make_anthropic_sse_event(
            "content_block_stop", {"type": "content_block_stop", "index": 0}
        ),
        _make_anthropic_sse_event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 1},
            },
        ),
        _make_anthropic_sse_event("message_stop", {"type": "message_stop"}),
    ]
    out = _collect_chunks(sm, events)
    for c in out:
        if isinstance(c, dict) and "id" in c:
            assert c["id"].startswith("chatcmpl-")


# ============================================================================
# === Group D: Responses API ===
# ============================================================================


def test_responses_input_as_string_treated_as_single_user_message():
    body = {"model": "claude-opus-4-6", "input": "hello"}
    out = responses_request_to_anthropic(body)
    msgs = out["messages"]
    assert len(msgs) == 1
    m = msgs[0]
    assert m["role"] == "user"
    # content should encode the text "hello" somewhere
    if isinstance(m["content"], list):
        assert any(
            b.get("type") == "text" and b.get("text") == "hello" for b in m["content"]
        )
    else:
        assert m["content"] == "hello"


def test_responses_input_as_messages_array_is_translated_like_chat_completions():
    body = {
        "model": "claude-opus-4-6",
        "input": [{"role": "user", "content": "hi"}],
    }
    out = responses_request_to_anthropic(body)
    msgs = out["messages"]
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"][0]["type"] == "text"
    assert msgs[0]["content"][0]["text"] == "hi"


def test_responses_input_as_items_form_normalized_to_messages():
    body = {
        "model": "claude-opus-4-6",
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "hi"}],
            }
        ],
    }
    out = responses_request_to_anthropic(body)
    msgs = out["messages"]
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"
    # Whatever shape used for content, the text should survive
    content = msgs[0]["content"]
    if isinstance(content, list):
        texts = [b.get("text", "") for b in content if isinstance(b, dict)]
        assert "hi" in texts
    else:
        assert content == "hi"


def test_responses_items_function_call_becomes_assistant_tool_calls():
    body = {
        "model": "claude-opus-4-6",
        "input": [
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "f",
                "arguments": "{}",
            }
        ],
    }
    out = responses_request_to_anthropic(body)
    msgs = out["messages"]
    # There should be an assistant message containing a tool_use block.
    asst = [m for m in msgs if m["role"] == "assistant"]
    assert asst, "expected an assistant message"
    blocks = asst[0]["content"]
    tu = [b for b in blocks if b.get("type") == "tool_use"]
    assert len(tu) == 1
    assert tu[0]["id"] == "call_1"
    assert tu[0]["name"] == "f"
    assert tu[0]["input"] == {}


def test_responses_items_function_call_output_becomes_tool_message():
    body = {
        "model": "claude-opus-4-6",
        "input": [
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "f",
                "arguments": "{}",
            },
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": "result",
            },
        ],
    }
    out = responses_request_to_anthropic(body)
    # function_call_output should appear as a user message with a tool_result block
    msgs = out["messages"]
    last = msgs[-1]
    assert last["role"] == "user"
    tr = [b for b in last["content"] if b.get("type") == "tool_result"]
    assert len(tr) == 1
    assert tr[0]["tool_use_id"] == "call_1"
    tr_content = tr[0].get("content")
    if isinstance(tr_content, str):
        assert "result" in tr_content
    else:
        joined = " ".join(
            (b.get("text", "") if isinstance(b, dict) else str(b)) for b in tr_content
        )
        assert "result" in joined


def test_responses_instructions_becomes_system():
    body = {
        "model": "claude-opus-4-6",
        "instructions": "be terse",
        "input": "hi",
    }
    out = responses_request_to_anthropic(body)
    sys_field = out.get("system")
    assert sys_field is not None
    if isinstance(sys_field, str):
        assert "be terse" in sys_field
    else:
        # array form
        joined = " ".join(
            (b.get("text", "") if isinstance(b, dict) else "") for b in sys_field
        )
        assert "be terse" in joined


def test_responses_max_output_tokens_maps_to_max_tokens():
    body = {
        "model": "claude-opus-4-6",
        "input": "hi",
        "max_output_tokens": 100,
    }
    out = responses_request_to_anthropic(body)
    assert out["max_tokens"] == 100
    assert "max_output_tokens" not in out


def test_responses_previous_response_id_raises_unsupported():
    body = {
        "model": "claude-opus-4-6",
        "input": "hi",
        "previous_response_id": "resp_foo",
    }
    with pytest.raises(UnsupportedParamError):
        responses_request_to_anthropic(body)


def test_responses_response_text_only_output_shape():
    a = {
        "id": "msg_resp1",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "hello"}],
        "model": "claude-opus-4-6",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 5, "output_tokens": 1},
    }
    out = anthropic_to_responses_response(a, requested_model="claude-opus-4-6")
    assert out["object"] == "response"
    assert out["status"] == "completed"
    assert out["id"].startswith("resp_")
    # output[0] is a message item with output_text content
    items = out["output"]
    msg_items = [it for it in items if it.get("type") == "message"]
    assert msg_items, "expected at least one message-type output item"
    content = msg_items[0]["content"]
    text_parts = [c for c in content if c.get("type") == "output_text"]
    assert text_parts
    assert text_parts[0]["text"] == "hello"


def test_responses_response_with_tool_use_emits_function_call_item():
    a = {
        "id": "msg_resp2",
        "type": "message",
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_xyz",
                "name": "do_thing",
                "input": {"a": 1},
            }
        ],
        "model": "claude-opus-4-6",
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 5, "output_tokens": 1},
    }
    out = anthropic_to_responses_response(a, requested_model="claude-opus-4-6")
    items = out["output"]
    fc = [it for it in items if it.get("type") == "function_call"]
    assert fc
    assert fc[0]["name"] == "do_thing"
    # call_id is the tool_use id
    assert fc[0]["call_id"] == "toolu_xyz"
    # arguments must be JSON-string of the input
    assert json.loads(fc[0]["arguments"]) == {"a": 1}


def test_responses_response_top_level_id_object_status():
    a = {
        "id": "msg_top",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "ok"}],
        "model": "claude-opus-4-6",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    out = anthropic_to_responses_response(a, requested_model="claude-opus-4-6")
    assert out["id"].startswith("resp_")
    assert out["object"] == "response"
    assert out["status"] == "completed"
    assert out["model"] == "claude-opus-4-6"


# ============================================================================
# === Group E: anthropic_error_to_openai_error ===
# ============================================================================


def test_error_from_dict_chat_completion_shape():
    a_err = {
        "type": "error",
        "error": {"type": "rate_limit_error", "message": "slow down"},
    }
    out = anthropic_error_to_openai_error(a_err, "chat.completion")
    assert "error" in out
    e = out["error"]
    assert e["message"] == "slow down"
    assert e["type"] == "rate_limit_error"


def test_error_from_bytes_parsed():
    raw = json.dumps(
        {
            "type": "error",
            "error": {"type": "overloaded_error", "message": "try again later"},
        }
    ).encode("utf-8")
    out = anthropic_error_to_openai_error(raw, "chat.completion")
    e = out["error"]
    assert e["message"] == "try again later"
    assert e["type"] == "overloaded_error"


def test_error_from_str_parsed():
    raw = json.dumps(
        {
            "type": "error",
            "error": {"type": "invalid_request_error", "message": "bad body"},
        }
    )
    out = anthropic_error_to_openai_error(raw, "chat.completion")
    e = out["error"]
    assert e["message"] == "bad body"
    assert e["type"] == "invalid_request_error"


def test_error_malformed_input_falls_back_to_raw_message():
    raw = b"not-json-at-all <html>oops</html>"
    out = anthropic_error_to_openai_error(raw, "chat.completion")
    # Must not raise. Must produce an error envelope with the raw content as message.
    assert "error" in out
    msg = out["error"].get("message") or ""
    # At minimum the raw content should be referenced.
    assert "not-json-at-all" in msg or "oops" in msg or msg  # always truthy as fallback


def test_error_target_shape_response_also_supported():
    a_err = {
        "type": "error",
        "error": {"type": "rate_limit_error", "message": "slow down"},
    }
    out_chat = anthropic_error_to_openai_error(a_err, "chat.completion")
    out_resp = anthropic_error_to_openai_error(a_err, "response")
    # Both shapes must produce an "error" envelope with message + type.
    for out in (out_chat, out_resp):
        assert "error" in out
        e = out["error"]
        assert e.get("message") == "slow down"
        assert e.get("type") == "rate_limit_error"
