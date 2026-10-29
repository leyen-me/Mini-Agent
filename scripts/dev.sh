#!/usr/bin/env bash
# Hot-reload development: FastAPI on 8787 + Vite on 5173.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export MINI_AGENT_DATA_DIR="${MINI_AGENT_DATA_DIR:-$ROOT/data}"
export MINI_AGENT_PORT="${MINI_AGENT_PORT:-8787}"
mkdir -p "$MINI_AGENT_DATA_DIR"
cleanup() {
  if [[ -n "${BACK_PID:-}" ]]; then kill "$BACK_PID" 2>/dev/null || true; fi
}
trap cleanup EXIT INT TERM
cd "$ROOT/backend"
uv run uvicorn mini_agent.main:create_app --factory --host 127.0.0.1 --port "$MINI_AGENT_PORT" --reload &
BACK_PID=$!
cd "$ROOT/frontend"
pnpm dev
