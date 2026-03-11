#!/bin/bash

BASE_URL="http://127.0.0.1:8765"
TIMEOUT=60

MODELS=(
  "claude-3-5-haiku-20241022"
  "claude-3-5-sonnet-20241022"
  "claude-3-7-sonnet-20250219"
  "claude-haiku-4-5-20251001"
  "claude-opus-4-1-20250805"
  "claude-opus-4-6"
  "claude-sonnet-4-20250514"
)

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo "========================================"
echo " AnyRouter Bridge Test"
echo "========================================"

# Health check
echo -n "[Health] "
health=$(curl -sS --max-time 5 "$BASE_URL/health" 2>&1)
if echo "$health" | grep -q '"ok"'; then
  echo -e "${GREEN}OK${NC} $health"
else
  echo -e "${RED}FAIL${NC} $health"
  echo "Service not running. Abort."
  exit 1
fi

echo "----------------------------------------"

pass=0
fail=0
limit=0

for model in "${MODELS[@]}"; do
  echo -ne "${CYAN}[$model]${NC} "

  start=$(date +%s)
  resp=$(curl -sS --max-time "$TIMEOUT" "$BASE_URL/v1/messages" \
    -H "content-type: application/json" \
    -d "{\"model\":\"$model\",\"max_tokens\":64,\"stream\":false,\"messages\":[{\"role\":\"user\",\"content\":\"Say pong\"}]}" 2>&1)
  end=$(date +%s)
  elapsed=$((end - start))

  if echo "$resp" | grep -q '"负载已经达到上限"'; then
    echo -e "${YELLOW}RATE LIMITED${NC} (${elapsed}s)"
    ((limit++))
  elif echo "$resp" | grep -q '"error"'; then
    msg=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('error',{}).get('message','unknown'))" 2>/dev/null || echo "$resp")
    echo -e "${RED}ERROR${NC} (${elapsed}s) $msg"
    ((fail++))
  elif echo "$resp" | grep -q '"content"'; then
    # Extract text from response
    text=$(echo "$resp" | python3 -c "
import sys,json
r=json.load(sys.stdin)
for b in r.get('content',[]):
    if b.get('type')=='text':
        print(b['text'][:80])
        break
" 2>/dev/null || echo "(parsed ok)")
    echo -e "${GREEN}OK${NC} (${elapsed}s) -> $text"
    ((pass++))
  else
    echo -e "${YELLOW}UNKNOWN${NC} (${elapsed}s) ${resp:0:120}"
    ((fail++))
  fi
done

echo "========================================"
echo -e "Pass: ${GREEN}$pass${NC}  Fail: ${RED}$fail${NC}  Rate Limited: ${YELLOW}$limit${NC}  Total: ${#MODELS[@]}"
echo "========================================"
