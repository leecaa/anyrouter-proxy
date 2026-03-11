#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/anyrouter-opencode-bridge"
VENV_DIR="$CONFIG_DIR/venv"
PLIST_DEST="$HOME/Library/LaunchAgents/com.anyrouter.opencode.bridge.plist"
LOG_PATH="$HOME/Library/Logs/anyrouter-opencode-bridge.log"

mkdir -p "$CONFIG_DIR"

if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$ROOT_DIR/requirements.txt"

if [[ ! -f "$CONFIG_DIR/proxy_config.json" ]]; then
  if [[ -f "$ROOT_DIR/proxy_config.example.json" ]]; then
    cp "$ROOT_DIR/proxy_config.example.json" "$CONFIG_DIR/proxy_config.json"
  else
    echo "{}" > "$CONFIG_DIR/proxy_config.json"
  fi
fi

mkdir -p "$(dirname "$PLIST_DEST")"
sed \
  -e "s|__ROOT_DIR__|$ROOT_DIR|g" \
  -e "s|__LOG_PATH__|$LOG_PATH|g" \
  "$ROOT_DIR/macos/com.anyrouter.opencode.bridge.plist" > "$PLIST_DEST"

launchctl unload "$PLIST_DEST" >/dev/null 2>&1 || true
launchctl load "$PLIST_DEST"

echo "Installed LaunchAgent: $PLIST_DEST"
echo "Config file: $CONFIG_DIR/proxy_config.json"
echo "Logs: $LOG_PATH"
echo "Status: launchctl list | grep com.anyrouter.opencode.bridge"
