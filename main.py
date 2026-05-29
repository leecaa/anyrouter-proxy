import json
import hashlib
import sys
import os
import secrets
import time
import uuid
import traceback
import argparse
import asyncio
import threading
import queue as thread_queue
from curl_cffi import requests as cf_requests
from fastapi import FastAPI, Request, Response, Form, HTTPException
from fastapi.responses import StreamingResponse, HTMLResponse, RedirectResponse
import uvicorn

from auth import create_session_token, verify_session_token, verify_password, COOKIE_NAME, MAX_AGE
from dashboard import get_login_html, get_dashboard_html
import model_tester
from model_tester import MODELS, test_results, test_single_model, test_all_models, update_models, load_tokens_from_zshrc
from openai_translator import (
    UnsupportedParamError,
    chat_completions_to_anthropic,
    anthropic_to_chat_completion,
    AnthropicSSEToChatCompletionsStream,
    responses_request_to_anthropic,
    anthropic_to_responses_response,
    AnthropicSSEToResponsesStream,
    anthropic_error_to_openai_error,
)

def resolve_config_path():
    env_path = os.environ.get("ANYROUTER_PROXY_CONFIG")
    if env_path:
        return os.path.abspath(os.path.expanduser(env_path))
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if not xdg_config:
        xdg_config = os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(xdg_config, "anyrouter-proxy", "proxy_config.json")

CONFIG_FILE = resolve_config_path()

DEFAULT_CONFIG = {
    "proxy_url": "",
    "use_proxy": False,
    "debug": False,
    "target_base_url": "https://anyrouter.top",
    "host": "127.0.0.1",
    "port": 8765,
    "dashboard_password": "",
    "dashboard_secret": "",
    "api_keys": [],
}

# --- API Key Pool and Round Robin Load Balancing ---
GLOBAL_KEYS = []
global_key_index = 0
key_lock = threading.Lock()

def parse_api_keys(key_str: str) -> list[str]:
    """Parse comma, pipe, or newline delimited API keys safely."""
    if not key_str:
        return []
    parts = []
    normalized = key_str.replace("\r", "").replace("\n", ",").replace("|", ",")
    for p in normalized.split(","):
        k = p.strip()
        if k:
            parts.append(k)
    return parts

def set_global_keys(keys: list[str]):
    global GLOBAL_KEYS
    with key_lock:
        GLOBAL_KEYS = [k for k in keys if k]

def get_next_global_key(client_key: str = None) -> str:
    """Get the next key from the pool. If client_key contains multiple keys, round-robin them.
    Otherwise, if client_key is a single valid key starting with sk-, use it.
    If no client_key is provided or it's a placeholder, use the global pool.
    """
    global global_key_index
    # 1. Parse client key(s)
    client_keys = parse_api_keys(client_key)
    if len(client_keys) > 1:
        with key_lock:
            key = client_keys[global_key_index % len(client_keys)]
            global_key_index += 1
            return key
    elif len(client_keys) == 1 and client_keys[0].startswith("sk-"):
        return client_keys[0]
        
    # 2. Fallback to global pool
    if GLOBAL_KEYS:
        with key_lock:
            key = GLOBAL_KEYS[global_key_index % len(GLOBAL_KEYS)]
            global_key_index += 1
            return key
            
    # 3. Fallback to client key even if it doesn't start with sk- (just in case)
    if client_keys:
        return client_keys[0]
        
    return ""

config = {}
SESSION = None
CLAUDE_CODE_TOOLS = []
CLAUDE_CODE_SYSTEM = []

# --- Claude CLI fingerprint template (matches real claude-cli traffic) ---
_CLI_VERSION = "2.1.72"
_SDK_PACKAGE_VERSION = "0.74.0"
_ANTHROPIC_VERSION = "2023-06-01"
_NODE_VERSION = "v24.3.0"

# Full anthropic-beta flags matching real Claude CLI
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

# Stable per-instance session id (regenerated on each process start)
_SESSION_ID = str(uuid.uuid4())
_USER_HASH = hashlib.sha256(secrets.token_bytes(32)).hexdigest()

def _make_user_id():
    """Generate a user_id matching Claude CLI format: user_{hash}_account__session_{uuid}."""
    return f"user_{_USER_HASH}_account__session_{_SESSION_ID}"

def load_claude_code_templates():
    global CLAUDE_CODE_TOOLS, CLAUDE_CODE_SYSTEM
    tools_file = os.path.join(os.path.dirname(__file__), 'claude_code_tools.json')
    system_file = os.path.join(os.path.dirname(__file__), 'claude_code_system.json')
    if os.path.exists(tools_file):
        try:
            with open(tools_file, 'r', encoding='utf-8') as f:
                CLAUDE_CODE_TOOLS = json.load(f)
            print(f"[SYSTEM] Loaded {len(CLAUDE_CODE_TOOLS)} Claude Code tools")
        except Exception as e:
            print(f"[SYSTEM] Error loading tools: {e}")
    if os.path.exists(system_file):
        try:
            with open(system_file, 'r', encoding='utf-8') as f:
                CLAUDE_CODE_SYSTEM = json.load(f)
            print(f"[SYSTEM] Loaded Claude Code system prompt")
        except Exception as e:
            print(f"[SYSTEM] Error loading system: {e}")
    model_tester.claude_code_tools = CLAUDE_CODE_TOOLS
    model_tester.claude_code_system = CLAUDE_CODE_SYSTEM

