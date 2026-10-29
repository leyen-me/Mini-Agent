#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/frontend"
pnpm build >/dev/null
rm -rf /tmp/mini-agent-e2e
export MINI_AGENT_DATA_DIR=/tmp/mini-agent-e2e
export MINI_AGENT_PORT=8788
cd "$ROOT/backend"
exec uv run uvicorn mini_agent.main:create_app --factory --host 127.0.0.1 --port 8788 --log-level warning
