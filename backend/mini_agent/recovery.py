"""Startup recovery for interrupted work.

A restart can never silently re-run dangerous operations. Running work is marked failed,
every ``tool_call`` gets a matching result, background tasks are marked failed and the
session receives an explicit system notice listing what was interrupted.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import Database
from .events import EventStore
from .models import Message, PendingInteraction, Session, Task, ToolCall, Turn, utcnow
from .serialization import interaction_dto, parse_json, tool_call_dto

logger = logging.getLogger(__name__)


async def recover_interrupted_work(db: Database, events: EventStore) -> dict[str, Any]:
    async with db.session() as session:
        affected_ids = set(
            (
                await session.execute(
                    select(Session.id).where(Session.status.in_(["running", "waiting_user", "compacting"]))
                )
            ).scalars()
        )
        affected_ids |= set(
            (await session.execute(select(Turn.session_id).where(Turn.status == "running"))).scalars()
        )
        affected_ids |= set(
            (
                await session.execute(select(Task.session_id).where(Task.status.in_(["queued", "running"])))
            ).scalars()
        )
    reports: list[dict[str, Any]] = []
    for session_id in sorted(affected_ids):
        try:
            report = await _recover_session(db, events, session_id)
            if report:
                reports.append(report)
        except Exception:  # noqa: BLE001
            logger.exception("session recovery failed", extra={"session_id": session_id})
    if reports:
        logger.warning("recovered interrupted sessions", extra={"sessions": len(reports)})
    return {"sessions": reports, "count": len(reports)}


async def _recover_session(db: Database, events: EventStore, session_id: str) -> dict[str, Any] | None:
    async with db.session() as session:
        row = await session.get(Session, session_id)
        if row is None:
            return None
        turns = (
            (
                await session.execute(
                    select(Turn).where(Turn.session_id == session_id, Turn.status == "running")
                )
            )
            .scalars()
            .all()
        )
        tool_calls = (
            (
                await session.execute(
                    select(ToolCall).where(ToolCall.session_id == session_id, ToolCall.ended_at.is_(None))
                )
            )
            .scalars()
            .all()
        )
        tasks = (
            (
                await session.execute(
                    select(Task).where(Task.session_id == session_id, Task.status.in_(["queued", "running"]))
                )
            )
            .scalars()
            .all()
        )
        interactions = (
            (
                await session.execute(
                    select(PendingInteraction).where(
                        PendingInteraction.session_id == session_id,
                        PendingInteraction.status == "pending",
                    )
                )
            )
            .scalars()
            .all()
        )
        streaming = (
            (
                await session.execute(
                    select(Message).where(Message.session_id == session_id, Message.status == "streaming")
                )
            )
            .scalars()
            .all()
        )
        # The model may have emitted several calls while only one had a row.
        turn_ids = [turn.id for turn in turns]
        if turn_ids:
            assistant_messages = (
                (
                    await session.execute(
                        select(Message)
                        .where(Message.turn_id.in_(turn_ids), Message.role == "assistant")
                        .order_by(Message.seq.asc())
                    )
                )
                .scalars()
                .all()
            )
            known = {call.id for call in tool_calls}
            for assistant in assistant_messages:
                raw = parse_json(assistant.raw_json)
                for item in raw.get("tool_calls") or []:
                    call_id = str(item.get("id") or "")
                    if not call_id or call_id in known:
                        continue
                    function = item.get("function") or {}
                    try:
                        arguments = json.loads(function.get("arguments") or "{}")
                    except (json.JSONDecodeError, TypeError):
                        arguments = {}
                    call = ToolCall(
                        id=call_id,
                        session_id=session_id,
                        turn_id=assistant.turn_id or turn_ids[0],
                        message_id=assistant.id,
                        name=str(function.get("name") or "unknown"),
                        input_json=json.dumps(arguments, ensure_ascii=False, separators=(",", ":")),
                        status="running",
                        risk="medium",
                    )
                    session.add(call)
                    tool_calls.append(call)
                    known.add(call_id)
        if not (turns or tool_calls or tasks or interactions or streaming) and row.status not in {
            "running",
            "waiting_user",
            "compacting",
        }:
            return None

        existing_results = await _tool_result_ids(session, session_id)
        task_ids = [task.id for task in tasks]
        for turn in turns:
            turn.status = "failed"
            turn.ended_at = utcnow()
            turn.error_code = "SERVER_RESTARTED"
            turn.error_message = "服务重启，进行中的 Turn 已中断；系统不会自动重放危险操作"
            await events.emit(
                session,
                session_id,
                "turn.failed",
                {
                    "turnId": turn.id,
                    "code": "SERVER_RESTARTED",
                    "message": turn.error_message,
                },
            )
        for message in streaming:
            message.status = "interrupted"
        for call in tool_calls:
            call.status = "cancelled"
            call.error_code = "SERVER_RESTARTED"
            call.ended_at = utcnow()
            payload = {
                "code": 49903,
                "errorCode": "SERVER_RESTARTED",
                "message": "服务重启，工具执行已中止；请确认状态后再决定是否重试",
                "data": {},
                "truncated": False,
                "artifacts": [],
            }
            call.output_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            if call.id not in existing_results:
                await _append_message(
                    session,
                    events,
                    session_id,
                    call.turn_id,
                    role="tool",
                    text=call.output_json,
                    raw={"role": "tool", "tool_call_id": call.id, "content": call.output_json},
                )
            await events.emit(session, session_id, "tool.completed", {"toolCall": tool_call_dto(call)})
        for task in tasks:
            task.status = "failed"
            task.error = "服务重启，后台任务被中断；请检查输出后决定是否重新运行"
            task.notified = True
            task.updated_at = utcnow()
            await events.emit(
                session,
                session_id,
                "task.updated",
                {
                    "task": {
                        "id": task.id,
                        "status": task.status,
                        "progress": task.progress,
                        "error": task.error,
                    }
                },
            )
        for interaction in interactions:
            interaction.status = "cancelled"
            interaction.resolved_at = utcnow()
            interaction.answer_json = json.dumps(
                {"cancelled": True, "reason": "服务重启，交互已过期"}, ensure_ascii=False
            )
            await events.emit(
                session,
                session_id,
                "interaction.resolved",
                {"interaction": interaction_dto(interaction)},
            )

        interrupted_parts = []
        if turns:
            interrupted_parts.append(f"{len(turns)} 个 Turn")
        if tool_calls:
            interrupted_parts.append(f"{len(tool_calls)} 个工具调用")
        if task_ids:
            interrupted_parts.append(f"后台任务：{', '.join(task_ids)}")
        if interactions:
            interrupted_parts.append(f"{len(interactions)} 个待回答交互")
        notice = (
            "[服务重启恢复] 上次运行被中断。"
            + ("已中断：" + "；".join(interrupted_parts) + "。" if interrupted_parts else "")
            + " 系统没有自动重放任何危险操作。请检查任务和工具结果后决定是否继续。"
        )
        row.status = "error"
        row.last_error = "服务重启，未完成的 Turn 已标记为失败并通知用户"
        row.updated_at = utcnow()
        await _append_message(
            session,
            events,
            session_id,
            None,
            role="system",
            text=notice,
            raw={"role": "system", "content": notice},
        )
        await events.emit(
            session,
            session_id,
            "session.recovered",
            {
                "turns": len(turns),
                "toolCalls": len(tool_calls),
                "tasks": task_ids,
                "interactions": len(interactions),
                "message": notice,
            },
        )
        await events.emit(session, session_id, "session.status", {"status": "error"})
        await session.commit()
    return {
        "sessionId": session_id,
        "turns": len(turns),
        "toolCalls": len(tool_calls),
        "tasks": task_ids,
        "interactions": len(interactions),
    }


async def _tool_result_ids(session: AsyncSession, session_id: str) -> set[str]:
    rows = (
        (
            await session.execute(
                select(Message).where(Message.session_id == session_id, Message.role == "tool")
            )
        )
        .scalars()
        .all()
    )
    ids: set[str] = set()
    for row in rows:
        raw = parse_json(row.raw_json)
        if raw.get("tool_call_id"):
            ids.add(str(raw["tool_call_id"]))
    return ids


async def _append_message(
    session: AsyncSession,
    events: EventStore,
    session_id: str,
    turn_id: str | None,
    *,
    role: str,
    text: str,
    raw: dict[str, Any],
) -> Message:
    seq = (
        int(
            (
                await session.execute(
                    select(func.coalesce(func.max(Message.seq), 0)).where(Message.session_id == session_id)
                )
            ).scalar_one()
        )
        + 1
    )
    row = Message(
        session_id=session_id,
        turn_id=turn_id,
        seq=seq,
        role=role,
        content_json=json.dumps({"text": text}, ensure_ascii=False, separators=(",", ":")),
        raw_json=json.dumps(raw, ensure_ascii=False, separators=(",", ":")),
        status="complete",
    )
    session.add(row)
    await session.flush()
    await events.emit(
        session,
        session_id,
        "message.created",
        {
            "message": {
                "id": row.id,
                "sessionId": row.session_id,
                "turnId": row.turn_id,
                "seq": row.seq,
                "role": row.role,
                "content": {"text": text, "reasoning": "", "toolCalls": []},
                "status": row.status,
                "clientMessageId": None,
                "createdAt": row.created_at,
            }
        },
    )
    return row
