#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/anyrouter-opencode-bridge"
VENV_DIR="$CONFIG_DIR/venv"
SERVICE_NAME="anyrouter-opencode-bridge"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
CURRENT_USER="$(whoami)"

echo "========================================"
echo " AnyRouter Bridge - Linux Install"
echo "========================================"

# Create config directory
mkdir -p "$CONFIG_DIR"

# Create venv
if [[ ! -d "$VENV_DIR" ]]; then
  echo "[1/5] Creating Python venv..."
  python3 -m venv "$VENV_DIR"
else
  echo "[1/5] Venv already exists."
fi

# Install dependencies
echo "[2/5] Installing dependencies..."
"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install -r "$ROOT_DIR/requirements.txt" -q

# Copy config if not present
if [[ ! -f "$CONFIG_DIR/proxy_config.json" ]]; then
  echo "[3/5] Creating default config..."
  if [[ -f "$ROOT_DIR/proxy_config.example.json" ]]; then
    cp "$ROOT_DIR/proxy_config.example.json" "$CONFIG_DIR/proxy_config.json"
  else
    echo '{}' > "$CONFIG_DIR/proxy_config.json"
  fi
else
  echo "[3/5] Config already exists."
fi

# Install systemd unit
echo "[4/5] Installing systemd service..."
TEMP_SERVICE=$(mktemp)
sed \
  -e "s|__USER__|$CURRENT_USER|g" \
  -e "s|__ROOT_DIR__|$ROOT_DIR|g" \
  -e "s|__VENV_DIR__|$VENV_DIR|g" \
  -e "s|__CONFIG_DIR__|$CONFIG_DIR|g" \
  "$ROOT_DIR/linux/anyrouter-opencode-bridge.service" > "$TEMP_SERVICE"

sudo cp "$TEMP_SERVICE" "$SERVICE_FILE"
rm -f "$TEMP_SERVICE"
sudo systemctl daemon-reload

# Enable and start
echo "[5/5] Starting service..."
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

echo ""
echo "========================================"
echo " Install complete!"
echo "========================================"
echo "Config:  $CONFIG_DIR/proxy_config.json"
echo "Logs:    sudo journalctl -u $SERVICE_NAME -f"
echo "Status:  sudo systemctl status $SERVICE_NAME"
echo "Stop:    sudo systemctl stop $SERVICE_NAME"
echo "========================================"
