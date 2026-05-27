"""E2E acceptance tests for OpenAI compatibility layer.

Run after `main.py` is wired with `/v1/chat/completions` (translator path)
and `/v1/responses`. Targets the running proxy at ANYROUTER_PROXY_BASE
(default http://127.0.0.1:8765).

Goal (from user): 部署后发起 OpenAI 格式的请求(两种格式都要), 能正确收到响应.

Run:
    python tests/test_e2e_openai.py

Configuration via env vars:
    ANYROUTER_PROXY_BASE  proxy base URL (default http://127.0.0.1:8765)
    ANYROUTER_PROXY_KEY   API key to send (else read from .secrets.json
                          in repo root if present)
    ANYROUTER_TEST_MODEL  model name (default claude-haiku-4-5-20251001)
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

def _load_test_key() -> str:
    env_key = os.environ.get("ANYROUTER_PROXY_KEY")
    if env_key:
        return env_key
    secrets_path = REPO_ROOT / ".secrets.json"
    if secrets_path.exists():
        with secrets_path.open() as f:
            data = json.load(f)
        toks = data.get("anthropic_tokens") or []
        for t in toks:
            if isinstance(t, str) and t.startswith("sk-"):
                return t
    raise SystemExit(
        "No API key available. Set ANYROUTER_PROXY_KEY env var, or create "
        ".secrets.json in repo root with {\"anthropic_tokens\": [\"sk-...\"]}."
    )


BASE = os.environ.get("ANYROUTER_PROXY_BASE", "http://127.0.0.1:8765")
KEY = _load_test_key()
MODEL_CLAUDE = os.environ.get("ANYROUTER_TEST_MODEL", "claude-haiku-4-5-20251001")

passed: list[str] = []
failed: list[tuple[str, str]] = []


def _check(name: str, ok: bool, detail: str = ""):
    if ok:
        passed.append(name)
        print(f"  ✓ {name}")
    else:
        failed.append((name, detail))
        print(f"  ✗ {name}: {detail}")


def t1_chat_completions_non_stream():
    print("\n[1] Chat Completions, non-stream, Claude model")
    from openai import OpenAI
    client = OpenAI(base_url=f"{BASE}/v1", api_key=KEY)
    r = client.chat.completions.create(
        model=MODEL_CLAUDE,
        messages=[{"role": "user", "content": "Say pong only, one word."}],
        max_tokens=64,
        temperature=0.0,
    )
    _check("response has choices", bool(r.choices), repr(r)[:200])
    _check("message.content non-empty",
           bool(r.choices[0].message.content),
           repr(r.choices[0].message)[:200])
    _check("finish_reason in {stop, length}",
           r.choices[0].finish_reason in ("stop", "length"),
           f"got {r.choices[0].finish_reason!r}")
    _check("id starts with chatcmpl-",
           (r.id or "").startswith("chatcmpl-"), f"id={r.id!r}")
    _check("usage present",
           bool(r.usage and r.usage.total_tokens),
           f"usage={r.usage!r}")


def t2_chat_completions_stream():
    print("\n[2] Chat Completions, streaming, Claude model")
    from openai import OpenAI
    client = OpenAI(base_url=f"{BASE}/v1", api_key=KEY)
    stream = client.chat.completions.create(
        model=MODEL_CLAUDE,
        messages=[{"role": "user", "content": "Count: 1, 2, 3."}],
        max_tokens=64,
        stream=True,
        temperature=0.0,
    )
    text_parts = []
    finish = None
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta and delta.content:
            text_parts.append(delta.content)
        if chunk.choices[0].finish_reason:
            finish = chunk.choices[0].finish_reason
    _check("streamed some content", bool(text_parts),
           f"got {len(text_parts)} chunks")
    _check("got finish_reason",
           finish in ("stop", "length"), f"finish={finish!r}")


def t3_responses_api_non_stream():
    print("\n[3] Responses API, non-stream, Claude model")
    from openai import OpenAI
    client = OpenAI(base_url=f"{BASE}/v1", api_key=KEY)
    try:
        r = client.responses.create(
            model=MODEL_CLAUDE,
            input="Say pong only.",
            max_output_tokens=64,
        )
    except Exception as e:
        _check("responses.create did not raise", False, f"{type(e).__name__}: {e}")
        return
    _check("response has output", bool(getattr(r, "output", None)),
           repr(r)[:200])
    if getattr(r, "output", None):
        first = r.output[0]
        item_type = getattr(first, "type", None)
        _check("first output item is message",
               item_type == "message", f"got type={item_type!r}")
        if item_type == "message":
            content = getattr(first, "content", None)
            _check("message has content", bool(content), repr(first)[:200])
    _check("id starts with resp_",
           (r.id or "").startswith("resp_"), f"id={r.id!r}")


def t4_anthropic_native_regression():
    print("\n[4] Anthropic native /v1/messages regression (should NOT be affected)")
    try:
        import anthropic
    except ImportError:
        _check("anthropic SDK installed", False,
               "pip install anthropic in venv first")
        return
    client = anthropic.Anthropic(base_url=BASE, api_key=KEY)
    r = client.messages.create(
        model=MODEL_CLAUDE,
        max_tokens=64,
        messages=[{"role": "user", "content": "Say pong."}],
    )
    _check("anthropic.messages.create returned content",
           bool(r.content), repr(r)[:200])


def main() -> int:
    print("=" * 60)
    print(f"E2E acceptance: {BASE}")
    print(f"Key:    {KEY[:10]}...{KEY[-4:]}")
    print(f"Model:  {MODEL_CLAUDE}")
    print("=" * 60)

    t1_chat_completions_non_stream()
    t2_chat_completions_stream()
    t3_responses_api_non_stream()
    t4_anthropic_native_regression()

    print("\n" + "=" * 60)
    print(f"PASSED: {len(passed)} | FAILED: {len(failed)}")
    if failed:
        print("\nFailures:")
        for name, detail in failed:
            print(f"  ✗ {name}: {detail}")
        return 1
    print("\n✓ ALL E2E TESTS PASSED — acceptance goal met")
    return 0


if __name__ == "__main__":
    sys.exit(main())
