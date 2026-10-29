#!/usr/bin/env bash
# Full local quality gate. Set RUN_E2E=1 to include Playwright.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo "→ Backend lint + tests"
cd "$ROOT/backend"
uv run ruff check mini_agent tests
uv run ruff format --check mini_agent tests
uv run pytest -q
echo "→ Frontend typecheck + lint + unit tests + build"
cd "$ROOT/frontend"
pnpm typecheck
pnpm lint
pnpm test
pnpm build
if [[ "${RUN_E2E:-0}" == "1" ]]; then
  echo "→ Playwright E2E"
  pnpm exec playwright test
fi
