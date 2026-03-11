import json
import hmac
import hashlib
import sys
import os
import secrets
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
from model_tester import MODELS, test_results, test_single_model, test_all_models

def resolve_config_path():
    env_path = os.environ.get("ANYROUTER_BRIDGE_CONFIG")
    if env_path:
        return os.path.abspath(os.path.expanduser(env_path))
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if not xdg_config:
        xdg_config = os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(xdg_config, "anyrouter-opencode-bridge", "proxy_config.json")

CONFIG_FILE = resolve_config_path()

DEFAULT_CONFIG = {
    "api_key": "",
    "proxy_url": "http://127.0.0.1:2080",
    "use_proxy": True,
    "debug": False,
    "target_base_url": "https://anyrouter.top/v1",
    "host": "127.0.0.1",
    "port": 8765,
    "dashboard_password": "",
    "dashboard_secret": "",
    "api_auth_key": ""
}

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
])
_ANTHROPIC_BETA_BASIC = "interleaved-thinking-2025-05-14"

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

def load_config():
    global config
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                loaded_config = json.load(f)
            config = DEFAULT_CONFIG.copy()
            config.update(loaded_config)
            print(f"[SYSTEM] Configuration loaded from {CONFIG_FILE}")
            return True
        except Exception as e:
            print(f"[SYSTEM] Error loading config: {e}")
            config = DEFAULT_CONFIG.copy()
            return False
    else:
        config = DEFAULT_CONFIG.copy()
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
    current_key = config.get('api_key', '')
    masked_key = f"{current_key[:8]}...{current_key[-4:]}" if len(current_key) > 12 else current_key
    api_key = input(f"Enter AnyRouter API Key [{masked_key}]: ").strip()
    if api_key:
        config['api_key'] = api_key
    elif not current_key:
        print("Warning: API Key is empty!")
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

    # If client already sends Claude CLI headers, prefer its values for
    # evolving fields (beta flags may be newer, retry count is per-request).
    if client_headers:
        client_beta = client_headers.get("anthropic-beta")
        if client_beta and len(client_beta) > len(beta):
            headers["anthropic-beta"] = client_beta
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
    if len(safe_config.get('api_key', '')) > 10:
        safe_config['api_key'] = safe_config['api_key'][:8] + "..." + safe_config['api_key'][-4:]
    safe_config.pop('dashboard_secret', None)
    safe_config.pop('dashboard_password', None)
    safe_config.pop('api_auth_key', None)
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

