from __future__ import annotations

from pathlib import Path

import pytest
from conftest import FakeResponse, run_harness


@pytest.mark.asyncio
async def test_session_start_hook_runs_and_receives_event(tmp_path: Path):
    async with run_harness(tmp_path, script=[FakeResponse(content="ok")]) as h:
        marker = Path(h.config.workspace_root) / "hook-event.txt"
        created = await h.client.post(
            "/api/v1/hooks",
            json={
                "name": "marker",
                "event": "session.start",
                "command": f'sh -c \'printf "%s" "$MINI_AGENT_EVENT" > {marker}\'',
            },
        )
        assert created.status_code == 200, created.text
        session_id = await h.create_session()
        assert marker.exists(), "hook did not run"
        assert marker.read_text() == "session.start"
        snapshot = await h.snapshot(session_id)
        assert snapshot["session"]["id"] == session_id
