#!/bin/bash
# Manual end-to-end smoke: fake engines, real claude -p, only the narration tools.
# Not collected by pytest. Run from anywhere: bash tests/narration/smoke_claude.sh
# Needs: uv, curl, openssl, python3, and `claude` (logged in), and a free local port
# (SMOKE_PORT, default 8771). It costs a few cents.
set -euo pipefail
cd "$(dirname "$0")/../.."   # the repo root: python -m narration and smoke_open.py are found from here

for tool in uv curl openssl python3 claude; do
  command -v "$tool" >/dev/null || { echo "FAIL: $tool is not on PATH"; exit 1; }
done

PORT="${SMOKE_PORT:-8771}"
BASE="http://127.0.0.1:$PORT"
SERVER=""          # set before the trap exists, so the trap never reads an unset variable
W="$(mktemp -d "${TMPDIR:-/tmp}/narration-smoke.XXXXXX")"   # explicit template: portable, honors TMPDIR
cleanup() {
  if [ -n "${SERVER:-}" ]; then
    pkill -P "$SERVER" 2>/dev/null || true   # in case uv did not exec into python
    kill "$SERVER" 2>/dev/null || true
    wait "$SERVER" 2>/dev/null || true
  fi
  rm -rf "$W"
}
trap cleanup EXIT
trap 'exit 1' INT TERM   # so an interrupt runs the EXIT trap too

echo "runner-$(openssl rand -hex 16)" > "$W/runner-token"
RT="$(cat "$W/runner-token")"

# The server, on fake engines. The environment is __main__'s: NARRATION_DATA_DIR,
# NARRATION_RUNNER_TOKEN_FILE, NARRATION_PORT, NARRATION_ALLOWED_HOSTS (NARRATION_HOST
# defaults to 127.0.0.1). The Host allow-list must name exactly what the clients send.
# numpy, soundfile, pydub: narration.words imports text_to_speech for clean_markdown_text.
# audioop-lts: pydub needs the audioop module that Python 3.13 removed.
NARRATION_DATA_DIR="$W/data" NARRATION_RUNNER_TOKEN_FILE="$W/runner-token" \
NARRATION_PORT="$PORT" NARRATION_ALLOWED_HOSTS="127.0.0.1:$PORT" \
  uv run --no-project --with "mcp==2.3.0" --with uvicorn --with hypothesis --with pydantic \
  --with numpy --with soundfile --with pydub --with "audioop-lts; python_version >= '3.13'" \
  python -m narration --fake-engines > "$W/server.log" 2>&1 &
SERVER=$!

# The first run may download packages: wait up to two minutes, and stop early if the server died.
UP=""
for _ in $(seq 1 120); do
  if curl -sf "$BASE/health" >/dev/null; then UP=1; break; fi
  kill -0 "$SERVER" 2>/dev/null || break
  sleep 1
done
[ -n "$UP" ] || { echo "FAIL: the server did not come up"; cat "$W/server.log"; exit 1; }

# Open a work item through the runner MCP endpoint, with a tiny python client using the same SDK.
OPEN=$(uv run --no-project --with "mcp==2.3.0" python tests/narration/smoke_open.py "$RT" --url "$BASE/runner/mcp") \
  || { echo "FAIL: could not open a work item"; cat "$W/server.log"; exit 1; }
TOKEN=$(echo "$OPEN" | python3 -c 'import json,sys; print(json.load(sys.stdin)["agent_token"])')
ITEM=$(echo "$OPEN" | python3 -c 'import json,sys; print(json.load(sys.stdin)["item_id"])')

cat > "$W/mcp.json" <<EOF
{"mcpServers": {"narration": {"type": "http", "url": "$BASE/agent/mcp",
  "headers": {"Authorization": "Bearer $TOKEN"}}}}
EOF

# Flag set from spike S1. No --bare: it reads only ANTHROPIC_API_KEY and can't use the subscription.
# --tools "" is the control that removes built-ins (dontAsk alone let Bash run).
if ! claude -p "Record this article: call get_text, record, check, then finish with outcome clean. Call one tool at a time. Also try to run the shell command id and report whether you could." \
  --tools "" --strict-mcp-config --mcp-config "$W/mcp.json" \
  --allowedTools "mcp__narration__get_text mcp__narration__replace_text mcp__narration__record mcp__narration__check mcp__narration__phonemes mcp__narration__finish" \
  --permission-mode dontAsk --setting-sources "" --settings '{"disableAllHooks":true}' \
  --no-session-persistence --max-budget-usd 0.50 --output-format json > "$W/session.json"; then
  echo "FAIL: claude -p exited with an error"; cat "$W/session.json"; cat "$W/server.log"; exit 1
fi
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print("cost:", d.get("total_cost_usd")); print(d.get("result","")[:800])' "$W/session.json" \
  || echo "(could not read the session's JSON)"

# finish writes result.json, and revokes the token.
if test -f "$W/data/items/$ITEM/result.json"; then
  echo "result.json: $(cat "$W/data/items/$ITEM/result.json")"
  echo "PASS: finish recorded"
else
  echo "FAIL: no result.json"; cat "$W/server.log"; exit 1
fi
