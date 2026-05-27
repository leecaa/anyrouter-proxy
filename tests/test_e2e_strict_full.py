"""Strict content E2E: tests both Claude (translated→/v1/messages) and
non-Claude (passthrough→/v1/responses) paths. Dumps actual model responses.

Configuration via env vars:
    ANYROUTER_PROXY_BASE   default http://127.0.0.1:8765
    ANYROUTER_PROXY_KEY    API key (else read from .secrets.json in repo root)
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

BASE = os.environ.get("ANYROUTER_PROXY_BASE", "http://127.0.0.1:8765")
REPO_ROOT = Path(__file__).resolve().parent.parent

def _key():
    env_key = os.environ.get("ANYROUTER_PROXY_KEY")
    if env_key:
        return env_key
    secrets = REPO_ROOT / ".secrets.json"
    if secrets.exists():
        return json.load(secrets.open())["anthropic_tokens"][0]
    raise SystemExit("Set ANYROUTER_PROXY_KEY or create .secrets.json in repo root")

KEY = _key()
print("=" * 76)
print(f"严格内容验收  base={BASE}  key={KEY[:10]}...{KEY[-4:]}")
print("=" * 76)

from openai import OpenAI
client = OpenAI(base_url=f"{BASE}/v1", api_key=KEY)

results: list[tuple[str, bool, str]] = []

def section(label):
    print(f"\n{'─' * 76}\n[ {label} ]\n{'─' * 76}")


# ──────────────────────────────────────────────────────────────────────────
# A. Claude — translated path (proxy → upstream /v1/messages)
# ──────────────────────────────────────────────────────────────────────────
section("A1: claude-haiku-4-5 via /v1/chat/completions 非流 — 2+2=?")
try:
    r = client.chat.completions.create(
        model="claude-haiku-4-5-20251001",
        messages=[{"role": "user", "content": "What is 2+2? Just the digit."}],
        max_tokens=32, temperature=0.0,
    )
    c = r.choices[0].message.content
    print(f"  finish={r.choices[0].finish_reason}  id={r.id}")
    print(f"  CONTENT: {c!r}")
    ok = "4" in (c or "")
    results.append(("A1 claude-haiku chat 2+2", ok, c or ""))
    print(f"  {'✓ 含 4' if ok else '✗ 内容不正确'}")
except Exception as e:
    results.append(("A1 claude-haiku chat 2+2", False, f"{type(e).__name__}: {e}"))
    print(f"  ✗ EXCEPTION: {type(e).__name__}: {e}")

section("A2: claude-haiku-4-5 via /v1/chat/completions 流式 — count 1-5")
try:
    stream = client.chat.completions.create(
        model="claude-haiku-4-5-20251001",
        messages=[{"role": "user", "content": "Count 1 to 5, comma-separated. Just numbers."}],
        max_tokens=64, temperature=0.0, stream=True,
    )
    parts, finish, n = [], None, 0
    for chunk in stream:
        n += 1
        if not chunk.choices: continue
        d = chunk.choices[0].delta
        if d and d.content: parts.append(d.content)
        if chunk.choices[0].finish_reason: finish = chunk.choices[0].finish_reason
    full = "".join(parts)
    print(f"  chunks={n} ({len(parts)} content)  finish={finish}")
    print(f"  ASSEMBLED: {full!r}")
    ok = all(c in full for c in "12345")
    results.append(("A2 claude-haiku stream 1-5", ok, full))
    print(f"  {'✓ 含 1-5' if ok else '✗ 内容不正确'}")
except Exception as e:
    results.append(("A2 claude-haiku stream 1-5", False, f"{type(e).__name__}: {e}"))
    print(f"  ✗ EXCEPTION: {type(e).__name__}: {e}")

section("A3: claude-haiku-4-5 via /v1/responses 非流 — capital of France")
try:
    r = client.responses.create(
        model="claude-haiku-4-5-20251001",
        input="What is the capital of France? Just the city name.",
        max_output_tokens=32,
    )
    text = ""
    for item in r.output:
        if getattr(item, "type", None) == "message":
            for c in getattr(item, "content", []) or []:
                if getattr(c, "type", None) == "output_text":
                    text += getattr(c, "text", "") or ""
    print(f"  id={r.id}  status={r.status}")
    print(f"  EXTRACTED: {text!r}")
    ok = "paris" in text.lower()
    results.append(("A3 claude-haiku resp Paris", ok, text))
    print(f"  {'✓ 含 paris' if ok else '✗ 内容不正确'}")
except Exception as e:
    results.append(("A3 claude-haiku resp Paris", False, f"{type(e).__name__}: {e}"))
    print(f"  ✗ EXCEPTION: {type(e).__name__}: {e}")

# ──────────────────────────────────────────────────────────────────────────
# B. Non-Claude — passthrough path (proxy → upstream /v1/responses, streamed)
# ──────────────────────────────────────────────────────────────────────────
section("B1: gpt-5.5 via /v1/responses — 2+2=?")
try:
    # gpt models go via streaming on upstream; client request is also stream.
    # Use stream=True so we drain SSE here.
    stream = client.responses.create(
        model="gpt-5.5",
        input=[{"role": "user", "content": "What is 2+2? Just the digit."}],
        max_output_tokens=32, stream=True,
    )
    text = ""
    event_count = 0
    for event in stream:
        event_count += 1
        et = getattr(event, "type", "")
        # Collect text deltas
        if et == "response.output_text.delta":
            text += getattr(event, "delta", "") or ""
        # Some upstreams send full text in 'output_text.done' or 'response.completed'
        if et == "response.completed":
            resp = getattr(event, "response", None)
            if resp and not text:
                for item in getattr(resp, "output", []) or []:
                    if getattr(item, "type", None) == "message":
                        for c in getattr(item, "content", []) or []:
                            if getattr(c, "type", None) == "output_text":
                                text += getattr(c, "text", "") or ""
    print(f"  events={event_count}")
    print(f"  TEXT: {text!r}")
    ok = "4" in text
    results.append(("B1 gpt-5.5 resp 2+2", ok, text))
    print(f"  {'✓ 含 4' if ok else '✗ 内容不正确'}")
except Exception as e:
    results.append(("B1 gpt-5.5 resp 2+2", False, f"{type(e).__name__}: {e}"))
    print(f"  ✗ EXCEPTION: {type(e).__name__}: {e}")

section("B2: gemini-2.5-pro via /v1/responses — 'Hi say pong'")
try:
    stream = client.responses.create(
        model="gemini-2.5-pro",
        input=[{"role": "user", "content": "Say the word pong, nothing else."}],
        max_output_tokens=32, stream=True,
    )
    text = ""
    event_count = 0
    for event in stream:
        event_count += 1
        et = getattr(event, "type", "")
        if et == "response.output_text.delta":
            text += getattr(event, "delta", "") or ""
        if et == "response.completed":
            resp = getattr(event, "response", None)
            if resp and not text:
                for item in getattr(resp, "output", []) or []:
                    if getattr(item, "type", None) == "message":
                        for c in getattr(item, "content", []) or []:
                            if getattr(c, "type", None) == "output_text":
                                text += getattr(c, "text", "") or ""
    print(f"  events={event_count}")
    print(f"  TEXT: {text!r}")
    ok = "pong" in text.lower()
    results.append(("B2 gemini-2.5 resp pong", ok, text))
    print(f"  {'✓ 含 pong' if ok else '✗ 内容不正确'}")
except Exception as e:
    results.append(("B2 gemini-2.5 resp pong", False, f"{type(e).__name__}: {e}"))
    print(f"  ✗ EXCEPTION: {type(e).__name__}: {e}")

# ──────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 76)
ok_n = sum(1 for _, ok, _ in results if ok)
print(f"严格内容验收: {ok_n}/{len(results)} passed")
print("=" * 76)
for name, ok, detail in results:
    print(f"  {'✓' if ok else '✗'} {name}  →  {detail[:60]!r}")

sys.exit(0 if ok_n == len(results) else 1)
