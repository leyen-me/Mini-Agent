from __future__ import annotations

import httpx
import pytest
from asgi_lifespan import LifespanManager
from conftest import make_config

from mini_agent.main import create_app


@pytest.mark.asyncio
async def test_remote_requests_require_static_token(tmp_path):
    config = make_config(tmp_path)
    config.api.token = "s3cr3t"
    config.api.require_token_remote = True
    app = create_app(config)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app, client=("198.51.100.5", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            denied = await client.get("/api/v1/healthz")
            assert denied.status_code == 401
            spoofed = await client.get("/api/v1/healthz", headers={"X-Forwarded-For": "127.0.0.1"})
            assert spoofed.status_code == 401
            allowed = await client.get("/api/v1/healthz", headers={"Authorization": "Bearer s3cr3t"})
            assert allowed.status_code == 200
        local = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
        async with httpx.AsyncClient(transport=local, base_url="http://test") as client:
            assert (await client.get("/api/v1/healthz")).status_code == 200


@pytest.mark.asyncio
async def test_rate_limit_returns_429(tmp_path):
    config = make_config(tmp_path)
    config.api.rate_limit_per_minute = 1
    app = create_app(config)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.get("/api/v1/sessions")
            assert first.status_code == 200
            second = await client.get("/api/v1/sessions")
            assert second.status_code == 429
            assert second.headers["Retry-After"] == "60"
