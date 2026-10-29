#!/usr/bin/env bash
# One-click local start: build the web UI if needed, then serve UI + API on one port.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${MINI_AGENT_PORT:-8787}"
HOST="${MINI_AGENT_HOST:-127.0.0.1}"
export MINI_AGENT_DATA_DIR="${MINI_AGENT_DATA_DIR:-$ROOT/data}"
export MINI_AGENT_PORT="$PORT"
export MINI_AGENT_HOST="$HOST"
mkdir -p "$MINI_AGENT_DATA_DIR"

if [[ ! -f "$ROOT/frontend/dist/index.html" ]] || \
   [[ -n "$(find "$ROOT/frontend/src" "$ROOT/frontend/index.html" -newer "$ROOT/frontend/dist/index.html" -print -quit 2>/dev/null)" ]]; then
  echo "→ Building web UI…"
  cd "$ROOT/frontend"
  if [[ ! -d node_modules ]]; then
    pnpm install --frozen-lockfile
  fi
  pnpm build
fi

echo "→ Mini Agent: http://$HOST:$PORT"
echo "→ Data: $MINI_AGENT_DATA_DIR"
cd "$ROOT/backend"
exec uv run uvicorn mini_agent.main:create_app --factory --host "$HOST" --port "$PORT"
