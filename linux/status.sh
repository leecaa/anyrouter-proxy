#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="anyrouter-opencode-bridge"

echo "=== $SERVICE_NAME Status ==="
systemctl status "$SERVICE_NAME" --no-pager 2>&1 || true
echo ""
echo "Recent logs:"
journalctl -u "$SERVICE_NAME" --no-pager -n 20 2>/dev/null || true
