from __future__ import annotations

from pathlib import Path

import pytest
from conftest import FakeResponse, make_config, run_harness

from mini_agent.config import ModelConfig, ProviderConfig
from mini_agent.model_gateway import ModelError


@pytest.mark.asyncio
async def test_runtime_falls_back_to_secondary_model(tmp_path: Path):
    primary = ModelConfig(id="echo", context_window=32_000, max_output=4_000)
    secondary = ModelConfig(id="backup", context_window=32_000, max_output=4_000)
    config = make_config(
        tmp_path,
        providers=[ProviderConfig(id="mock", protocol="mock", base_url="", models=[primary, secondary])],
    )
    config.defaults.fallback_models = ["mock/backup"]

    def spec(request, index):  # noqa: ANN001
        if request.model_ref == "mock/echo":
            raise ModelError("PROVIDER_UNAVAILABLE", "primary down", retryable=False)
        return FakeResponse(content="fallback answer")

    async with run_harness(tmp_path, script=[spec], config=config) as h:
        session_id = await h.create_session()
        await h.send(session_id, "hello")
        snapshot = await h.wait_idle(session_id)
        assert snapshot["messages"][-1]["content"]["text"] == "fallback answer"
        models = [request.model_ref for request in h.gateway.requests]
        assert models[0] == "mock/echo"
        assert models[1] == "mock/backup"
