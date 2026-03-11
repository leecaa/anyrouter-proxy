# AnyRouter Proxy

[English Documentation](README.md)

这是一个专为增强 API 访问和绕过常见网络限制而设计的本地桥接/代理服务。它能够将本地客户端应用程序连接到上游的 AnyRouter API，并无缝处理请求过滤、Claude 模型的工具注入（Tool Injection）以及 HTTP/2 传输。

本指南被设计为开箱即用。**您只需将本文档的链接发给任何 AI 助手（如 Claude, ChatGPT 或 Gemini），AI 即可根据此指南自主为您完成所有的配置与部署工作。**

## 核心特性

- **支持 HTTP/2：** 使用 `curl_cffi` 提供高级支持，模拟标准浏览器指纹以绕过 WAF 检查。
- **Claude 工具注入：** 无缝为 Claude 模型注入工具和 System Prompts。
- **请求过滤：** 自动移除请求中不必要的字段，防止触发上游安全拦截。
- **流式传输支持：** 完全支持流式（Streaming）对话补全。
- **Web Dashboard 面板：** 内置可视化 Web 控制台，方便实时监控代理状态与健康度。
- **多平台支持：** 支持 Windows、macOS 和 Linux。包含 macOS (`LaunchAgent`) 和 Linux (`systemd`) 的后台自动启动脚本。

## 运行环境

- Python 3.10 及以上版本
- （推荐）使用虚拟环境

## 快速开始 & 部署指南

### 1. 克隆代码库

```bash
git clone https://github.com/leecaa/anyrouter-proxy.git
cd anyrouter-proxy
```

### 2. 安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. 配置指南与端口说明

代理服务通过 JSON 配置文件（`proxy_config.json`）或环境变量（详见 `.env.example`）进行配置。

**交互式自动配置（推荐）：**
```bash
python main.py --setup
```

**手动配置：**
复制模板文件并进行编辑：
```bash
cp proxy_config.example.json proxy_config.json
```

**核心配置项与端口说明：**
- `host`: 服务绑定的本地 IP 地址。默认值为 `127.0.0.1`（仅允许本地访问）。如需局域网或公网访问，请修改为 `0.0.0.0`。
- `port`: 代理 API 服务和 Dashboard 控制台的运行端口。默认端口为 **8765**。
- `api_key`: 您的上游 AnyRouter API 密钥。这是转发请求所必需的凭证。
- `dashboard_password`: 访问 Web Dashboard 控制台的登录密码。请务必设置一个安全的密码。
- `target_base_url`: 上游 API 目标地址。默认为 `https://anyrouter.top/v1`。

### 4. 运行代理服务

```bash
python main.py
```
启动后，服务默认将监听在 `http://127.0.0.1:8765`。

### 5. 访问 Web Dashboard 控制台

代理服务运行后，您可以通过浏览器访问 Web 面板，实时查看流量、系统健康度和运行日志。

- **访问地址:** 在浏览器中打开 `http://127.0.0.1:8765`
- **登录方式:** 输入您在第 3 步中配置的 `dashboard_password` 进行登录。

### 6. （可选）配合官方 Claude Code 命令行使用

如果您正在使用 Anthropic 官方的 `claude-code` 命令行工具，该工具在代码中硬编码了只能请求 `api.anthropic.com`。为了解除此限制，我们提供了一个补丁脚本（适用于通过 npm 安装的环境）：

```bash
./patch_claude_api_host.sh
```
*(此脚本会将全局 `claude-code` npm 包内部的 `api.anthropic.com` 替换为 `anyrouter.top`。如果您升级了 claude-code，可能需要重新运行此脚本。)*

## 后台服务部署（开机自启）

### macOS (LaunchAgent)

使用我们提供的脚本，一键安装为 macOS 后台服务：
```bash
./macos/install.sh
./macos/status.sh
# 查看日志
tail -f ~/Library/Logs/anyrouter-opencode-bridge.log
```
如需卸载：`./macos/uninstall.sh`

### Linux (Systemd)

使用我们提供的脚本，一键安装为 Linux systemd 服务：
```bash
sudo ./linux/install.sh
sudo ./linux/status.sh
# 查看日志
journalctl -u anyrouter-opencode-bridge -f
```
如需卸载：`sudo ./linux/uninstall.sh`

## 安全建议

请务必阅读 [SECURITY.md](SECURITY.md) 安全指南。若将服务绑定到 `0.0.0.0` 并暴露在公网，必须配置 `api_auth_key` 并在客户端请求中携带该 Auth Token，否则代理将被滥用。切勿将您的 `.env` 或 `proxy_config.json` 提交到公开代码库。

## 开源协议

本项目采用 MIT 协议开源 - 详情请参阅 [LICENSE](LICENSE) 文件。