async def fetch_upstream_models_list(api_key: str) -> list[str]:
    """Fetch model list from upstream /v1/models using API Key."""
    global SESSION
    if not api_key:
        return []
        
    base = config['target_base_url'].rstrip('/')
    if "betterclau.de" in base:
        base = "https://anyrouter.top"
    elif base.endswith('/v1'):
        base = base[:-3]
    candidate_urls = [f"{base}/v1/models"]
        
    headers = {
        "Accept": "application/json",
        "x-api-key": api_key,
        "Authorization": f"Bearer {api_key}",
    }
    
    last_err = None
    for target_url in candidate_urls:
        try:
            if config['debug']:
                print(f"[SYSTEM] Fetching upstream models from {target_url}")
            resp = await asyncio.to_thread(
                SESSION.request, "GET", target_url, headers=headers, timeout=15
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict) and "data" in data:
                    models = [m["id"] for m in data["data"] if isinstance(m, dict) and "id" in m]
                    if config['debug']:
                        print(f"[SYSTEM] Successfully fetched {len(models)} models from upstream ({target_url})")
                    return models
            else:
                last_err = f"HTTP {resp.status_code}"
                if config['debug']:
                    print(f"[SYSTEM] Fetching models from {target_url} failed with {resp.status_code}, trying next candidate...")
        except Exception as e:
            last_err = str(e)
            if config['debug']:
                print(f"[SYSTEM] Fetching models from {target_url} raised exception: {e}, trying next candidate...")
                
    print(f"[SYSTEM] Failed to fetch upstream models after trying all candidates. Last error: {last_err}")
    return []

def load_config():
    global config
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                loaded_config = json.load(f)
            config = DEFAULT_CONFIG.copy()
            config.update(loaded_config)
            print(f"[SYSTEM] Configuration loaded from {CONFIG_FILE}")
            
            # Load keys
            config_keys = config.get("api_keys", [])
            if isinstance(config_keys, list):
                set_global_keys(config_keys)
            elif isinstance(config_keys, str):
                set_global_keys(parse_api_keys(config_keys))
                
            if not GLOBAL_KEYS:
                zsh_keys = load_tokens_from_zshrc()
                if zsh_keys:
                    set_global_keys(zsh_keys)
                    print(f"[SYSTEM] Fallback: Loaded {len(zsh_keys)} API Keys from ~/.zshrc")
            else:
                print(f"[SYSTEM] Loaded {len(GLOBAL_KEYS)} API Keys from config")
                
            return True
        except Exception as e:
            print(f"[SYSTEM] Error loading config: {e}")
            config = DEFAULT_CONFIG.copy()
            return False
    else:
        config = DEFAULT_CONFIG.copy()
        # Fallback to ~/.zshrc
        zsh_keys = load_tokens_from_zshrc()
        if zsh_keys:
            set_global_keys(zsh_keys)
            print(f"[SYSTEM] Loaded {len(zsh_keys)} API Keys from ~/.zshrc")
        return False

def save_config():
    try:
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4)
        print(f"[SYSTEM] Configuration saved to {CONFIG_FILE}")
    except Exception as e:
        print(f"[SYSTEM] Error saving config: {e}")

def setup_wizard():
    print("\n" + "="*60)
    print("AnyRouter Proxy Setup Wizard")
    print("="*60)
    print("Please configure your proxy settings.\n")
    use_proxy_str = "y" if config.get('use_proxy', True) else "n"
    use_proxy = input(f"Use HTTP Proxy? (y/n) [{use_proxy_str}]: ").strip().lower()
    if use_proxy:
        config['use_proxy'] = (use_proxy == 'y')
    if config['use_proxy']:
        current_proxy = config.get('proxy_url', '')
        proxy_url = input(f"Proxy URL [{current_proxy}]: ").strip()
        if proxy_url:
            config['proxy_url'] = proxy_url
    debug_str = "y" if config.get('debug', False) else "n"
    debug_mode = input(f"Enable Debug Mode? (y/n) [{debug_str}]: ").strip().lower()
    if debug_mode:
        config['debug'] = (debug_mode == 'y')
    save_config()
    print("\n" + "="*60)
    print("Setup complete!")
    print("="*60 + "\n")

app = FastAPI()

