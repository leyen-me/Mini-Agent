.PHONY: setup dev start test lint build e2e docker

setup:
	cd backend && uv sync --all-groups
	cd frontend && pnpm install --frozen-lockfile

dev:
	./scripts/dev.sh

start:
	./scripts/start.sh

lint:
	cd backend && uv run ruff check mini_agent tests && uv run ruff format --check mini_agent tests
	cd frontend && pnpm typecheck && pnpm lint

test:
	./scripts/test.sh

build:
	cd frontend && pnpm build

e2e:
	cd frontend && pnpm exec playwright test

docker:
	docker compose up --build
