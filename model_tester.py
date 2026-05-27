import asyncio
import hashlib
import json
import secrets
import time
import uuid
from datetime import datetime, timezone
import os
import re

def load_tokens_from_zshrc() -> list[str]:
    """Parse ~/.zshrc to find the ANTHROPIC_TOKENS block and extract quoted keys."""
    zshrc_path = os.path.expanduser("~/.zshrc")
    tokens = []
    if os.path.exists(zshrc_path):
        try:
            with open(zshrc_path, "r", encoding="utf-8") as f:
                content = f.read()
            match = re.search(r'ANTHROPIC_TOKENS\s*=\s*\((.*?)\)', content, re.DOTALL)
            if match:
                block = match.group(1)
                found = re.findall(r'"([^"]+)"|\'([^\']+)\'', block)
                for f_tuple in found:
                    token = f_tuple[0] or f_tuple[1]
                    if token and token.strip():
                        tokens.append(token.strip())
        except Exception as e:
            print(f"[SYSTEM] Error reading ~/.zshrc: {e}")
    return tokens

MODELS = [
    "claude-3-5-haiku-20241022",
    "claude-3-5-sonnet-20241022",
    "claude-3-7-sonnet-20250219",
    "claude-haiku-4-5-20251001",
    "claude-opus-4-1-20250805",
    "claude-opus-4-6",
    "claude-opus-4-7[1m]",
    "claude-sonnet-4-20250514",
    "gpt-5.5",
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


def update_models(new_models: list[str]):
    """Update global MODELS and initialize new test_results entries dynamically."""
    global MODELS, test_results
    if not new_models:
        return
    
    # Remove duplicates and preserve order
    seen = set()
    unique_models = []
    for m in new_models:
        if m not in seen:
            seen.add(m)
            unique_models.append(m)
            
    MODELS.clear()
    MODELS.extend(unique_models)
    _init_results()


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
    elif "choices" in data:
        choices = data.get("choices", [])
        if choices and isinstance(choices, list):
            first = choices[0]
            if isinstance(first, dict) and "message" in first:
                msg = first["message"]
                if isinstance(msg, dict) and "content" in msg:
                    text = msg["content"][:80]
                    result["status"] = "ok"
                    result["error_message"] = None
                    result["response_preview"] = text
                else:
                    result["status"] = "error"
                    result["error_message"] = f"HTTP {resp_status}: empty choice message content"
                    result["response_preview"] = None
            else:
                result["status"] = "error"
                result["error_message"] = f"HTTP {resp_status}: choices structure invalid"
                result["response_preview"] = None
        else:
            result["status"] = "error"
            result["error_message"] = f"HTTP {resp_status}: empty choices"
            result["response_preview"] = None
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
    "context-1m-2025-08-07",
])
_ANTHROPIC_BETA_BASIC = "interleaved-thinking-2025-05-14,context-1m-2025-08-07"

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


def _is_claude(model_name: str) -> bool:
    lower = model_name.lower()
    return "claude" in lower


def _test_headers(model_name: str) -> dict:
    """Build headers matching Claude CLI fingerprint or standard OpenAI."""
    if not _is_claude(model_name):
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
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


async def test_single_model(session, config: dict, model_name: str, header_fn, api_key: str = "") -> dict:
    """Test a single model using curl_cffi session (via asyncio.to_thread).
    Supports multiple keys (comma-separated or pipe-separated) with failover/retry up to 3 times.
    Supports candidate URLs (e.g. betterclau.de with automatic direct anyrouter.top fallback).
    """
    parts = [p.strip() for p in api_key.replace("\n", ",").replace("|", ",").split(",") if p.strip()]
    if not parts:
        parts = [""]
        
    max_attempts = min(3, len(parts))
    last_result = None
    
    # Get candidate URLs
    base = config['target_base_url'].rstrip('/')
    if "betterclau.de" in base:
        base = "https://anyrouter.top"
    elif base.endswith('/v1'):
        base = base[:-3]
    is_claude_model = _is_claude(model_name)
    primary_path = "/v1/messages" if is_claude_model else "/v1/chat/completions"
    
    # Always route directly to the resolved target base URL without betterclau.de
    candidate_urls = [f"{base}{primary_path}"]
        
    for attempt in range(max_attempts):
        if not hasattr(test_single_model, "counter"):
            test_single_model.counter = 0
        current_key = parts[test_single_model.counter % len(parts)]
        test_single_model.counter += 1
        
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
        success = False
        result = None
        
        for target_url in candidate_urls:
            url_to_use = target_url
            # Only append beta flag for Claude models using messages endpoint
            if is_claude_model and _needs_claude_code(model_name):
                url_to_use += "?beta=true"
                
            headers = _test_headers(model_name)
            if current_key:
                # Always send x-api-key for compatibility
                headers["x-api-key"] = current_key
                headers["Authorization"] = f"Bearer {current_key}"
                
            try:
                resp = await asyncio.to_thread(
                    session.request, "POST", url_to_use,
                    headers=headers, json=body, timeout=30,
                )
                elapsed_ms = int((time.monotonic() - start) * 1000)
                result = _parse_response(resp.status_code, resp.content, elapsed_ms, model_name)
                
                # Check for Cloudflare challenge (403 HTML page)
                is_cf = (resp.status_code in (403, 502, 504) and 
                         ("Just a moment" in resp.content.decode("utf-8", errors="ignore") or 
                          "cloudflare" in resp.content.decode("utf-8", errors="ignore").lower()))
                
                if is_cf:
                    if config.get("debug"):
                        print(f"[TESTER] candidate URL {url_to_use} got Cloudflare challenge, trying next URL...")
                    last_result = result
                    continue
                    
                success = True
                break
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
                if config.get("debug"):
                    print(f"[TESTER] candidate URL {url_to_use} failed with exception: {e}, trying next URL...")
                last_result = result
                continue
                
        if success and result:
            # If the resolved request itself is a rate limit or auth error, we can retry with next key
            is_retryable = (
                result["status"] == "rate_limited" or
                "limit" in str(result.get("error_message", "")).lower() or
                "unavailable" in str(result.get("error_message", "")).lower() or
                "unexpected response" in str(result.get("error_message", "")).lower()
            )
            
            if is_retryable and attempt < max_attempts - 1:
                if config.get("debug"):
                    print(f"[TESTER] Attempt {attempt + 1} got retryable state for {model_name}, retrying with next key...")
                last_result = result
                continue
                
            test_results[model_name] = result
            return result
        elif attempt < max_attempts - 1:
            if config.get("debug"):
                print(f"[TESTER] Attempt {attempt + 1} failed for {model_name}, retrying with next key...")
            continue
            
    if last_result:
        test_results[model_name] = last_result
        return last_result


async def test_all_models(session, config: dict, header_fn, api_key: str = "") -> dict[str, dict]:
    tasks = [test_single_model(session, config, model, header_fn, api_key) for model in MODELS]
    completed = await asyncio.gather(*tasks)
    return {model: res for model, res in zip(MODELS, completed)}
