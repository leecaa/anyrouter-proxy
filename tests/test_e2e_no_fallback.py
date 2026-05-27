"""No-fallback acceptance: verify that
  (A) supported Claude models actually answer downstream requests, and
  (B) unsupported models get a transparent upstream error (no model switch).

This replaces the implicit fallback behavior with a strict "model passthrough"
contract: the client's `model` field is forwarded to upstream verbatim.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

BASE = os.environ.get("ANYROUTER_PROXY_BASE", "http://127.0.0.1:8765")
REPO_ROOT = Path(__file__).resolve().parent.parent


def _key() -> str:
    env_key = os.environ.get("ANYROUTER_PROXY_KEY")
    if env_key:
        return env_key
    secrets = REPO_ROOT / ".secrets.json"
    if secrets.exists():
        return json.load(secrets.open())["anthropic_tokens"][0]
    raise SystemExit("Set ANYROUTER_PROXY_KEY or create .secrets.json in repo root")


KEY = _key()
print("=" * 76)
print(f"No-Fallback 验收  base={BASE}  key={KEY[:10]}...{KEY[-4:]}")
print("=" * 76)

from openai import OpenAI, NotFoundError, BadRequestError, APIStatusError

client = OpenAI(base_url=f"{BASE}/v1", api_key=KEY)

results: list[tuple[str, bool, str]] = []


def section(label: str) -> None:
    print(f"\n{'─' * 76}\n[ {label} ]\n{'─' * 76}")


def record(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"  {'✓' if ok else '✗'} {detail}")


# ──────────────────────────────────────────────────────────────────────────
# A. 支持的 Claude 模型: 客户端 model 透传到上游, 拿到真实回答
# ──────────────────────────────────────────────────────────────────────────

section("A1: claude-haiku-4-5 via /v1/chat/completions 非流 — 2+2=?")
try:
    r = client.chat.completions.create(
        model="claude-haiku-4-5-20251001",
        messages=[{"role": "user", "content": "What is 2+2? Just the digit."}],
        max_tokens=32, temperature=0.0,
    )
    content = r.choices[0].message.content or ""
    print(f"  HTTP 200  id={r.id}  model={r.model}  finish={r.choices[0].finish_reason}")
    print(f"  CONTENT: {content!r}")
    record("A1", "4" in content, f"含 '4'? {'YES' if '4' in content else 'NO'}")
except Exception as e:
    record("A1", False, f"EXCEPTION: {type(e).__name__}: {e}")

section("A2: claude-haiku-4-5 via /v1/chat/completions 流式 — count 1-5")
try:
    stream = client.chat.completions.create(
        model="claude-haiku-4-5-20251001",
        messages=[{"role": "user", "content": "Count 1 to 5, comma-separated. Just numbers."}],
        max_tokens=64, temperature=0.0, stream=True,
    )
    parts, finish, n = [], None, 0
    for c in stream:
        n += 1
        if not c.choices:
            continue
        d = c.choices[0].delta
        if d and d.content:
            parts.append(d.content)
        if c.choices[0].finish_reason:
            finish = c.choices[0].finish_reason
    full = "".join(parts)
    print(f"  chunks={n} ({len(parts)} content)  finish={finish}")
    print(f"  ASSEMBLED: {full!r}")
    record("A2", all(x in full for x in "12345"), f"含 1-5? {'YES' if all(x in full for x in '12345') else 'NO'}")
except Exception as e:
    record("A2", False, f"EXCEPTION: {type(e).__name__}: {e}")

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
    print(f"  HTTP 200  id={r.id}  status={r.status}  model={r.model}")
    print(f"  EXTRACTED: {text!r}")
    record("A3", "paris" in text.lower(), f"含 paris? {'YES' if 'paris' in text.lower() else 'NO'}")
except Exception as e:
    record("A3", False, f"EXCEPTION: {type(e).__name__}: {e}")

section("A4: claude-haiku-4-5 via /v1/responses 流式 — Say pong")
try:
    stream = client.responses.create(
        model="claude-haiku-4-5-20251001",
        input=[{"role": "user", "content": "Say the word pong only."}],
        max_output_tokens=32, stream=True,
    )
    text = ""
    n = 0
    for event in stream:
        n += 1
        if getattr(event, "type", "") == "response.output_text.delta":
            text += getattr(event, "delta", "") or ""
    print(f"  events={n}")
    print(f"  TEXT: {text!r}")
    record("A4", "pong" in text.lower(), f"含 pong? {'YES' if 'pong' in text.lower() else 'NO'}")
except Exception as e:
    record("A4", False, f"EXCEPTION: {type(e).__name__}: {e}")

# ──────────────────────────────────────────────────────────────────────────
# B. 不支持的模型: 必须透传上游错误, 不切换模型 (no fallback)
# ──────────────────────────────────────────────────────────────────────────

section("B1: model='gpt-5.5' — 应透传上游 404, 不 fallback")
try:
    client.chat.completions.create(
        model="gpt-5.5",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=16,
    )
    record("B1", False, "EXPECTED 404 but got 200 (fallback leaked!)")
except (NotFoundError, BadRequestError, APIStatusError) as e:
    msg = str(e).lower()
    is_404 = "404" in str(e) or "not_found" in msg or "不支持" in str(e)
    print(f"  caught {type(e).__name__}: {str(e)[:200]}")
    record("B1", is_404, f"上游 404 透传 {'OK' if is_404 else 'NOT 404'}")

section("B2: model='claude-opus-4-7' — 上游 503 时应透传 503, 不 fallback")
# Note: behavior depends on upstream state. Acceptable outcomes:
#   - 200 (upstream recovered) → response carries claude-opus-4-7
#   - 503 (upstream overloaded) → error transparently passed
# Either way, the response.model MUST equal client request, not haiku.
try:
    r = client.responses.create(
        model="claude-opus-4-7",
        input=[{"role": "user", "content": "hi"}],
        max_output_tokens=16,
    )
    print(f"  HTTP 200  model returned: {r.model!r}")
    ok = r.model == "claude-opus-4-7"
    record("B2", ok, f"no model switch? model={r.model!r} {'OK (matches request)' if ok else 'SWITCHED!'}")
except (APIStatusError, Exception) as e:
    status = getattr(e, "status_code", None) or "?"
    msg = str(e).lower()
    transparent = "503" in str(e) or "service unavailable" in msg or "overload" in msg
    print(f"  caught {type(e).__name__} (status={status}): {str(e)[:200]}")
    record("B2", transparent, f"上游 5xx 透传 {'OK (no fallback)' if transparent else 'unexpected'}")

# ──────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 76)
ok_n = sum(1 for _, ok, _ in results if ok)
total = len(results)
print(f"No-Fallback 验收: {ok_n}/{total} passed")
print("=" * 76)
for name, ok, detail in results:
    print(f"  {'✓' if ok else '✗'} {name}  →  {detail}")
sys.exit(0 if ok_n == total else 1)