@app.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
async def proxy(path: str, request: Request):
    global SESSION
    auth_key = config.get('api_auth_key', '')
    if auth_key:
        req_key = request.headers.get("x-api-key", "")
        if not req_key:
            bearer = request.headers.get("Authorization", "")
            if bearer.startswith("Bearer "):
                req_key = bearer[7:]
        if not hmac.compare_digest(req_key.encode(), auth_key.encode()):
            return Response(
                content=json.dumps({"error": {"type": "authentication_error", "message": "invalid api key"}}),
                status_code=401,
                media_type="application/json",
            )
    target_url = f"{config['target_base_url']}/{path}"
    if path == "messages":
        target_url += "?beta=true"
    body = await request.body()
    body_json = {}
    wants_stream = False
    if body:
        try:
            body_json = json.loads(body)
            safe_keys = {
                'model', 'messages', 'max_tokens', 'metadata', 'stop_sequences',
                'stream', 'system', 'temperature', 'top_k', 'top_p',
                'tools', 'tool_choice', 'thinking', 'service_tier',
                'context_management', 'output_config',
            }
            filtered_body = {k: v for k, v in body_json.items() if k in safe_keys}
            model = filtered_body.get('model', '')
            if 'anyrouter/' in model:
                filtered_body['model'] = model.replace('anyrouter/', '')
            if config['debug']:
                print(f"[PROXY] Original request keys: {list(body_json.keys())}")
                print(f"[PROXY] Has tools: {'tools' in body_json}, tools count: {len(body_json.get('tools', []))}")
                print(f"[PROXY] Has system: {'system' in body_json}")
                print(f"[PROXY] Has thinking: {'thinking' in body_json}")
            if ('sonnet' in model.lower() or 'opus' in model.lower() or 'haiku' in model.lower()) and CLAUDE_CODE_TOOLS:
                filtered_body['tools'] = CLAUDE_CODE_TOOLS
                if config['debug']:
                    print(f"[PROXY] Injected {len(CLAUDE_CODE_TOOLS)} Claude Code tools")
                if CLAUDE_CODE_SYSTEM:
                    filtered_body['system'] = CLAUDE_CODE_SYSTEM
                    if config['debug']:
                        print(f"[PROXY] Injected Claude Code system prompt")
                if 'sonnet' in model.lower() or 'opus' in model.lower():
                    if 'thinking' not in filtered_body:
                        filtered_body['thinking'] = {"type": "adaptive"}
                        if config['debug']:
                            print(f"[PROXY] Injected thinking config (adaptive)")
                    if 'context_management' not in filtered_body:
                        filtered_body['context_management'] = {"edits": [{"type": "clear_thinking_20251015", "keep": "all"}]}
                    if 'output_config' not in filtered_body:
                        filtered_body['output_config'] = {"effort": "medium"}
                if 'metadata' not in filtered_body:
                    filtered_body['metadata'] = {"user_id": _make_user_id()}
            wants_stream = filtered_body.get('stream', False)
            body_json = filtered_body
        except Exception as e:
            if config['debug']:
                print(f"[PROXY] Body parse error: {e}")
    model_name = body_json.get('model', '')
    headers = get_claude_headers(is_stream=wants_stream, model=model_name, client_headers=dict(request.headers))
    req_auth = request.headers.get("Authorization")
    if config['api_key']:
        headers["x-api-key"] = config['api_key']
    elif req_auth:
        api_key_from_auth = req_auth.replace("Bearer ", "")
        headers["x-api-key"] = api_key_from_auth
    if config['debug']:
        print(f"\n{'='*60}")
        print(f"[PROXY] Target: {target_url}")
        print(f"[PROXY] Model: {body_json.get('model', 'N/A')}")
        print(f"[PROXY] Stream: {wants_stream}")
        print(f"[PROXY] TLS: curl_cffi/chrome")
    max_attempts = 5
    retry_delay = 1
    for attempt in range(max_attempts):
        try:
            if config['debug']:
                print(f"[PROXY] Attempt {attempt + 1}/{max_attempts}...")
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
                if config['debug']:
                    print(f"[PROXY] Status: {status_code}")
                if status_code in [520, 502]:
                    # drain queue
                    while True:
                        mt, _ = await loop.run_in_executor(None, q.get)
                        if mt in ("end", "error"):
                            break
                    if attempt < max_attempts - 1:
                        SESSION = create_session()
                        await asyncio.sleep(retry_delay)
                        continue
                    return Response(content=b'{"error":{"message":"Network error after max retries"}}', status_code=502, media_type="application/json")
                if status_code in [403, 500]:
                    chunks = []
                    while True:
                        mt, v = await loop.run_in_executor(None, q.get)
                        if mt == "data":
                            chunks.append(v if isinstance(v, bytes) else v.encode())
                        elif mt in ("end", "error"):
                            break
                    error_content = b"".join(chunks)
                    if config['debug']:
                        print(f"[PROXY] Error response: {error_content.decode('utf-8', errors='ignore')[:500]}")
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
                if config['debug']:
                    print(f"[PROXY] Status: {resp.status_code}")
                if resp.status_code in [520, 502]:
                    if attempt < max_attempts - 1:
                        SESSION = create_session()
                        await asyncio.sleep(retry_delay)
                        continue
                    return Response(content=b'{"error":{"message":"Network error after max retries"}}', status_code=502, media_type="application/json")
                if resp.status_code in [403, 500]:
                    return Response(content=resp.content, status_code=resp.status_code, media_type="application/json")
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
    """Check dashboard cookie OR api_auth_key (x-api-key / Bearer)."""
    token = request.cookies.get(COOKIE_NAME)
    if token and verify_session_token(token, config.get('dashboard_secret', '')):
        return True
    auth_key = config.get('api_auth_key', '')
    if auth_key:
        req_key = request.headers.get("x-api-key", "")
        if not req_key:
            bearer = request.headers.get("Authorization", "")
            if bearer.startswith("Bearer "):
                req_key = bearer[7:]
        if req_key and hmac.compare_digest(req_key.encode(), auth_key.encode()):
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
    return HTMLResponse(get_dashboard_html(MODELS))

# --- Dashboard API ---

@app.post("/api/test-all")
async def api_test_all(request: Request):
    if not _check_auth(request):
        raise HTTPException(status_code=401, detail="Not authenticated")
    results = await test_all_models(SESSION, config, get_claude_headers)
    return results

@app.post("/api/test/{model_name}")
async def api_test_one(model_name: str, request: Request):
    if not _check_auth(request):
        raise HTTPException(status_code=401, detail="Not authenticated")
    if model_name not in MODELS:
        raise HTTPException(status_code=404, detail=f"Unknown model: {model_name}")
    result = await test_single_model(SESSION, config, model_name, get_claude_headers)
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
    needs_setup = args.setup or (not config_loaded) or (not config.get('api_key'))
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
