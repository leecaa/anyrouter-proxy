# AnyRouter Proxy

> Inspired by [dalia-abdallah25/anyrouter-opencode-bridge](https://github.com/dalia-abdallah25/anyrouter-opencode-bridge)

[中文文档](README_zh.md)

A local bridge/proxy service designed to enhance API access and bypass common restrictions. It connects local client applications to the upstream AnyRouter API, handling request filtering, tool injection for Claude models, and HTTP/2 transport seamlessly.

This README is designed to be fully self-contained. You can provide this file to any AI assistant (like Claude, ChatGPT, or Gemini), and it will be able to autonomously configure and deploy the service for you.

## Features

- **HTTP/2 Support:** Advanced support using `curl_cffi` to mimic standard browser fingerprints and bypass WAF checks.
- **Claude Tool Injection:** Seamlessly injects tools and system prompts for Claude models.
- **Request Filtering:** Removes unnecessary fields from requests that might trigger upstream security blocking.
- **Streaming & Non-Streaming:** Fully supports streaming chat completions.
- **Dashboard GUI:** Includes a built-in web dashboard to monitor proxy status and health.
- **Multi-Platform Support:** Runs on Windows, macOS, and Linux. Included launch scripts for macOS (`LaunchAgent`) and Linux (`systemd`).

## Prerequisites

- Python 3.10+
- (Optional but Recommended) A virtual environment

## Quick Start & Deployment Guide

### 1. Clone the Repository

```bash
git clone https://github.com/leecaa/anyrouter-proxy.git
cd anyrouter-proxy
```

### 2. Install Dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configuration & Ports

The proxy is configured via a JSON file (`proxy_config.json`) or environment variables (see `.env.example`). 

**Interactive Setup (Recommended):**
```bash
python main.py --setup
```

**Manual Setup:**
Copy the template and edit it:
```bash
cp proxy_config.example.json proxy_config.json
```

**Key Configurations & Ports:**
- `host`: The local IP address to bind to. Default is `127.0.0.1` (only accessible locally). Set to `0.0.0.0` if you need to access it from other machines.
- `port`: The port the proxy and dashboard will run on. Default is **8765**.
- `api_key`: Your upstream AnyRouter API Key. Required to forward requests successfully.
- `dashboard_password`: The password required to access the Web Dashboard. Make sure to set a secure password.
- `target_base_url`: Upstream API URL. Default is `https://anyrouter.top/v1`.

### 4. Run the Proxy Server

```bash
python main.py
```
The server will start listening on `http://127.0.0.1:8765` by default.

### 5. Access the Web Dashboard

Once the proxy is running, you can access the Web Dashboard from your browser to monitor traffic, health, and logs.

- **URL:** Open `http://127.0.0.1:8765` in your web browser.
- **Login:** Enter the `dashboard_password` you configured in Step 3.

### 6. (Optional) Using with Claude Code CLI

If you use the Anthropic official `claude-code` CLI, it has a hardcoded restriction requiring `api.anthropic.com`. To bypass this, a patch script is provided for node-installed environments:

```bash
./patch_claude_api_host.sh
```
*(This script replaces `api.anthropic.com` with `anyrouter.top` inside the global `claude-code` npm package. You may need to re-run it after updating claude-code.)*

## Running as a Background Service

### macOS (LaunchAgent)

```bash
./macos/install.sh
./macos/status.sh
tail -f ~/Library/Logs/anyrouter-opencode-bridge.log
```
To uninstall: `./macos/uninstall.sh`

### Linux (Systemd)

```bash
sudo ./linux/install.sh
sudo ./linux/status.sh
journalctl -u anyrouter-opencode-bridge -f
```
To uninstall: `sudo ./linux/uninstall.sh`

## Security

Please review the [SECURITY.md](SECURITY.md) guidelines. Never expose your local proxy (`0.0.0.0`) to the public internet without proper authentication (`api_auth_key`). Do not commit your `.env` or `proxy_config.json` files.

## License

MIT License - see the [LICENSE](LICENSE) file for details.