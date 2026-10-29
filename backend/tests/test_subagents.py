from __future__ import annotations

from pathlib import Path

import pytest
from conftest import FakeResponse, make_config, run_harness

from mini_agent.config import CommandPermission, PermissionConfig


def _spec(request, index):  # noqa: ANN001
    text = "\n".join(
        str(message.get("content") or "") for message in request.messages if message.get("role") == "user"
    )
    if "子任务内容" in text:
        return FakeResponse(content="child result")
    if index == 0:
        return FakeResponse(
            tool_calls=[("spawn_agent", {"task": "子任务内容：分析文件", "background": False})]
        )
    return FakeResponse(content="parent done")


@pytest.mark.asyncio
async def test_foreground_subagent_returns_summary(tmp_path: Path):
    config = make_config(tmp_path)
    config.permissions = PermissionConfig(subagent=CommandPermission(default="allow"))
    async with run_harness(tmp_path, script=[_spec], config=config) as h:
        session_id = await h.create_session()
        await h.send(session_id, "请委派子 Agent")
        snapshot = await h.wait_idle(session_id)
        call = next(call for call in snapshot["toolCalls"] if call["name"] == "spawn_agent")
        assert call["output"]["code"] == 0
        child_id = call["output"]["data"]["childSessionId"]
        assert call["output"]["data"]["summary"] == "child result"
        child = await h.snapshot(child_id)
        assert child["session"]["parentSessionId"] == session_id
        assert child["messages"][-1]["content"]["text"] == "child result"
        assert snapshot["childSessions"][0]["id"] == child_id


def _background_spec(request, index):  # noqa: ANN001
    text = "\n".join(
        str(message.get("content") or "") for message in request.messages if message.get("role") == "user"
    )
    if "后台子任务" in text:
        return FakeResponse(content="background child result")
    if index == 0:
        return FakeResponse(
            tool_calls=[("spawn_agent", {"task": "后台子任务：慢慢分析", "background": True})]
        )
    return FakeResponse(content="parent continues")


@pytest.mark.asyncio
async def test_background_subagent_is_tracked_as_task(tmp_path: Path):
    import asyncio

    from mini_agent.config import CommandPermission, PermissionConfig

    config = make_config(tmp_path)
    config.permissions = PermissionConfig(subagent=CommandPermission(default="allow"))
    async with run_harness(tmp_path, script=[_background_spec], config=config) as h:
        session_id = await h.create_session()
        await h.send(session_id, "请后台委派")
        snapshot = await h.wait_idle(session_id)
        call = next(call for call in snapshot["toolCalls"] if call["name"] == "spawn_agent")
        assert call["output"]["code"] == 0
        data = call["output"]["data"]
        assert data["background"] is True
        assert data["taskId"].startswith("task_")
        child_id = data["childSessionId"]
        async with h.app.state.services.db.session() as db:
            from mini_agent.models import Task

            for _ in range(100):
                task = await db.get(Task, data["taskId"])
                if task and task.status in {"succeeded", "failed", "cancelled", "timeout"}:
                    break
                await asyncio.sleep(0.05)
        assert task is not None
        assert task.status == "succeeded", task.error
        child = await h.snapshot(child_id)
        assert child["messages"][-1]["content"]["text"] == "background child result"
