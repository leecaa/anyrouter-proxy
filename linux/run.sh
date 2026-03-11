#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/anyrouter-opencode-bridge"
VENV_DIR="$CONFIG_DIR/venv"

if [[ ! -d "$VENV_DIR" ]]; then
  echo "Missing venv at $VENV_DIR. Run linux/install.sh first." >&2
  exit 1
fi

export ANYROUTER_BRIDGE_CONFIG="$CONFIG_DIR/proxy_config.json"

exec "$VENV_DIR/bin/python" "$ROOT_DIR/main.py"