def get_claude_headers(is_stream=False, model="", client_headers=None):
    """Build headers matching Claude CLI fingerprint exactly.

    Replicates the header set sent by claude-cli (Node.js / @anthropic-ai/sdk).
    If client_headers are provided and contain Claude CLI signatures, certain
    fields (anthropic-beta, retry count) are forwarded from the client to
    preserve the most up-to-date beta flags.
    """
    is_code_model = "opus" in model.lower() or "sonnet" in model.lower()
    beta = _ANTHROPIC_BETA_FULL if is_code_model else _ANTHROPIC_BETA_BASIC

    headers = {
        "Accept": "text/event-stream" if is_stream else "application/json",
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

    # Merge client's anthropic-beta with proxy's required flags.
    # Strategy: union of all flags, ensuring critical ones are never lost.
    # ALWAYS ensure context-1m-2025-08-07 is present (upstream requires it).
    _REQUIRED_BETA_FLAGS = {"context-1m-2025-08-07"}
    if client_headers:
        client_beta = client_headers.get("anthropic-beta")
        if client_beta:
            proxy_flags = set(beta.split(","))
            client_flags = set(client_beta.split(","))
            merged = proxy_flags | client_flags | _REQUIRED_BETA_FLAGS
            if not _needs_context_1m_beta(model):
                merged = {"interleaved-thinking-2025-05-14"}
            merged.discard("")
            headers["anthropic-beta"] = ",".join(sorted(merged))
        # Prefer client's User-Agent if it's a valid claude-cli UA
        client_ua = client_headers.get("user-agent", "")
        if client_ua.startswith("claude-cli/"):
            headers["User-Agent"] = client_ua
        # Prefer client's Stainless metadata (arch, os, versions evolve with CLI)
        _STAINLESS_FORWARD = {
            "x-stainless-arch": "X-Stainless-Arch",
            "x-stainless-os": "X-Stainless-OS",
            "x-stainless-package-version": "X-Stainless-Package-Version",
            "x-stainless-runtime-version": "X-Stainless-Runtime-Version",
        }
        for lower_key, header_key in _STAINLESS_FORWARD.items():
            val = client_headers.get(lower_key)
            if val:
                headers[header_key] = val
        client_retry = client_headers.get("X-Stainless-Retry-Count") or client_headers.get("x-stainless-retry-count")
        if client_retry is not None:
            headers["X-Stainless-Retry-Count"] = client_retry

    return headers

def create_session():
    """Create curl_cffi session with Chrome TLS fingerprint."""
    proxies = None
    if config['use_proxy']:
        proxies = {
            "http": config['proxy_url'],
            "https": config['proxy_url'],
        }
    if config['debug']:
        print(f"[SYSTEM] Creating curl_cffi session (impersonate=chrome, proxy={config['proxy_url'] if config['use_proxy'] else 'None'})")
    return cf_requests.Session(
        impersonate="chrome",
        proxies=proxies,
        verify=False,
        timeout=600,
    )

@app.on_event("startup")
async def startup():
    global SESSION
    SESSION = create_session()
    if not config.get('dashboard_secret'):
        config['dashboard_secret'] = secrets.token_hex(32)
        save_config()

@app.on_event("shutdown")
async def shutdown():
    global SESSION
    if SESSION:
        SESSION.close()

# --- Streaming bridge: curl_cffi (sync) -> FastAPI (async) ---

_STREAM_END = object()

def _stream_worker(session, method, url, headers, json_data, q):
    """Worker thread: run curl_cffi streaming request, push chunks to queue."""
    try:
        resp = session.request(
            method=method, url=url, headers=headers, json=json_data,
            stream=True, timeout=600,
        )
        q.put(("status", resp.status_code))
        for chunk in resp.iter_content():
            q.put(("data", chunk))
        q.put(("end", None))
    except Exception as e:
        q.put(("error", e))

async def _async_chunks(q):
    """Async generator reading chunks from thread-safe queue."""
    loop = asyncio.get_running_loop()
    while True:
        msg_type, value = await loop.run_in_executor(None, q.get)
        if msg_type == "end":
            break
        if msg_type == "error":
            print(f"[PROXY] Stream error: {value}")
            break
        if msg_type == "data":
            yield value

@app.get("/config")
async def get_config(request: Request):
    if not _check_auth(request):
        raise HTTPException(status_code=401, detail="Not authenticated")
    safe_config = config.copy()
    safe_config.pop('dashboard_secret', None)
    safe_config.pop('dashboard_password', None)
    return safe_config

@app.post("/config/reload")
async def reload_config(request: Request):
    if not _check_auth(request):
        raise HTTPException(status_code=401, detail="Not authenticated")
    global SESSION
    load_config()
    if SESSION:
        SESSION.close()
    SESSION = create_session()
    return {"status": "ok", "message": "Configuration reloaded"}

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": "v25",
        "proxy_enabled": config['use_proxy'],
        "tools_loaded": len(CLAUDE_CODE_TOOLS),
        "tls_fingerprint": "chrome",
    }

