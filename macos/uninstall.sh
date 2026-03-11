#!/usr/bin/env bash
set -euo pipefail

PLIST_DEST="$HOME/Library/LaunchAgents/com.anyrouter.opencode.bridge.plist"

if [[ -f "$PLIST_DEST" ]]; then
  launchctl unload "$PLIST_DEST" >/dev/null 2>&1 || true
  rm -f "$PLIST_DEST"
  echo "Removed LaunchAgent: $PLIST_DEST"
else
  echo "LaunchAgent not found: $PLIST_DEST"
fi

echo "Note: config and venv remain in ~/.config/anyrouter-opencode-bridge"
