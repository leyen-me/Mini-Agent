from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from conftest import make_config

from mini_agent.config import DefaultsConfig, ModelConfig, ProviderConfig
from mini_agent.model_gateway import ModelRequest, OpenAICompatibleGateway


def _request(model_ref: str, *, structured: dict | None = None) -> ModelRequest:
    return ModelRequest(
        session_id="s",
        turn_id="t",
        step=1,
        model_ref=model_ref,
        messages=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
        ],
        tools=[],
        reasoning_level="off",
        max_output=1024,
        prefix_text="prefix",
        prefix_hash="hash",
        structured_output=structured,
    )


@pytest.mark.asyncio
async def test_anthropic_stream_adapter(tmp_path: Path):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        lines = [
            "event: message_start",
            'data: {"type":"message_start","message":{"usage":{"input_tokens":11,"cache_read_input_tokens":3}}}',
            "event: content_block_start",
            'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
            "event: content_block_delta",
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"hi"}}',
            "event: content_block_start",
            'data: {"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"tu_1","name":"read_file"}}',
            "event: content_block_delta",
            'data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\\"path\\":\\"a.txt\\"}"}}',
            "event: message_delta",
            'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"},"usage":{"output_tokens":5}}',
        ]
        return httpx.Response(
            200, text="\n\n".join(lines) + "\n\n", headers={"content-type": "text/event-stream"}
        )

    config = make_config(
        tmp_path,
        providers=[
            ProviderConfig(
                id="anthropic",
                protocol="anthropic",
                base_url="https://api.anthropic.com",
                api_key="sk-ant",
                models=[ModelConfig(id="claude", context_window=10000, max_output=1000)],
            )
        ],
        defaults=DefaultsConfig(model="anthropic/claude", agent="build", mode="build"),
    )
    gateway = OpenAICompatibleGateway(
        config, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    chunks = [chunk async for chunk in gateway.stream(_request("anthropic/claude"))]
    assert captured["url"].endswith("/v1/messages")
    assert captured["body"]["system"] == "system"
    assert captured["body"]["messages"][0]["role"] == "user"
    completed = chunks[-1]
    assert completed.content == "hi"
    assert completed.tool_calls[0].name == "read_file"
    assert completed.usage.input_tokens == 11
    await gateway.aclose()


@pytest.mark.asyncio
async def test_openai_responses_stream_adapter_and_structured_output(tmp_path: Path):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        lines = [
            'data: {"type":"response.output_text.delta","delta":"{\\"a\\":"}',
            'data: {"type":"response.output_text.delta","delta":"1}"}',
            'data: {"type":"response.output_item.added","item":{"id":"fc_1","type":"function_call","call_id":"c1","name":"shell"}}',
            'data: {"type":"response.function_call_arguments.delta","item_id":"fc_1","delta":"{\\"command\\":\\"echo hi\\"}"}',
            'data: {"type":"response.completed","response":{"usage":{"input_tokens":20,"output_tokens":7,"input_tokens_details":{"cached_tokens":8}}}}',
        ]
        return httpx.Response(
            200, text="\n\n".join(lines) + "\n\n", headers={"content-type": "text/event-stream"}
        )

    config = make_config(
        tmp_path,
        providers=[
            ProviderConfig(
                id="openai",
                protocol="openai-responses",
                base_url="https://api.openai.com/v1",
                api_key="sk-openai",
                models=[ModelConfig(id="gpt", context_window=10000, max_output=1000)],
            )
        ],
        defaults=DefaultsConfig(model="openai/gpt", agent="build", mode="build"),
    )
    gateway = OpenAICompatibleGateway(
        config, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    request = _request("openai/gpt", structured={"type": "object", "properties": {"a": {"type": "integer"}}})
    chunks = [chunk async for chunk in gateway.stream(request)]
    assert captured["body"]["text"]["format"]["type"] == "json_schema"
    completed = chunks[-1]
    assert completed.content == '{"a":1}'
    assert completed.tool_calls[0].name == "shell"
    assert completed.usage.cache_hit_tokens == 8
    await gateway.aclose()


@pytest.mark.asyncio
async def test_deepseek_flash_sends_thinking_switch_and_effort(tmp_path: Path):
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            text='data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":2,"completion_tokens":1}}\n\ndata: [DONE]\n\n',
            headers={"content-type": "text/event-stream"},
        )

    config = make_config(
        tmp_path,
        providers=[
            ProviderConfig(
                id="deepseek",
                protocol="openai-chat",
                base_url="https://api.deepseek.com",
                api_key="sk-deepseek",
                models=[
                    ModelConfig(
                        id="deepseek-flash",
                        context_window=10_000,
                        max_output=1_000,
                        reasoning_levels=["off", "low", "medium", "high", "max"],
                    )
                ],
            )
        ],
        defaults=DefaultsConfig(model="deepseek/deepseek-flash", agent="build", mode="build"),
    )
    gateway = OpenAICompatibleGateway(
        config, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    high = _request("deepseek/deepseek-flash")
    high.reasoning_level = "medium"
    _ = [chunk async for chunk in gateway.stream(high)]
    assert bodies[0]["thinking"] == {"type": "enabled"}
    assert bodies[0]["reasoning_effort"] == "high"

    off = _request("deepseek/deepseek-flash")
    off.reasoning_level = "off"
    _ = [chunk async for chunk in gateway.stream(off)]
    assert bodies[1]["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in bodies[1]
    await gateway.aclose()
