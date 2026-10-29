# syntax=docker/dockerfile:1.7
FROM node:22-alpine AS web
WORKDIR /app/frontend
RUN corepack enable && corepack prepare pnpm@10.13.1 --activate
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

FROM python:3.13-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MINI_AGENT_DATA_DIR=/data \
    MINI_AGENT_HOST=0.0.0.0 \
    MINI_AGENT_PORT=8787
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev
COPY backend/ ./
COPY --from=web /app/frontend/dist /app/frontend/dist
RUN useradd --create-home --uid 10001 agent \
    && mkdir -p /data /app/frontend/dist \
    && chown -R agent:agent /data /app
USER agent
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8787/api/v1/healthz', timeout=3).status==200 else 1)"
CMD ["/app/backend/.venv/bin/uvicorn", "mini_agent.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8787"]
