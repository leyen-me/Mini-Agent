from __future__ import annotations

from pathlib import Path

import pytest
from conftest import FakeResponse, make_config, run_harness


@pytest.mark.asyncio
async def test_manual_compaction_uses_model_summary(tmp_path: Path):
    config = make_config(tmp_path)
    config.compaction.use_model = True
    config.compaction.model = "mock/echo"
    script = [
        FakeResponse(content="第一轮内容" * 60),
        FakeResponse(content="模型生成的压缩摘要"),
    ]
    async with run_harness(tmp_path, script=script, config=config) as h:
        session_id = await h.create_session()
        await h.send(session_id, "开始")
        await h.wait_idle(session_id)
        response = await h.client.post(f"/api/v1/sessions/{session_id}/compact")
        assert response.status_code == 200, response.text
        assert response.json()["data"]["summary"] == "模型生成的压缩摘要"


@pytest.mark.asyncio
async def test_compaction_reloads_agents_md_into_frozen_prompt(tmp_path: Path):
    from conftest import FakeResponse, make_config, run_harness

    config = make_config(tmp_path)
    config.compaction.use_model = False
    workspace = Path(config.workspace_root)
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "AGENTS.md").write_text("# OLD-RULE\n", encoding="utf-8")
    script = [FakeResponse(content="旧回复" * 80), FakeResponse(content="继续")]
    async with run_harness(tmp_path, script=script, config=config) as h:
        session_id = await h.create_session()
        await h.send(session_id, "开始")
        await h.wait_idle(session_id)
        before = await h.snapshot(session_id)
        assert "OLD-RULE" in before["systemPrompt"]
        (workspace / "AGENTS.md").write_text("# NEW-RULE\n", encoding="utf-8")
        response = await h.client.post(f"/api/v1/sessions/{session_id}/compact")
        assert response.status_code == 200, response.text
        after = await h.snapshot(session_id)
        assert "NEW-RULE" in after["systemPrompt"]
        assert "OLD-RULE" not in after["systemPrompt"]
