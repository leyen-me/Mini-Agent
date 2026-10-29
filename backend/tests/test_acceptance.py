"""Appendix D acceptance checks, exercised through the real HTTP runtime."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager
from conftest import FakeResponse, read_sse_events, run_harness, run_live_harness
from sqlalchemy import func, select

from mini_agent.config import CommandPermission, PermissionConfig
from mini_agent.main import create_app
from mini_agent.models import (
    Event,
    Message,
    ModelRequest,
    PendingInteraction,
    Task,
    new_id,
    utcnow,
)


@pytest.mark.asyncio
async def test_a1_refresh_restores_stream_queue_and_interactions(tmp_path: Path):
    slow = FakeResponse(content="流式内容 " * 120, delay=0.012)
    script = [slow, FakeResponse(content="排队后的回答")]
    async with run_harness(tmp_path, script=script) as h:
        session_id = await h.create_session()
        await h.send(session_id, "开始流式输出", clientMessageId="first")
        snap = await h.wait_for(
            session_id,
            lambda s: any(
                m["role"] == "assistant" and m["status"] == "streaming" and m["content"]["text"]
                for m in s["messages"]
            ),
        )
        streaming = [m for m in snap["messages"] if m["status"] == "streaming"]
        assert streaming and streaming[0]["role"] == "assistant"
        assert snap["lastEventId"] > 0
        queued = await h.send(session_id, "这是排队消息", mode="queue", clientMessageId="queued")
        assert queued["queued"] is True
        snap = await h.snapshot(session_id)
        assert len(snap["queue"]) == 1
        assert snap["queue"][0]["content"] == "这是排队消息"
        snap = await h.wait_idle(session_id, timeout=20)
        assert snap["session"]["status"] == "idle"
        assert snap["queue"] == []
        assert any("排队后的回答" in m["content"]["text"] for m in snap["messages"])


@pytest.mark.asyncio
async def test_a1_ask_question_and_permission_survive_snapshot(tmp_path: Path):
    ask_script = [
        FakeResponse(
            tool_calls=[
                (
                    "ask_question",
                    {
                        "questions": [
                            {
                                "id": "q1",
                                "question": "选一个",
                                "type": "single",
                                "options": [{"value": "a", "label": "A"}],
                            }
                        ]
                    },
                )
            ]
        ),
        FakeResponse(content="回答已收到"),
    ]
    async with run_harness(tmp_path, script=ask_script) as h:
        session_id = await h.create_session()
        await h.send(session_id, "问我")
        snap = await h.wait_for(session_id, lambda s: bool(s["interactions"]))
        assert snap["session"]["status"] == "waiting_user"
        interaction = snap["interactions"][0]
        assert interaction["type"] == "ask_question"
        # Reload equivalent: the pending interaction is part of the snapshot.
        again = await h.snapshot(session_id)
        assert again["interactions"][0]["id"] == interaction["id"]
        response = await h.client.post(
            f"/api/v1/sessions/{session_id}/interactions/{interaction['id']}/answer",
            json={"answer": {"answers": {"q1": "a"}}},
        )
        assert response.status_code == 200, response.text
        snap = await h.wait_idle(session_id)
        assert snap["session"]["status"] == "idle"
        assert snap["interactions"] == []

    permission_script = [
        FakeResponse(tool_calls=[("write_file", {"path": "note.txt", "content": "hi"})]),
        FakeResponse(content="写入了文件"),
    ]
    async with run_harness(tmp_path / "perm", script=permission_script) as h:
        session_id = await h.create_session()
        await h.send(session_id, "写文件")
        snap = await h.wait_for(session_id, lambda s: bool(s["interactions"]))
        interaction = snap["interactions"][0]
        assert interaction["type"] == "permission"
        assert interaction["payload"]["tool"] == "write_file"
        again = await h.snapshot(session_id)
        assert again["interactions"][0]["id"] == interaction["id"]
        response = await h.client.post(
            f"/api/v1/sessions/{session_id}/interactions/{interaction['id']}/answer",
            json={"answer": {"action": "deny", "reason": "测试拒绝"}},
        )
        assert response.status_code == 200
        snap = await h.wait_idle(session_id)
        denied = [c for c in snap["toolCalls"] if c["name"] == "write_file"][0]
        assert denied["status"] == "denied"
        assert denied["output"]["errorCode"] == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_a1_sse_last_event_id_replays_without_duplicates(tmp_path: Path):
    async with run_live_harness(tmp_path, script=[FakeResponse(content="实时事件")]) as h:
        session_id = await h.create_session()
        await h.send(session_id, "生成事件")
        await h.wait_idle(session_id)
        total = await _event_count(h, session_id)
        events = await read_sse_events(h.client, session_id, count=total)
        ids = [event["id"] for event in events]
        assert ids == sorted(set(ids))
        assert len(ids) == total
        midpoint = ids[len(ids) // 2]
        remaining = await _event_count(h, session_id, after=midpoint)
        replay = await read_sse_events(h.client, session_id, last_event_id=midpoint, count=remaining)
        assert [event["id"] for event in replay] == sorted(set(event["id"] for event in replay))
        assert all(event["id"] > midpoint for event in replay)
        assert [event["id"] for event in events if event["id"] > midpoint] == [
            event["id"] for event in replay
        ]


@pytest.mark.asyncio
async def test_a2_restart_marks_work_failed_and_notifies(tmp_path: Path):
    from conftest import make_config

    config = make_config(tmp_path)
    app = create_app(config)
    async with LifespanManager(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/api/v1/sessions", json={})
            session_id = response.json()["data"]["session"]["id"]
    # Simulate a process that died while work was in flight.
    db_path = config.data_dir / "mini-agent.sqlite3"
    con = sqlite3.connect(db_path)
    turn_id = new_id("turn")
    message_id = new_id("msg")
    call_id = new_id("call")
    call_id2 = new_id("call")
    task_id = new_id("task")
    interaction_id = new_id("int")
    now = utcnow()
    con.execute(
        "insert into turn (id,session_id,seq,status,started_at,usage_json) values (?,?,?,?,?,?)",
        (turn_id, session_id, 1, "running", now, "{}"),
    )
    con.execute(
        "insert into message (id,session_id,turn_id,seq,role,content_json,raw_json,status,created_at) values (?,?,?,?,?,?,?,?,?)",
        (
            message_id,
            session_id,
            turn_id,
            1,
            "assistant",
            json.dumps({"text": "部分输出", "reasoning": "", "tool_calls": []}),
            json.dumps(
                {
                    "role": "assistant",
                    "content": "部分输出",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": "shell", "arguments": '{"command":"sleep 99"}'},
                        },
                        {
                            "id": call_id2,
                            "type": "function",
                            "function": {"name": "read_file", "arguments": '{"path":"README.md"}'},
                        },
                    ],
                }
            ),
            "streaming",
            now,
        ),
    )
    con.execute(
        "insert into tool_call (id,session_id,turn_id,message_id,name,input_json,status,risk) values (?,?,?,?,?,?,?,?)",
        (call_id, session_id, turn_id, message_id, "shell", '{"command":"sleep 99"}', "running", "high"),
    )
    con.execute(
        "insert into task (id,session_id,tool_call_id,name,status,progress,summary,notified,created_at,updated_at) values (?,?,?,?,?,?,?,?,?,?)",
        (task_id, session_id, call_id, "长任务", "running", 10, "", 0, now, now),
    )
    con.execute(
        "insert into pending_interaction (id,session_id,turn_id,type,payload,status,created_at) values (?,?,?,?,?,?,?)",
        (interaction_id, session_id, turn_id, "permission", "{}", "pending", now),
    )
    con.execute("update session set status='running' where id=?", (session_id,))
    con.commit()
    con.close()

    app2 = create_app(config)
    async with LifespanManager(app2):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app2), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/v1/sessions/{session_id}")
            data = response.json()["data"]
            assert data["session"]["status"] == "error"
            assert data["turns"][-1]["status"] == "failed"
            assert data["turns"][-1]["errorCode"] == "SERVER_RESTARTED"
            task = data["tasks"][0]
            assert task["status"] == "failed"
            call = data["toolCalls"][0]
            assert call["status"] == "cancelled"
            assert call["output"]["errorCode"] == "SERVER_RESTARTED"
            tool_messages = [m for m in data["messages"] if m["role"] == "tool"]
            assert (
                tool_messages
                and json.loads(tool_messages[0]["content"]["text"])["errorCode"] == "SERVER_RESTARTED"
            )
            assert data["interactions"] == []
            notices = [m["content"]["text"] for m in data["messages"] if m["role"] == "system"]
            assert any("服务重启" in text and task_id in text for text in notices)


@pytest.mark.asyncio
async def test_a3_pause_aborts_tools_and_fills_results(tmp_path: Path):
    from conftest import make_config

    config = make_config(tmp_path)
    config.permissions = PermissionConfig(shell=CommandPermission(default="allow"))
    script = [
        FakeResponse(
            content="运行一个长命令",
            tool_calls=[
                ("shell", {"command": "sleep 10; echo never"}),
                ("read_file", {"path": "README.md"}),
            ],
        ),
        FakeResponse(content="不会到这里"),
    ]
    async with run_harness(tmp_path / "pause", script=script, config=config) as h:
        session_id = await h.create_session()
        await h.send(session_id, "运行")
        snap = await h.wait_for(
            session_id,
            lambda s: bool(s["toolCalls"]) and any(c["status"] == "running" for c in s["toolCalls"]),
            timeout=10,
        )
        response = await h.client.post(f"/api/v1/sessions/{session_id}/pause")
        assert response.status_code == 200
        snap = await h.wait_for(session_id, lambda s: s["session"]["status"] == "paused")
        assert snap["turns"][-1]["status"] == "paused"
        assert len(snap["toolCalls"]) == 2
        for call in snap["toolCalls"]:
            assert call["output"] is not None
            assert call["output"]["errorCode"] == "USER_PAUSED"
            assert call["status"] == "cancelled"
        tool_messages = [m for m in snap["messages"] if m["role"] == "tool"]
        assert len(tool_messages) == 2
        ids = {json.loads(m["content"]["text"])["errorCode"] for m in tool_messages}
        assert ids == {"USER_PAUSED"}


@pytest.mark.asyncio
async def test_a4_five_turn_prefix_is_byte_stable_and_cache_grows(tmp_path: Path):
    script = [FakeResponse(content="回答" + "内容" * 30) for _ in range(5)]
    async with run_harness(tmp_path, script=script) as h:
        session_id = await h.create_session()
        for index in range(5):
            await h.send(session_id, f"第 {index + 1} 轮", clientMessageId=f"m{index}")
            await h.wait_idle(session_id)
        assert len(h.gateway.requests) == 5
        prefixes = [request.prefix_text for request in h.gateway.requests]
        for previous, current in zip(prefixes, prefixes[1:], strict=False):
            assert current.startswith(previous), "previous request must be an exact byte prefix"
        async with h.app.state.services.db.session() as db:
            rows = (
                (await db.execute(select(ModelRequest).order_by(ModelRequest.created_at.asc())))
                .scalars()
                .all()
            )
        hits = [row.cache_hit_tokens for row in rows]
        assert hits == sorted(hits)
        assert hits[0] == 0
        assert hits[-1] > 0
        assert len({row.prefix_hash for row in rows}) == 5


@pytest.mark.asyncio
async def test_a6_secrets_absent_from_export_share_and_logs(tmp_path: Path, capsys):
    from conftest import make_config

    sentinel = "sk-super-secret-value-1234567890"
    config = make_config(tmp_path)
    config.providers[0].api_key = sentinel
    async with run_harness(
        tmp_path / "secrets",
        script=[FakeResponse(content=f"模型回复包含 {sentinel}")],
        config=config,
    ) as h:
        session_id = await h.create_session()
        await h.send(session_id, f"请不要泄漏 {sentinel}")
        await h.wait_idle(session_id)
        export = await h.client.get(f"/api/v1/sessions/{session_id}/export?format=json")
        assert export.status_code == 200
        assert sentinel not in export.text
        assert "[REDACTED]" in export.text
        markdown = await h.client.get(f"/api/v1/sessions/{session_id}/export?format=markdown")
        assert sentinel not in markdown.text
        created = await h.client.post(f"/api/v1/sessions/{session_id}/shares", json={})
        assert created.status_code == 200
        token = created.json()["data"]["token"]
        shared = await h.client.get(f"/api/v1/shares/{token}")
        assert shared.status_code == 200
        assert sentinel not in shared.text
        assert "systemPrompt" not in shared.json()["data"]
        # The structured log formatter is configured with the registry secret.
        import logging

        from mini_agent.logging_utils import JsonFormatter

        record = logging.LogRecord(
            "mini_agent.test", logging.INFO, __file__, 1, "leaking %s", (sentinel,), None
        )
        formatted = JsonFormatter().format(record)
        assert sentinel not in formatted
        assert "[REDACTED]" in formatted


@pytest.mark.asyncio
async def test_a7_permission_denial_prevents_identical_retry(tmp_path: Path):
    from conftest import make_config

    config = make_config(tmp_path)
    config.permissions = PermissionConfig(shell=CommandPermission(default="ask"))
    command = "echo should-not-run"
    script = [
        FakeResponse(tool_calls=[("shell", {"command": command})]),
        FakeResponse(tool_calls=[("shell", {"command": command})]),
        FakeResponse(content="已停止重试"),
        FakeResponse(content="完成"),
    ]
    async with run_harness(tmp_path / "deny", script=script, config=config) as h:
        session_id = await h.create_session()
        await h.send(session_id, "执行")
        snap = await h.wait_for(session_id, lambda s: bool(s["interactions"]))
        interaction = snap["interactions"][0]
        await h.client.post(
            f"/api/v1/sessions/{session_id}/interactions/{interaction['id']}/answer",
            json={"answer": {"action": "deny"}},
        )
        snap = await h.wait_idle(session_id)
        assert snap["session"]["status"] == "idle"
        calls = [c for c in snap["toolCalls"] if c["name"] == "shell"]
        assert len(calls) == 2
        assert calls[0]["status"] == "denied"
        assert calls[1]["status"] == "denied"
        assert calls[1]["output"]["errorCode"] == "REPEATED_CALL"
        assert "should-not-run" not in (calls[1]["output"].get("data") or {}).get("output", "")
        interactions = snap["interactions"]
        assert interactions == []
        # Only one permission prompt was ever created; the retry was blocked by the runtime.
        async with h.app.state.services.db.session() as db:
            total = (
                await db.execute(
                    select(func.count(PendingInteraction.id)).where(
                        PendingInteraction.session_id == session_id
                    )
                )
            ).scalar_one()
        assert total == 1


@pytest.mark.asyncio
async def test_a8_long_task_notifies_model_for_self_check(tmp_path: Path):
    from conftest import make_config

    config = make_config(tmp_path)
    config.permissions = PermissionConfig(shell=CommandPermission(default="allow"))
    observed: list[bool] = []

    def third_response(request, index: int) -> FakeResponse:
        _ = index
        last = request.messages[-1]
        observed.append(
            last.get("role") == "system"
            and "后台任务完成通知" in str(last.get("content", ""))
            and "taskId=" in str(last.get("content", ""))
            and "自检" in str(last.get("content", ""))
        )
        return FakeResponse(content="任务完成自检通过")

    script = [
        FakeResponse(
            tool_calls=[
                (
                    "shell_background",
                    {
                        "command": "python3 -c \"import time; time.sleep(1); print('task-ok')\"",
                        "description": "验收后台任务",
                    },
                )
            ]
        ),
        FakeResponse(content="先结束本轮"),
        third_response,
    ]
    async with run_harness(tmp_path / "task", script=script, config=config) as h:
        session_id = await h.create_session()
        await h.send(session_id, "启动后台任务")
        snap = await h.wait_idle(session_id, timeout=20)
        assert observed == [True]
        assert len(h.gateway.requests) >= 3
        assert snap["tasks"] and snap["tasks"][0]["status"] == "succeeded"
        notices = [m["content"]["text"] for m in snap["messages"] if m["role"] == "system"]
        assert any("后台任务完成通知" in text and "自检" in text for text in notices)
        assert any("自检" in m["content"]["text"] for m in snap["messages"] if m["role"] == "assistant")


@pytest.mark.asyncio
async def test_a10_compaction_preserves_goal_questions_and_task_ids(tmp_path: Path):
    from conftest import make_config

    config = make_config(tmp_path)
    config.compaction.keep_recent_tokens = 120
    long_text = "压缩测试内容" * 80
    script = [FakeResponse(content=long_text) for _ in range(4)] + [FakeResponse(content="压缩后继续")]
    async with run_harness(tmp_path / "compact", script=script, config=config) as h:
        session_id = await h.create_session()
        for index in range(4):
            await h.send(session_id, f"第 {index + 1} 轮 " + long_text)
            await h.wait_idle(session_id)
        goal = {
            "objective": "交付可验收的 mini-agent",
            "criteria": "附录 D 全部自检通过",
            "tasks": [{"title": "实现运行时", "status": "completed"}],
        }
        response = await h.client.put(f"/api/v1/sessions/{session_id}/goal", json=goal)
        assert response.status_code == 200, response.text
        async with h.app.state.services.db.session() as db:
            task_id = new_id("task")
            db.add(
                Task(
                    id=task_id,
                    session_id=session_id,
                    name="压缩保留任务",
                    status="running",
                    progress=50,
                )
            )
            interaction = PendingInteraction(
                session_id=session_id,
                type="ask_question",
                payload=json.dumps({"questions": [{"id": "q1", "question": "保留我", "type": "text"}]}),
                status="pending",
            )
            db.add(interaction)
            await db.commit()
        response = await h.client.post(f"/api/v1/sessions/{session_id}/compact")
        assert response.status_code == 200, response.text
        snap = await h.snapshot(session_id)
        summary = snap["compaction"]["summary"]
        assert "交付可验收的 mini-agent" in summary
        assert "附录 D 全部自检通过" in summary
        assert "保留我" in summary
        assert task_id in summary
        assert "待回答的问题" in summary
        # Raw history remains.
        async with h.app.state.services.db.session() as db:
            message_count = (
                await db.execute(select(func.count(Message.id)).where(Message.session_id == session_id))
            ).scalar_one()
        assert message_count >= 8
        before = len(h.gateway.requests)
        await h.send(session_id, "压缩后继续")
        await h.wait_idle(session_id)
        assert len(h.gateway.requests) == before + 1
        request = h.gateway.requests[-1]
        system_contents = [str(m.get("content")) for m in request.messages if m.get("role") == "system"]
        assert any("[Context compaction]" in text and task_id in text for text in system_contents)
        assert any("交付可验收的 mini-agent" in text for text in system_contents)


async def _event_count(h, session_id: str, after: int = 0) -> int:
    async with h.app.state.services.db.session() as db:
        return int(
            (
                await db.execute(
                    select(func.count(Event.id)).where(Event.session_id == session_id, Event.id > after)
                )
            ).scalar_one()
        )
