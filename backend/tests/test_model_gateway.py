"""Model gateway retry, error mapping and deterministic prefix serialization."""

from __future__ import annotations

import json
import random
from pathlib import Path

import httpx
import pytest
from conftest import run_harness

from mini_agent.config import (
    AppConfig,
    DefaultsConfig,
    ModelConfig,
    ProviderConfig,
    ServerConfig,
)
from mini_agent.model_gateway import (
    ModelError,
    ModelRequest,
    OpenAICompatibleGateway,
    messages_prefix_text,
)
from mini_agent.tokens import estimate_tokens


def gateway_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        server=ServerConfig(log_level="WARNING"),
        defaults=DefaultsConfig(model="test/chat"),
        providers=[
            ProviderConfig(
                id="test",
                name="Test",
                protocol="openai-chat",
                base_url="https://api.example.com/v1",
                api_key="sk-test-key-123456",
                models=[ModelConfig(id="chat", name="Chat", context_window=8192)],
            )
        ],
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "ws",
    )


def make_request() -> ModelRequest:
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
    ]
    prefix_text, prefix_hash = messages_prefix_text(messages, [])
    return ModelRequest(
        session_id="sess_test",
        turn_id="turn_test",
        step=1,
        model_ref="test/chat",
        messages=messages,
        tools=[],
        reasoning_level="off",
        max_output=1000,
        prefix_text=prefix_text,
        prefix_hash=prefix_hash,
    )


def sse_response(content: str = "hello") -> httpx.Response:
    first = {
        "choices": [
            {"index": 0, "delta": {"role": "assistant", "content": content[:2]}, "finish_reason": None}
        ]
    }
    second = {"choices": [{"index": 0, "delta": {"content": content[2:]}, "finish_reason": "stop"}]}
    usage = {
        "choices": [],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 3,
            "prompt_cache_hit_tokens": 60,
            "prompt_cache_miss_tokens": 40,
        },
    }
    body = "\n\n".join(
        [
            f"data: {json.dumps(first)}",
            f"data: {json.dumps(second)}",
            f"data: {json.dumps(usage)}",
            "data: [DONE]",
            "",
        ]
    )
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


@pytest.mark.asyncio
async def test_retry_backoff_and_retry_after(tmp_path: Path):
    attempts = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        assert request.url.path.endswith("/chat/completions")
        if attempts <= 2:
            return httpx.Response(503, json={"error": {"code": "overloaded", "message": "busy"}})
        return sse_response("hello")

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = OpenAICompatibleGateway(
        gateway_config(tmp_path), client=client, sleep=fake_sleep, rng=random.Random(7)
    )
    chunks = [chunk async for chunk in gateway.stream(make_request())]
    await client.aclose()
    assert attempts == 3
    assert len(delays) == 2
    assert 0.8 <= delays[0] <= 1.2
    assert 1.6 <= delays[1] <= 2.4
    assert [chunk.kind for chunk in chunks].count("retry") == 2
    final = chunks[-1]
    assert final.kind == "completed"
    assert final.content == "hello"
    assert final.usage is not None
    assert final.usage.cache_hit_tokens == 60
    assert final.usage.cache_miss_tokens == 40


@pytest.mark.asyncio
async def test_retry_after_header_is_honoured(tmp_path: Path):
    attempts = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(
                429,
                json={"error": {"code": "rate_limit", "message": "slow down"}},
                headers={"retry-after": "2.5"},
            )
        return sse_response("ok")

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = OpenAICompatibleGateway(gateway_config(tmp_path), client=client, sleep=fake_sleep)
    _ = [chunk async for chunk in gateway.stream(make_request())]
    await client.aclose()
    assert delays == [2.5]


@pytest.mark.asyncio
async def test_retry_exhaustion_reports_readable_error_and_raw_code(tmp_path: Path):
    attempts = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            500,
            json={"error": {"code": "server_error", "message": "exploded"}},
            headers={"x-request-id": "req-42"},
        )

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = OpenAICompatibleGateway(gateway_config(tmp_path), client=client, sleep=fake_sleep)
    with pytest.raises(ModelError) as error:
        _ = [chunk async for chunk in gateway.stream(make_request())]
    await client.aclose()
    assert attempts == 4  # initial + 3 retries
    assert error.value.code == "PROVIDER_UNAVAILABLE"
    assert error.value.raw_code == "server_error"
    assert error.value.request_id == "req-42"
    assert "模型服务" in error.value.message
    assert len(delays) == 3


@pytest.mark.asyncio
async def test_auth_error_is_not_retried(tmp_path: Path):
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(401, json={"error": {"code": "invalid_api_key", "message": "bad key"}})

    async def no_sleep(_delay: float) -> None:
        raise AssertionError("auth errors must not sleep/retry")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = OpenAICompatibleGateway(gateway_config(tmp_path), client=client, sleep=no_sleep)
    with pytest.raises(ModelError) as error:
        _ = [chunk async for chunk in gateway.stream(make_request())]
    await client.aclose()
    assert attempts == 1
    assert error.value.code == "AUTH_FAILED"
    assert error.value.retryable is False


def test_prefix_serialization_is_append_only_and_deterministic():
    tools = [
        {"type": "function", "function": {"name": "b", "parameters": {"type": "object"}}},
        {"type": "function", "function": {"name": "a", "parameters": {"type": "object"}}},
    ]
    first = [{"role": "system", "content": "s"}, {"role": "user", "content": "one"}]
    second = first + [
        {"role": "assistant", "content": "two"},
        {"role": "user", "content": "three"},
    ]
    text1, hash1 = messages_prefix_text(first, tools)
    text1_again, hash1_again = messages_prefix_text(first, tools)
    text2, hash2 = messages_prefix_text(second, tools)
    assert text1 == text1_again
    assert hash1 == hash1_again
    assert text2.startswith(text1)
    assert hash1 != hash2
    assert estimate_tokens(text1) > 0


@pytest.mark.asyncio
async def test_runtime_discards_partial_output_when_model_restarts(tmp_path: Path):
    from mini_agent.model_gateway import BaseGateway, ModelChunk, Usage

    class RestartGateway(BaseGateway):
        async def stream(self, request):
            yield ModelChunk(kind="content", text="不完整")
            yield ModelChunk(kind="retry", attempt=1, reason="流中断", error_code="NETWORK_ERROR")
            yield ModelChunk(kind="content", text="完整输出")
            yield ModelChunk(
                kind="completed",
                content="完整输出",
                usage=Usage(input_tokens=10, output_tokens=4, cache_hit_tokens=5, cache_miss_tokens=5),
            )

    async with run_harness(tmp_path, gateway=RestartGateway(gateway_config(tmp_path))) as h:
        session_id = await h.create_session()
        await h.send(session_id, "重试")
        await h.wait_idle(session_id)
        snap = await h.snapshot(session_id)
        assistant = [m for m in snap["messages"] if m["role"] == "assistant"][-1]
        assert assistant["content"]["text"] == "完整输出"
        assert "不完整" not in assistant["content"]["text"]
        ids = [row.id for row in await _events(h, session_id)]
        assert ids == sorted(set(ids))


async def _events(h, session_id: str):
    from sqlalchemy import select

    from mini_agent.models import Event

    async with h.app.state.services.db.session() as db:
        return (await db.execute(select(Event).where(Event.session_id == session_id))).scalars().all()
