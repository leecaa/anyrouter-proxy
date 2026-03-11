#!/bin/bash

# Find global node_modules directory
NPM_ROOT=$(npm root -g 2>/dev/null)
if [ -z "$NPM_ROOT" ]; then
  echo "Error: npm is not installed or npm root -g failed."
  exit 1
fi

CLAUDE_DIR="$NPM_ROOT/@anthropic-ai/claude-code"

if [ ! -d "$CLAUDE_DIR" ]; then
  echo "Error: claude-code is not installed globally at $CLAUDE_DIR"
  echo "Please install it via: npm install -g @anthropic-ai/claude-code"
  exit 1
fi

TARGET_HOST="${API_HOST:-anyrouter.top}"

echo "Patching Claude Code to use $TARGET_HOST instead of api.anthropic.com..."

# Find all javascript files containing api.anthropic.com and replace it
# Backup files will be created with .bak extension
find "$CLAUDE_DIR" -type f -name "*.js" -exec grep -l "api\.anthropic\.com" {} + | while read -r file; do
  echo "Patching: $file"
  sed -i.bak "s/api\.anthropic\.com/$TARGET_HOST/g" "$file"
done

echo "Patch applied successfully! You can re-run this script after upgrading claude-code."
