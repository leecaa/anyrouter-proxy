import asyncio
import hashlib
import json
import secrets
import time
import uuid
from datetime import datetime, timezone

MODELS = [
    "claude-3-5-haiku-20241022",
    "claude-3-5-sonnet-20241022",
    "claude-3-7-sonnet-20250219",
    "claude-haiku-4-5-20251001",
    "claude-opus-4-1-20250805",
    "claude-opus-4-6",
    "claude-sonnet-4-20250514",
]

test_results: dict[str, dict] = {}


def _init_results():
    for model in MODELS:
        if model not in test_results:
            test_results[model] = {
                "model": model,
                "status": "untested",
                "latency_ms": None,
                "error_message": None,
                "response_preview": None,
                "tested_at": None,
            }


_init_results()


def _parse_response(resp_status: int, body: bytes, elapsed_ms: int, model: str) -> dict:
    result = {
        "model": model,
        "latency_ms": elapsed_ms,
        "tested_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        result["status"] = "error"
        result["error_message"] = f"HTTP {resp_status}: invalid response"
        result["response_preview"] = None
        return result

    error = data.get("error", {})
    error_msg = error.get("message", "") if isinstance(error, dict) else str(error)

    if "负载已经达到上限" in error_msg:
        result["status"] = "rate_limited"
        result["error_message"] = error_msg
        result["response_preview"] = None
    elif "已下线" in error_msg or "不支持" in error_msg:
        result["status"] = "error"
        result["error_message"] = error_msg
        result["response_preview"] = None
    elif error_msg:
        result["status"] = "error"
        result["error_message"] = error_msg
        result["response_preview"] = None
    elif "content" in data:
        text = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                text = block.get("text", "")[:80]
                break
        result["status"] = "ok"
        result["error_message"] = None
        result["response_preview"] = text
    else:
        result["status"] = "error"
        result["error_message"] = f"HTTP {resp_status}: unexpected response"
        result["response_preview"] = None

    return result


_CLI_VERSION = "2.1.72"
_SDK_PACKAGE_VERSION = "0.74.0"
_ANTHROPIC_VERSION = "2023-06-01"
_NODE_VERSION = "v24.3.0"

_ANTHROPIC_BETA_FULL = ",".join([
    "claude-code-20250219",
    "interleaved-thinking-2025-05-14",
    "redact-thinking-2026-02-12",
    "context-management-2025-06-27",
    "prompt-caching-scope-2026-01-05",
    "effort-2025-11-24",
])
_ANTHROPIC_BETA_BASIC = "interleaved-thinking-2025-05-14"

_SESSION_ID = str(uuid.uuid4())
_USER_HASH = hashlib.sha256(secrets.token_bytes(32)).hexdigest()

def _make_user_id():
    return f"user_{_USER_HASH}_account__session_{_SESSION_ID}"

# Shared state: set by main.py at startup
claude_code_tools: list = []
claude_code_system: list = []


def _needs_claude_code(model_name: str) -> bool:
    lower = model_name.lower()
    return "sonnet" in lower or "opus" in lower


def _test_headers(model_name: str) -> dict:
    """Build headers matching Claude CLI fingerprint."""
    is_code_model = _needs_claude_code(model_name)
    beta = _ANTHROPIC_BETA_FULL if is_code_model else _ANTHROPIC_BETA_BASIC
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": f"claude-cli/{_CLI_VERSION} (external, cli)",
        "X-Stainless-Arch": "x64",
        "X-Stainless-Lang": "js",
        "X-Stainless-OS": "MacOS",
        "X-Stainless-Package-Version": _SDK_PACKAGE_VERSION,
        "X-Stainless-Retry-Count": "0",
        "X-Stainless-Runtime": "node",
        "X-Stainless-Runtime-Version": _NODE_VERSION,
        "X-Stainless-Timeout": "600",
        "anthropic-beta": beta,
        "anthropic-dangerous-direct-browser-access": "true",
        "anthropic-version": _ANTHROPIC_VERSION,
        "x-app": "cli",
        "Accept-Encoding": "gzip, deflate, br, zstd",
    }
    return headers


async def test_single_model(session, config: dict, model_name: str, header_fn) -> dict:
    """Test a single model using curl_cffi session (via asyncio.to_thread)."""
    target_url = f"{config['target_base_url']}/messages"
    if _needs_claude_code(model_name):
        target_url += "?beta=true"
    headers = _test_headers(model_name)
    if config.get("api_key"):
        headers["x-api-key"] = config["api_key"]

    body = {
        "model": model_name,
        "max_tokens": 64,
        "stream": False,
        "messages": [{"role": "user", "content": "Say pong"}],
    }
    if _needs_claude_code(model_name):
        body["thinking"] = {"type": "adaptive"}
        body["metadata"] = {"user_id": _make_user_id()}
        body["context_management"] = {"edits": [{"type": "clear_thinking_20251015", "keep": "all"}]}
        body["output_config"] = {"effort": "medium"}
        if claude_code_tools:
            body["tools"] = claude_code_tools
        if claude_code_system:
            body["system"] = claude_code_system

    start = time.monotonic()
    try:
        resp = await asyncio.to_thread(
            session.request, "POST", target_url,
            headers=headers, json=body, timeout=60,
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)
        result = _parse_response(resp.status_code, resp.content, elapsed_ms, model_name)
    except Exception as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        result = {
            "model": model_name,
            "status": "error",
            "latency_ms": elapsed_ms,
            "error_message": str(e),
            "response_preview": None,
            "tested_at": datetime.now(timezone.utc).isoformat(),
        }

    test_results[model_name] = result
    return result


async def test_all_models(session, config: dict, header_fn) -> dict[str, dict]:
    results = {}
    for model in MODELS:
        results[model] = await test_single_model(session, config, model, header_fn)
    return results
