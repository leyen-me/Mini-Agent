from __future__ import annotations

from pathlib import Path

import pytest
from conftest import FakeResponse, run_harness


@pytest.mark.asyncio
async def test_skill_enable_and_load_tool(tmp_path: Path):
    responses = [FakeResponse(content="placeholder")]
    async with run_harness(tmp_path, script=responses) as h:
        session_id = await h.create_session()
        enabled = await h.client.post(
            f"/api/v1/sessions/{session_id}/skills/code-review/toggle", json={"enabled": True}
        )
        assert enabled.status_code == 200, enabled.text
        snapshot = await h.snapshot(session_id)
        assert any(item["name"] == "code-review" and item["enabled"] for item in snapshot["skills"])
        h.gateway.script = [
            FakeResponse(tool_calls=[("load_skill", {"name": "code-review"})]),
            FakeResponse(content="loaded"),
        ]
        h.gateway._calls[session_id] = 0
        await h.send(session_id, "加载 skill")
        snapshot = await h.wait_idle(session_id)
        call = next(call for call in snapshot["toolCalls"] if call["name"] == "load_skill")
        assert call["output"]["code"] == 0
        assert "Code Review" in call["output"]["data"]["content"]


@pytest.mark.asyncio
async def test_system_skill_create_and_delete(tmp_path: Path):
    async with run_harness(tmp_path) as h:
        created = await h.client.post(
            "/api/v1/skills",
            json={
                "scope": "system",
                "name": "release-notes",
                "description": "生成发布说明",
                "content": "---\nname: release-notes\ndescription: 生成发布说明\n---\n\n按版本整理。",
            },
        )
        assert created.status_code == 200, created.text
        skills = (await h.client.get("/api/v1/skills")).json()["data"]
        assert any(item["name"] == "release-notes" for item in skills)
        deleted = await h.client.delete("/api/v1/skills/system/release-notes")
        assert deleted.status_code == 200