def _extract_client_key(request: Request) -> str:
    """Extract API key from request headers (Bearer preferred, then x-api-key)."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    k = request.headers.get("x-api-key", "")
    if k:
        return k
    return ""


# --- Model strategy ---
# The client's `model` field is passed through verbatim to upstream
# `/v1/messages`. Proxy does NOT override the model, does NOT fall back to
# another model, and does NOT trip a circuit breaker. If the upstream rejects
# the model (e.g. returns 503 for an overloaded model, 404 for an unknown
# model), that error is propagated to the downstream client as-is so the
# downstream (e.g. NewAPI) can decide how to handle it.
DEFAULT_MAX_TOKENS = 4096


def _is_claude_model(model_name: str) -> bool:
    """True if the model name suggests an Anthropic Claude model (case-insensitive)."""
    return "claude" in (model_name or "").lower()


def _needs_context_1m_beta(model_name: str) -> bool:
    """Whether upstream requires `anthropic-beta: context-1m-2025-08-07` for
    this Claude model.

    Empirical scan (2026-05-27): all Claude 4.x sonnet/opus and 3.5/3.7 sonnet
    models return 400 "1m 上下文已经全量可用" without this beta.
    claude-haiku-4-5 does NOT need it.
    """
    n = (model_name or "").lower()
    if not _is_claude_model(n):
        return False
    if "haiku" in n:
        return False
    return True


def _build_translated_anthropic_headers(
    request: Request, resolved_key: str, effective_model: str, wants_stream: bool
) -> dict:
    """Headers for OpenAI→Anthropic translated requests to upstream /v1/messages.
    Adds `anthropic-beta: context-1m-2025-08-07` only when the effective model
    requires it (everything except haiku-4-5)."""
    headers = {
        "Accept": "text/event-stream" if wants_stream else "application/json",
        "Content-Type": "application/json",
        "anthropic-version": _ANTHROPIC_VERSION,
    }
    if _needs_context_1m_beta(effective_model):
        headers["anthropic-beta"] = "context-1m-2025-08-07"
    if resolved_key:
        headers["x-api-key"] = resolved_key
        headers["Authorization"] = f"Bearer {resolved_key}"
    return headers


async def _translated_stream(q, translator):
    """Drain queue from _stream_worker, feed bytes into the OpenAI SSE translator,
    yield translated OpenAI/Responses SSE chunks."""
    loop = asyncio.get_running_loop()
    while True:
        msg_type, value = await loop.run_in_executor(None, q.get)
        if msg_type == "end":
            break
        if msg_type == "error":
            if config['debug']:
                print(f"[OPENAI-TRANSLATOR] stream error: {value}")
            break
        if msg_type == "data":
            try:
                for openai_chunk in translator.feed(value):
                    yield openai_chunk
            except Exception as e:
                if config['debug']:
                    print(f"[OPENAI-TRANSLATOR] feed error: {type(e).__name__}: {e}")
                    traceback.print_exc()
                break
    try:
        for tail in translator.flush():
            yield tail
    except Exception as e:
        if config['debug']:
            print(f"[OPENAI-TRANSLATOR] flush error: {type(e).__name__}: {e}")


async def _run_openai_translated_request(
    request: Request,
    *,
    request_translator,  # callable: (openai_body) -> anthropic_body
    response_translator,  # callable: (anthropic_body, requested_model) -> openai_body
    stream_machine_factory,  # callable: (requested_model, include_usage) -> state machine
    error_shape: str,  # "chat.completion" or "response"
):
    """Shared core: parse OpenAI body, translate to Anthropic, POST to upstream
    /v1/messages, translate response back to OpenAI shape.

    Used by both /v1/chat/completions and /v1/responses.
    """
    global SESSION

    body = await request.body()
    try:
        openai_body = json.loads(body) if body else {}
    except Exception as e:
        return Response(
            content=json.dumps({"error": {
                "message": f"invalid JSON body: {e}", "type": "invalid_request_error"}}).encode(),
            status_code=400, media_type="application/json")

    try:
        anthropic_body = request_translator(openai_body)
    except UnsupportedParamError as e:
        err = {"error": {
            "message": str(e), "type": "invalid_request_error",
            "param": getattr(e, "param", None), "code": None}}
        return Response(content=json.dumps(err).encode(),
                        status_code=400, media_type="application/json")
    except Exception as e:
        if config['debug']:
            print(f"[OPENAI-TRANSLATOR] request translation failed: {type(e).__name__}: {e}")
            traceback.print_exc()
        err = {"error": {
            "message": f"request translation failed: {e}",
            "type": "invalid_request_error", "code": None}}
        return Response(content=json.dumps(err).encode(),
                        status_code=400, media_type="application/json")

    model = openai_body.get("model", "")
    wants_stream = bool(openai_body.get("stream"))
    include_usage = bool((openai_body.get("stream_options") or {}).get("include_usage"))

    # Pass through the client's model verbatim. NO override, NO fallback,
    # NO circuit breaker. Upstream errors propagate as-is to the downstream.
    effective_model = model or "claude-opus-4-7"
    anthropic_body["model"] = effective_model

    resolved_key = get_next_global_key(_extract_client_key(request))
    headers = _build_translated_anthropic_headers(
        request, resolved_key, effective_model, wants_stream
    )

    target_url = "https://anyrouter.top/v1/messages"
    max_attempts = 1  # No retry, no fallback — upstream errors propagate.
    retry_delay = 0

    if config['debug']:
        print(f"\n{'='*60}")
        print(f"[OPENAI-TRANSLATOR] Target: {target_url}")
        print(f"[OPENAI-TRANSLATOR] Client asked for model: {model!r}")
        print(f"[OPENAI-TRANSLATOR] Effective: {effective_model} | Stream: {wants_stream}")
        print(f"[OPENAI-TRANSLATOR] Shape: {error_shape}")

    for attempt in range(max_attempts):
        try:
            if wants_stream:
                q = thread_queue.Queue(maxsize=256)
                t = threading.Thread(
                    target=_stream_worker,
                    args=(SESSION, "POST", target_url, headers, anthropic_body, q),
                    daemon=True,
                )
                t.start()
                loop = asyncio.get_running_loop()
                msg_type, value = await loop.run_in_executor(None, q.get)
                if msg_type == "error":
                    raise value
                status_code = value
                if config['debug']:
                    print(f"[OPENAI-TRANSLATOR] upstream status: {status_code} (model={effective_model})")

                if status_code in (520, 502, 503, 403, 429) and attempt < max_attempts - 1:
                    # No fallback; just drain and retry on same model
                    while True:
                        mt, _ = await loop.run_in_executor(None, q.get)
                        if mt in ("end", "error"):
                            break
                    SESSION = create_session()
                    await asyncio.sleep(retry_delay)
                    continue

                if status_code != 200:
                    # collect upstream error body, translate to OpenAI shape
                    chunks = []
                    while True:
                        mt, v = await loop.run_in_executor(None, q.get)
                        if mt == "data":
                            chunks.append(v if isinstance(v, bytes) else v.encode())
                        elif mt in ("end", "error"):
                            break
                    err_body = anthropic_error_to_openai_error(
                        b"".join(chunks), error_shape)
                    return Response(content=json.dumps(err_body).encode(),
                                    status_code=status_code,
                                    media_type="application/json")

                machine = stream_machine_factory(effective_model, include_usage)
                return StreamingResponse(
                    _translated_stream(q, machine),
                    status_code=200, media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )
            else:
                resp = await asyncio.to_thread(
                    SESSION.request, "POST", target_url,
                    headers=headers, json=anthropic_body, timeout=600,
                )
                if config['debug']:
                    print(f"[OPENAI-TRANSLATOR] upstream status: {resp.status_code} (model={effective_model})")

                if resp.status_code in (520, 502, 503, 403, 429) and attempt < max_attempts - 1:
                    # No fallback; bare retry only if max_attempts > 1.
                    SESSION = create_session()
                    await asyncio.sleep(retry_delay)
                    continue

                if resp.status_code != 200:
                    err_body = anthropic_error_to_openai_error(
                        resp.content, error_shape)
                    return Response(content=json.dumps(err_body).encode(),
                                    status_code=resp.status_code,
                                    media_type="application/json")

                try:
                    a_body = resp.json()
                except Exception as e:
                    err = {"error": {
                        "message": f"upstream returned non-JSON 200: {e}",
                        "type": "upstream_error", "code": None}}
                    return Response(content=json.dumps(err).encode(),
                                    status_code=502, media_type="application/json")

                try:
                    openai_resp = response_translator(a_body, effective_model)
                except Exception as e:
                    if config['debug']:
                        print(f"[OPENAI-TRANSLATOR] response translation failed: {type(e).__name__}: {e}")
                        traceback.print_exc()
                    err = {"error": {
                        "message": f"response translation failed: {e}",
                        "type": "upstream_error", "code": None}}
                    return Response(content=json.dumps(err).encode(),
                                    status_code=502, media_type="application/json")

                return Response(content=json.dumps(openai_resp).encode(),
                                status_code=200, media_type="application/json")
        except Exception as e:
            if config['debug']:
                print(f"[OPENAI-TRANSLATOR] attempt {attempt+1} error: {type(e).__name__}: {e}")
                traceback.print_exc()
            if attempt < max_attempts - 1:
                SESSION = create_session()
                await asyncio.sleep(retry_delay)
                continue
            err = {"error": {
                "message": str(e), "type": "upstream_error", "code": None}}
            return Response(content=json.dumps(err).encode(),
                            status_code=500, media_type="application/json")


async def _passthrough_to_upstream_responses(request: Request):
    """For non-Claude models (gpt-5.5/codex, gemini-*) the proxy cannot
    translate into Anthropic Messages — upstream /v1/messages 404s those
    models. Empirically the upstream /v1/responses endpoint accepts them
    when (a) input is a list, (b) stream=true. So we passthrough the OpenAI
    Responses request directly, normalize input shape, force stream=true,
    and stream the SSE response back.
    """
    global SESSION
    body = await request.body()
    try:
        openai_body = json.loads(body) if body else {}
    except Exception as e:
        return Response(
            content=json.dumps({"error": {
                "message": f"invalid JSON body: {e}",
                "type": "invalid_request_error"}}).encode(),
            status_code=400, media_type="application/json")

    upstream_body = _normalize_responses_input_for_upstream(openai_body)
    resolved_key = get_next_global_key(_extract_client_key(request))
    headers = {
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
    }
    if resolved_key:
        headers["Authorization"] = f"Bearer {resolved_key}"
        headers["x-api-key"] = resolved_key

    target_url = "https://anyrouter.top/v1/responses"
    if config['debug']:
        print(f"\n{'='*60}")
        print(f"[PASSTHROUGH-RESP] Target: {target_url}")
        print(f"[PASSTHROUGH-RESP] Model: {upstream_body.get('model', 'N/A')} | forced stream=true")

    max_attempts = 3
    retry_delay = 1
    for attempt in range(max_attempts):
        try:
            q = thread_queue.Queue(maxsize=256)
            t = threading.Thread(
                target=_stream_worker,
                args=(SESSION, "POST", target_url, headers, upstream_body, q),
                daemon=True,
            )
            t.start()
            loop = asyncio.get_running_loop()
            msg_type, value = await loop.run_in_executor(None, q.get)
            if msg_type == "error":
                raise value
            status_code = value
            if config['debug']:
                print(f"[PASSTHROUGH-RESP] upstream status: {status_code}")

            if status_code in (520, 502, 503, 403, 429) and attempt < max_attempts - 1:
                while True:
                    mt, _ = await loop.run_in_executor(None, q.get)
                    if mt in ("end", "error"):
                        break
                SESSION = create_session()
                await asyncio.sleep(retry_delay)
                continue

            if status_code != 200:
                chunks = []
                while True:
                    mt, v = await loop.run_in_executor(None, q.get)
                    if mt == "data":
                        chunks.append(v if isinstance(v, bytes) else v.encode())
                    elif mt in ("end", "error"):
                        break
                err_body = anthropic_error_to_openai_error(
                    b"".join(chunks), "response")
                return Response(content=json.dumps(err_body).encode(),
                                status_code=status_code,
                                media_type="application/json")

            # Stream the upstream SSE directly back to the client (no translation).
            return StreamingResponse(
                _passthrough_stream(q),
                status_code=200, media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        except Exception as e:
            if config['debug']:
                print(f"[PASSTHROUGH-RESP] attempt {attempt+1} error: {type(e).__name__}: {e}")
                traceback.print_exc()
            if attempt < max_attempts - 1:
                SESSION = create_session()
                await asyncio.sleep(retry_delay)
                continue
            err = {"error": {
                "message": str(e), "type": "upstream_error", "code": None}}
            return Response(content=json.dumps(err).encode(),
                            status_code=500, media_type="application/json")


async def _passthrough_stream(q):
    """Drain upstream SSE bytes unchanged."""
    loop = asyncio.get_running_loop()
    while True:
        msg_type, value = await loop.run_in_executor(None, q.get)
        if msg_type == "end":
            break
        if msg_type == "error":
            if config['debug']:
                print(f"[PASSTHROUGH-RESP] stream error: {value}")
            break
        if msg_type == "data":
            yield value


@app.post("/v1/chat/completions")
async def openai_chat_completions(request: Request):
    """OpenAI Chat Completions endpoint.

    Translates the OpenAI request into Anthropic Messages format and forwards
    it to upstream `/v1/messages`. The `model` field is passed through verbatim.
    Upstream errors (503, 404, etc.) propagate to the client as-is.
    """
    return await _run_openai_translated_request(
        request,
        request_translator=chat_completions_to_anthropic,
        response_translator=anthropic_to_chat_completion,
        stream_machine_factory=lambda model, include_usage: AnthropicSSEToChatCompletionsStream(
            requested_model=model, include_usage=include_usage),
        error_shape="chat.completion",
    )


@app.post("/v1/responses")
async def openai_responses(request: Request):
    """OpenAI Responses API endpoint.

    Translates the OpenAI Responses request into Anthropic Messages format
    and forwards it to upstream `/v1/messages`. The `model` field is passed
    through verbatim. Upstream errors propagate to the client as-is.
    """
    return await _run_openai_translated_request(
        request,
        request_translator=responses_request_to_anthropic,
        response_translator=anthropic_to_responses_response,
        stream_machine_factory=lambda model, _iu: AnthropicSSEToResponsesStream(
            requested_model=model),
        error_shape="response",
    )

@app.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
async def proxy(path: str, request: Request):
    global SESSION
    # Normalize base URL candidates
    base = config['target_base_url'].rstrip('/')
    if "betterclau.de" in base:
        base = "https://anyrouter.top"
    elif base.endswith('/v1'):
        base = base[:-3]
    candidate_urls = [f"{base}/v1/{path}"]

    # --- Inbound request logging ---
    client_ip = request.client.host if request.client else "unknown"
    print(f"\n{'='*60}")
    print(f"[INBOUND] {request.method} /v1/{path} from {client_ip}")
    print(f"[INBOUND] Headers:")
    for k, v in request.headers.items():
        if k.lower() in ("x-api-key", "authorization"):
            print(f"  {k}: {v[:12]}...{v[-4:]}" if len(v) > 20 else f"  {k}: ***")
        else:
            print(f"  {k}: {v}")

    body = await request.body()
    body_json = {}
    wants_stream = False
    if body:
        try:
            body_json = json.loads(body)
            model = body_json.get('model', '')
            if 'anyrouter/' in model:
                body_json['model'] = model.replace('anyrouter/', '')

            print(f"[INBOUND] Body keys: {list(body_json.keys())}")
            print(f"[INBOUND] model={model}, tools={len(body_json.get('tools', []))}, "
                  f"system={'yes' if body_json.get('system') else 'no'}, "
                  f"stream={body_json.get('stream', False)}")

            # 检测客户端类型：有 tools 或 system 的是富客户端（Xcode/Claude Code），直接透传
            is_bare_client = not body_json.get('tools') and not body_json.get('system')
            is_claude_model = any(k in model.lower() for k in ('sonnet', 'opus', 'haiku'))
            is_test_probe = body_json.get('max_tokens') is not None and isinstance(body_json.get('max_tokens'), int) and body_json.get('max_tokens') <= 10

            if is_bare_client and is_claude_model and CLAUDE_CODE_TOOLS and not is_test_probe:
                # 裸客户端（curl/OpenCode 等）：注入 Claude Code 伪装模板
                body_json['tools'] = CLAUDE_CODE_TOOLS
                if CLAUDE_CODE_SYSTEM:
                    body_json['system'] = CLAUDE_CODE_SYSTEM
                if 'thinking' not in body_json:
                    body_json['thinking'] = {"type": "adaptive"}
                if 'context_management' not in body_json:
                    body_json['context_management'] = {"edits": [{"type": "clear_thinking_20251015", "keep": "all"}]}
                if 'output_config' not in body_json:
                    body_json['output_config'] = {"effort": "medium"}
                print(f"[INBOUND] Client type: BARE → injected Claude Code camouflage")
            else:
                print(f"[INBOUND] Client type: RICH → passthrough body as-is")

            # 始终确保 metadata.user_id（伪装必需）
            if 'metadata' not in body_json:
                body_json['metadata'] = {"user_id": _make_user_id()}
            elif 'user_id' not in body_json.get('metadata', {}):
                body_json['metadata']['user_id'] = _make_user_id()

            wants_stream = body_json.get('stream', False)
        except Exception as e:
            if config['debug']:
                print(f"[PROXY] Body parse error: {e}")
    model_name = body_json.get('model', '')
    headers = get_claude_headers(is_stream=wants_stream, model=model_name, client_headers=dict(request.headers))
    # Pass through client's API key to upstream, with pool round-robin support
    req_api_key = request.headers.get("x-api-key", "")
    if not req_api_key:
        bearer = request.headers.get("Authorization", "")
        if bearer.startswith("Bearer "):
            req_api_key = bearer[7:]

    resolved_key = get_next_global_key(req_api_key)
    if resolved_key:
        headers["x-api-key"] = resolved_key
        headers["Authorization"] = f"Bearer {resolved_key}"
    print(f"[OUTBOUND] Target: {candidate_urls[0]}")
    print(f"[OUTBOUND] Headers:")
    for k, v in headers.items():
        if k.lower() in ("x-api-key", "authorization"):
            print(f"  {k}: {v[:12]}...{v[-4:]}" if len(v) > 20 else f"  {k}: ***")
        else:
            print(f"  {k}: {v}")
    print(f"[OUTBOUND] model={body_json.get('model', 'N/A')}, stream={wants_stream}, "
          f"body_bytes={len(json.dumps(body_json)) if body_json else 0}, TLS=curl_cffi/chrome")
    max_attempts = 5
    retry_delay = 1
    for attempt in range(max_attempts):
        # Choose candidate URL based on attempt
        target_url = candidate_urls[0]
        if attempt > 0 and len(candidate_urls) > 1:
            target_url = candidate_urls[1]

        # NOTE: ?beta=true removed — it triggers upstream 520 (Cloudflare).
        # 1m context is activated via anthropic-beta header instead.

        attempt_start = time.time()
        try:
            print(f"[PROXY] Attempt {attempt + 1}/{max_attempts} to {target_url}...")
            sys.stdout.flush()

            if wants_stream:
                q = thread_queue.Queue(maxsize=256)
                t = threading.Thread(
                    target=_stream_worker,
                    args=(SESSION, request.method, target_url, headers, body_json, q),
                    daemon=True,
                )
                t.start()
                loop = asyncio.get_running_loop()
                msg_type, value = await loop.run_in_executor(None, q.get)
                if msg_type == "error":
                    raise value
                status_code = value
                elapsed = time.time() - attempt_start
                print(f"[PROXY] Status: {status_code} (took {elapsed:.2f}s, stream)")

                # If status_code indicates error (>= 400), retrieve full content and return direct response
                if status_code >= 400:
                    chunks = []
                    while True:
                        mt, v = await loop.run_in_executor(None, q.get)
                        if mt == "data":
                            chunks.append(v if isinstance(v, bytes) else v.encode())
                        elif mt in ("end", "error"):
                            break
                    error_content = b"".join(chunks)
                    if config['debug']:
                        print(f"[PROXY] Upstream error {status_code} — passing through to client (took {elapsed:.2f}s)")
                    return Response(content=error_content, status_code=status_code, media_type="application/json")

                return StreamingResponse(
                    _async_chunks(q), status_code=status_code, media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )
            else:
                resp = await asyncio.to_thread(
                    SESSION.request, request.method, target_url,
                    headers=headers, json=body_json, timeout=600,
                )
                elapsed = time.time() - attempt_start
                print(f"[PROXY] Status: {resp.status_code} (took {elapsed:.2f}s)")
                return Response(content=resp.content, status_code=resp.status_code, media_type="application/json")
        except Exception as e:
            if config['debug']:
                print(f"[PROXY] Error: {type(e).__name__}: {e}")
                traceback.print_exc()
            if attempt < max_attempts - 1:
                SESSION = create_session()
            else:
                return Response(content=json.dumps({"error": {"message": str(e)}}), status_code=500)

# --- Auth Helpers ---

def _check_auth(request: Request):
    """Check dashboard cookie session."""
    token = request.cookies.get(COOKIE_NAME)
    if token and verify_session_token(token, config.get('dashboard_secret', '')):
        return True
    return False

# --- Dashboard Routes ---

@app.get("/dashboard/login", response_class=HTMLResponse)
async def dashboard_login_page(request: Request):
    if _check_auth(request):
        return RedirectResponse("/dashboard", status_code=302)
    return HTMLResponse(get_login_html())

@app.post("/dashboard/login")
async def dashboard_login(request: Request, password: str = Form(...)):
    stored = config.get('dashboard_password', '')
    if not stored:
        return HTMLResponse(get_login_html("Dashboard password not configured"), status_code=503)
    if not verify_password(password, stored):
        return HTMLResponse(get_login_html("Incorrect password"), status_code=401)
    token = create_session_token(config['dashboard_secret'])
    resp = RedirectResponse("/dashboard", status_code=302)
    resp.set_cookie(COOKIE_NAME, token, max_age=MAX_AGE, httponly=True, samesite="lax", path="/")
    return resp

@app.post("/dashboard/logout")
async def dashboard_logout():
    resp = RedirectResponse("/dashboard/login", status_code=302)
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    if not _check_auth(request):
        return RedirectResponse("/dashboard/login", status_code=302)
    return HTMLResponse(get_dashboard_html(model_tester.MODELS))

# --- Dashboard API ---

@app.post("/api/fetch-models")
async def api_fetch_models(request: Request):
    if not _check_auth(request):
        raise HTTPException(status_code=401, detail="Not authenticated")
    api_key = request.headers.get("x-api-key", "")
    if api_key:
        req_keys = parse_api_keys(api_key)
        if req_keys:
            set_global_keys(req_keys)
            
        test_key = get_next_global_key(api_key)
        if test_key:
            fetched = await fetch_upstream_models_list(test_key)
            if fetched:
                update_models(fetched)
    return {"status": "ok", "models": MODELS}

@app.post("/api/test-all")
async def api_test_all(request: Request):
    if not _check_auth(request):
        raise HTTPException(status_code=401, detail="Not authenticated")
    api_key = request.headers.get("x-api-key", "")
    if api_key:
        req_keys = parse_api_keys(api_key)
        if req_keys:
            set_global_keys(req_keys)
    results = await test_all_models(SESSION, config, get_claude_headers, api_key)
    return results

@app.post("/api/test/{model_name}")
async def api_test_one(model_name: str, request: Request):
    if not _check_auth(request):
        raise HTTPException(status_code=401, detail="Not authenticated")
    if model_name not in MODELS:
        MODELS.append(model_name)
        test_results[model_name] = {
            "model": model_name,
            "status": "untested",
            "latency_ms": None,
            "error_message": None,
            "response_preview": None,
            "tested_at": None,
        }
    api_key = request.headers.get("x-api-key", "")
    result = await test_single_model(SESSION, config, model_name, get_claude_headers, api_key)
    return result

@app.get("/api/model-status")
async def api_model_status(request: Request):
    if not _check_auth(request):
        raise HTTPException(status_code=401, detail="Not authenticated")
    return test_results

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="AnyRouter Proxy Server")
    parser.add_argument("--setup", action="store_true", help="Run setup wizard")
    parser.add_argument("--host", type=str, default=None, help="Bind host")
    parser.add_argument("--port", type=int, default=None, help="Bind port")
    args = parser.parse_args()
    config_loaded = load_config()
    load_claude_code_templates()
    if args.host:
        config['host'] = args.host
    if args.port:
        config['port'] = args.port
    needs_setup = args.setup or (not config_loaded)
    if needs_setup:
        if sys.stdin.isatty():
            setup_wizard()
        else:
            print("[SYSTEM] Setup required but stdin is not interactive; skipping wizard.")
    host = config.get('host', '127.0.0.1')
    port = config.get('port', 8765)
    print("=" * 60)
    print("AnyRouter Proxy Server v25 (curl_cffi/chrome)")
    print("=" * 60)
    print(f"Config:    {CONFIG_FILE}")
    print(f"Target:    {config['target_base_url']}")
    print(f"Proxy:     {config['proxy_url'] if config['use_proxy'] else 'Disabled'}")
    print(f"Debug:     {'Enabled' if config['debug'] else 'Disabled'}")
    print(f"Tools:     {len(CLAUDE_CODE_TOOLS)} Claude Code tools loaded")
    print(f"TLS:       curl_cffi impersonate=chrome (Chrome JA3/JA4/H2)")
    print(f"Headers:   claude-cli/{_CLI_VERSION} (Node.js SDK {_SDK_PACKAGE_VERSION})")
    print(f"Dashboard: http://{host}:{port}/dashboard")
    print("-" * 60)
    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8')
    log_level = "info" if config['debug'] else "warning"
    try:
        uvicorn.run(app, host=host, port=port, log_level=log_level)
    except KeyboardInterrupt:
        print("\nStopping server...")
