"""Strict E2E acceptance: validates that the LLM actually responds with meaningful
content (not just that the HTTP layer returned 200 with a 'choices' field).

For each call, dumps the FULL response text so the user can see what came back.

Configuration via env vars (same as test_e2e_openai.py):
    ANYROUTER_PROXY_BASE   default http://127.0.0.1:8765
    ANYROUTER_PROXY_KEY    API key (else read from .secrets.json in repo root)
    ANYROUTER_TEST_MODEL   default claude-haiku-4-5-20251001
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

BASE = os.environ.get("ANYROUTER_PROXY_BASE", "http://127.0.0.1:8765")
MODEL = os.environ.get("ANYROUTER_TEST_MODEL", "claude-haiku-4-5-20251001")

REPO_ROOT = Path(__file__).resolve().parent.parent

def _load_key():
    env_key = os.environ.get("ANYROUTER_PROXY_KEY")
    if env_key:
        return env_key
    secrets = REPO_ROOT / ".secrets.json"
    if secrets.exists():
        return json.load(secrets.open())["anthropic_tokens"][0]
    raise SystemExit("Set ANYROUTER_PROXY_KEY or create .secrets.json in repo root")

KEY = _load_key()

print("=" * 72)
print(f"严格内容验收  base={BASE}  model={MODEL}  key={KEY[:10]}...{KEY[-4:]}")
print("=" * 72)


def divider(label):
    print(f"\n{'─' * 72}\n[ {label} ]\n{'─' * 72}")


from openai import OpenAI
client = OpenAI(base_url=f"{BASE}/v1", api_key=KEY)

# ──────────────────────────────────────────────────────────────────────────
# Case 1: Chat Completions, non-stream — ask the model a question with a
# verifiable answer
# ──────────────────────────────────────────────────────────────────────────
divider("Case 1: /v1/chat/completions 非流  —  '2+2=?'")
r1 = client.chat.completions.create(
    model=MODEL,
    messages=[{"role": "user",
               "content": "What is 2+2? Respond with just the digit, nothing else."}],
    max_tokens=32, temperature=0.0,
)
content1 = r1.choices[0].message.content
print(f"  HTTP  : 200 OK")
print(f"  ID    : {r1.id}")
print(f"  Model : {r1.model}")
print(f"  Finish: {r1.choices[0].finish_reason}")
print(f"  Usage : prompt={r1.usage.prompt_tokens}, completion={r1.usage.completion_tokens}, total={r1.usage.total_tokens}")
print(f"  CONTENT (raw, repr):")
print(f"    {content1!r}")
verdict1 = "4" in (content1 or "")
print(f"  含 '4'? {'✓ YES' if verdict1 else '✗ NO — 内容不正确!'}")

# ──────────────────────────────────────────────────────────────────────────
# Case 2: Chat Completions, streaming — same question, must stream identical
# information through SSE chunks
# ──────────────────────────────────────────────────────────────────────────
divider("Case 2: /v1/chat/completions 流式  —  'Count 1 to 5'")
stream = client.chat.completions.create(
    model=MODEL,
    messages=[{"role": "user",
               "content": "Count from 1 to 5, separated by commas. Just the numbers."}],
    max_tokens=64, temperature=0.0, stream=True,
)
chunks = []
finish = None
chunk_count = 0
for chunk in stream:
    chunk_count += 1
    if not chunk.choices:
        continue
    delta = chunk.choices[0].delta
    if delta and delta.content:
        chunks.append(delta.content)
    if chunk.choices[0].finish_reason:
        finish = chunk.choices[0].finish_reason
full2 = "".join(chunks)
print(f"  Chunks received: {chunk_count} ({len(chunks)} content chunks)")
print(f"  Finish reason  : {finish}")
print(f"  ASSEMBLED CONTENT (raw, repr):")
print(f"    {full2!r}")
all_5 = all(c in full2 for c in ("1", "2", "3", "4", "5"))
print(f"  含 1-5? {'✓ YES' if all_5 else '✗ NO — 内容不正确!'}")

# ──────────────────────────────────────────────────────────────────────────
# Case 3: Responses API — same correctness check
# ──────────────────────────────────────────────────────────────────────────
divider("Case 3: /v1/responses 非流  —  '法国的首都是?'")
r3 = client.responses.create(
    model=MODEL,
    input="What is the capital of France? Respond with the city name only, one word.",
    max_output_tokens=32,
)
print(f"  HTTP  : 200 OK")
print(f"  ID    : {r3.id}")
print(f"  Status: {r3.status}")
print(f"  Output items: {len(r3.output)}")
# Drill into the message content
text3 = ""
for item in r3.output:
    if getattr(item, "type", None) == "message":
        for c in getattr(item, "content", []) or []:
            if getattr(c, "type", None) == "output_text":
                text3 += getattr(c, "text", "") or ""
print(f"  RAW output:")
print(f"    {[item.model_dump() if hasattr(item,'model_dump') else dict(item) for item in r3.output]!r}"[:500])
print(f"  EXTRACTED TEXT:")
print(f"    {text3!r}")
verdict3 = "paris" in text3.lower()
print(f"  含 'paris'? {'✓ YES' if verdict3 else '✗ NO — 内容不正确!'}")

# ──────────────────────────────────────────────────────────────────────────
# Verdict
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 72)
total = sum([verdict1, all_5, verdict3])
print(f"严格内容验收: {total}/3 passed")
print("=" * 72)
for label, ok in [("Chat Completions 非流 (2+2=4)", verdict1),
                  ("Chat Completions 流式 (count 1-5)", all_5),
                  ("Responses API (capital of France=Paris)", verdict3)]:
    print(f"  {'✓' if ok else '✗'} {label}")

if total == 3:
    print("\n✓ 所有响应内容均正确 — 模型确实在通过 proxy 给出有意义的回答")
    sys.exit(0)
else:
    print("\n✗ 部分响应内容不正确 — proxy 链路可能传错或翻译丢失了内容")
    sys.exit(1)
