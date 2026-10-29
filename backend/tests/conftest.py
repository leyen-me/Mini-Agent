"""Shared test fixtures and a scripted model gateway."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
import uvicorn
from asgi_lifespan import LifespanManager

from mini_agent.config import (
    AppConfig,
    BudgetConfig,
    CompactionConfig,
    DefaultsConfig,
    ModelConfig,
    PermissionConfig,
    ProviderConfig,
    ServerConfig,
    TaskConfig,
)
from mini_agent.main import create_app
from mini_agent.model_gateway import BaseGateway, ModelChunk, ModelError, ModelRequest, ToolCallRequest, Usage
from mini_agent.tokens import estimate_tokens


@dataclass
class FakeResponse:
    content: str = "done"
    reasoning: str = ""
    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    delay: float = 0.0
    error: ModelError | None = None
    usage: Usage | None = None


class ScriptedGateway(BaseGateway):
    """Replays a per-session script and records each request verbatim."""

    def __init__(
        self,
        config: AppConfig,
        script: list[FakeResponse | Callable[[ModelRequest, int], FakeResponse]] | None = None,
        *,
        chunk_size: int = 6,
    ) -> None:
        super().__init__(config)
        self.script = script or [FakeResponse(content="done")]
        self.chunk_size = chunk_size
        self.requests: list[ModelRequest] = []
        self._calls: dict[str, int] = {}
        self._prefixes: list[str] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        self.requests.append(request)
        index = self._calls.get(request.session_id, 0)
        self._calls[request.session_id] = index + 1
        spec = self.script[min(index, len(self.script) - 1)]
        if callable(spec):
            spec = spec(request, index)
        if spec.error is not None:
            raise spec.error
        hit = 0
        for previous in self._prefixes:
            if request.prefix_text.startswith(previous):
                hit = max(hit, estimate_tokens(previous))
        self._prefixes.append(request.prefix_text)
        for offset in range(0, len(spec.reasoning), self.chunk_size):
            if spec.delay:
                await asyncio.sleep(spec.delay)
            yield ModelChunk(kind="reasoning", text=spec.reasoning[offset : offset + self.chunk_size])
        for offset in range(0, len(spec.content), self.chunk_size):
            if spec.delay:
                await asyncio.sleep(spec.delay)
            yield ModelChunk(kind="content", text=spec.content[offset : offset + self.chunk_size])
        calls = [
            ToolCallRequest(
                id=f"call_{index}_{position}",
                name=name,
                arguments=json.dumps(args, ensure_ascii=False),
            )
            for position, (name, args) in enumerate(spec.tool_calls)
        ]
        total_input = max(1, estimate_tokens(request.prefix_text))
        usage = spec.usage or Usage(
            input_tokens=total_input,
            output_tokens=estimate_tokens(spec.content + spec.reasoning),
            cache_hit_tokens=min(hit, total_input),
            cache_miss_tokens=max(0, total_input - min(hit, total_input)),
        )
        yield ModelChunk(
            kind="completed",
            content=spec.content,
            reasoning=spec.reasoning,
            tool_calls=calls,
            usage=usage,
            finish_reason="tool_calls" if calls else "stop",
        )


def make_config(tmp_path: Path, **overrides: Any) -> AppConfig:
    data_dir = tmp_path / "data"
    workspace_root = tmp_path / "workspace"
    model = ModelConfig(
        id="echo",
        name="Echo",
        context_window=32_000,
        max_output=4_000,
        capabilities=["text", "tools", "reasoning"],
        reasoning_levels=["off", "high"],
    )
    config = AppConfig(
        server=ServerConfig(log_level="WARNING"),
        defaults=DefaultsConfig(model="mock/echo", agent="build", mode="build"),
        providers=[
            ProviderConfig(id="mock", protocol="mock", base_url="", models=[model]),
        ],
        permissions=PermissionConfig(),
        budget=BudgetConfig(max_tool_output_chars=8_000),
        compaction=CompactionConfig(keep_recent_tokens=600),
        tasks=TaskConfig(wait_timeout_seconds=10, auto_continue_after_completion=True),
        data_dir=data_dir,
        workspace_root=workspace_root,
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


@dataclass
class Harness:
    app: Any
    client: httpx.AsyncClient
    config: AppConfig
    gateway: ScriptedGateway

    async def create_session(self, **kwargs: Any) -> str:
        response = await self.client.post("/api/v1/sessions", json=kwargs)
        assert response.status_code == 200, response.text
        return response.json()["data"]["session"]["id"]

    async def snapshot(self, session_id: str) -> dict[str, Any]:
        response = await self.client.get(f"/api/v1/sessions/{session_id}")
        assert response.status_code == 200, response.text
        return response.json()["data"]

    async def wait_for(
        self,
        session_id: str,
        predicate: Callable[[dict[str, Any]], bool],
        timeout: float = 12.0,
    ) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout
        snapshot = await self.snapshot(session_id)
        while asyncio.get_running_loop().time() < deadline:
            snapshot = await self.snapshot(session_id)
            if predicate(snapshot):
                return snapshot
            await asyncio.sleep(0.03)
        return snapshot

    async def wait_idle(self, session_id: str, timeout: float = 12.0) -> dict[str, Any]:
        return await self.wait_for(
            session_id,
            lambda snap: (
                snap["session"]["status"] in {"idle", "error", "paused"} and not snap.get("active", False)
            ),
            timeout,
        )

    async def send(self, session_id: str, content: str, **kwargs: Any) -> dict[str, Any]:
        response = await self.client.post(
            f"/api/v1/sessions/{session_id}/messages",
            json={"content": content, **kwargs},
        )
        assert response.status_code == 200, response.text
        return response.json()["data"]


@asynccontextmanager
async def run_harness(
    tmp_path: Path,
    *,
    script: list[FakeResponse | Callable[[ModelRequest, int], FakeResponse]] | None = None,
    config: AppConfig | None = None,
    chunk_size: int = 6,
    gateway: BaseGateway | None = None,
) -> AsyncIterator[Harness]:
    cfg = config or make_config(tmp_path)
    gateway = gateway or ScriptedGateway(cfg, script, chunk_size=chunk_size)
    app = create_app(cfg, gateway_override=gateway)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=30) as client:
            yield Harness(app=app, client=client, config=cfg, gateway=gateway)  # type: ignore[arg-type]


@pytest_asyncio.fixture
async def harness(tmp_path: Path):
    async with run_harness(tmp_path) as value:
        yield value


@asynccontextmanager
async def run_live_harness(
    tmp_path: Path,
    *,
    script: list[FakeResponse | Callable[[ModelRequest, int], FakeResponse]] | None = None,
    config: AppConfig | None = None,
) -> AsyncIterator[Harness]:
    """A real uvicorn server, required for streaming SSE assertions."""

    cfg = config or make_config(tmp_path)
    gateway = ScriptedGateway(cfg, script, chunk_size=6)
    app = create_app(cfg, gateway_override=gateway)
    server_config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=0,
        log_level="warning",
        access_log=False,
        lifespan="on",
    )
    server = uvicorn.Server(server_config)
    server_task = asyncio.create_task(server.serve())
    for _ in range(300):
        if server.started:
            break
        await asyncio.sleep(0.02)
    if not server.started:
        server.should_exit = True
        await server_task
        raise RuntimeError("uvicorn failed to start")
    port = server.servers[0].sockets[0].getsockname()[1]
    client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30)
    try:
        yield Harness(app=app, client=client, config=cfg, gateway=gateway)
    finally:
        await client.aclose()
        server.should_exit = True
        await asyncio.wait_for(server_task, timeout=15)


async def read_sse_events(
    client: httpx.AsyncClient,
    session_id: str,
    *,
    last_event_id: int | None = None,
    count: int = 0,
    timeout: float = 10.0,
) -> list[dict[str, Any]]:
    headers = {}
    if last_event_id is not None:
        headers["Last-Event-ID"] = str(last_event_id)
    events: list[dict[str, Any]] = []

    async def _consume() -> None:
        async with client.stream("GET", f"/api/v1/sessions/{session_id}/events", headers=headers) as response:
            assert response.status_code == 200, response.text
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                events.append(json.loads(line[5:].strip()))
                if count and len(events) >= count:
                    return

    await asyncio.wait_for(_consume(), timeout=timeout)
    return events


def pytest_configure(config: pytest.Config) -> None:
    marker = "asyncio"
    _ = marker
