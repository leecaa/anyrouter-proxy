#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="anyrouter-opencode-bridge"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

echo "Stopping service..."
sudo systemctl stop "$SERVICE_NAME" 2>/dev/null || true
sudo systemctl disable "$SERVICE_NAME" 2>/dev/null || true

if [[ -f "$SERVICE_FILE" ]]; then
  sudo rm -f "$SERVICE_FILE"
  sudo systemctl daemon-reload
  echo "Removed: $SERVICE_FILE"
else
  echo "Service file not found."
fi

echo ""
echo "Uninstalled. Config and venv preserved at:"
echo "  ${XDG_CONFIG_HOME:-$HOME/.config}/anyrouter-opencode-bridge/"
