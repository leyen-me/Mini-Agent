"""Session export, redacted sharing and forking."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import AppConfig
from .events import EventStore
from .models import Message, Session, Turn, utcnow
from .security import redact_object, redact_text
from .serialization import build_snapshot, parse_json


async def export_session(
    db: AsyncSession,
    events: EventStore,
    config: AppConfig,
    session_id: str,
    *,
    fmt: str = "json",
    include_reasoning: bool = True,
    include_tools: bool = True,
) -> tuple[str, str, str]:
    """Return ``(content, media_type, filename)`` with all secrets redacted."""

    snapshot = await build_snapshot(db, session_id, config, events)
    if snapshot is None:
        raise LookupError("会话不存在")
    session = await db.get(Session, session_id)
    assert session is not None
    if fmt == "json":
        raw_rows = (
            (
                await db.execute(
                    select(Message).where(Message.session_id == session_id).order_by(Message.seq.asc())
                )
            )
            .scalars()
            .all()
        )
        raw_messages = [
            {
                "id": row.id,
                "turnId": row.turn_id,
                "seq": row.seq,
                "role": row.role,
                "content": parse_json(row.content_json),
                "raw": parse_json(row.raw_json),
                "status": row.status,
                "createdAt": row.created_at,
            }
            for row in raw_rows
        ]
        payload = {
            "formatVersion": 2,
            "exportedAt": utcnow(),
            "session": snapshot["session"],
            "workspace": snapshot["workspace"],
            "agent": snapshot["agent"],
            "systemPrompt": snapshot.get("systemPrompt"),
            "messages": snapshot["messages"],
            "rawMessages": raw_messages,
            "turns": snapshot["turns"],
            "toolCalls": snapshot["toolCalls"] if include_tools else [],
            "queue": snapshot["queue"],
            "usage": snapshot["usage"],
            "goal": snapshot["goal"],
            "compaction": snapshot["compaction"],
        }
        if not include_reasoning:
            for message in payload["messages"]:
                message["content"]["reasoning"] = ""
        cleaned = redact_object(payload, config.secret_values())
        return (
            json.dumps(cleaned, ensure_ascii=False, indent=2),
            "application/json",
            f"{_safe_name(session.title)}.json",
        )
    lines: list[str] = [f"# {session.title}", ""]
    lines.append(f"- 会话 ID：`{session.id}`")
    lines.append(f"- 模型：`{session.model}`")
    lines.append(f"- 创建时间：{session.created_at}")
    lines.append("")
    if snapshot.get("goal"):
        goal = snapshot["goal"]
        lines.append("## 目标")
        lines.append(f"**{goal['objective']}**")
        if goal.get("criteria"):
            lines.append(f"完成标准：{goal['criteria']}")
        for task in goal.get("tasks", []):
            lines.append(
                f"- [{'x' if task['status'] == 'completed' else ' '}] {task['title']} ({task['status']})"
            )
        lines.append("")
    if snapshot.get("compaction"):
        lines.append("## 上下文压缩摘要")
        lines.append(snapshot["compaction"]["summary"])
        lines.append("")
    tool_by_message = {call["messageId"]: call for call in snapshot["toolCalls"]}
    for message in snapshot["messages"]:
        role = message["role"]
        text = message["content"].get("text", "")
        if role == "user":
            lines.extend(["## 用户", text, ""])
        elif role == "assistant":
            if include_reasoning and message["content"].get("reasoning"):
                lines.extend(
                    [
                        "<details><summary>思考过程</summary>",
                        "",
                        message["content"]["reasoning"],
                        "",
                        "</details>",
                        "",
                    ]
                )
            if text:
                lines.extend(["## 助手", text, ""])
            if include_tools:
                for call in message["content"].get("toolCalls", []):
                    tool_id = call.get("id")
                    row = next((item for item in snapshot["toolCalls"] if item["id"] == tool_id), None)
                    if row:
                        lines.extend(
                            [
                                f"<details><summary>工具 {row['name']} · {row['status']}</summary>",
                                "",
                                "```json",
                                json.dumps(row["input"], ensure_ascii=False, indent=2),
                                "```",
                                "",
                                "```json",
                                json.dumps(row.get("output"), ensure_ascii=False, indent=2),
                                "```",
                                "",
                                "</details>",
                                "",
                            ]
                        )
            _ = tool_by_message
        elif role == "system":
            lines.extend([f"> {text}", ""])
        elif role == "tool" and include_tools:
            lines.extend(["```json", text, "```", ""])
    lines.extend(["---", f"用量：{json.dumps(snapshot['usage'], ensure_ascii=False)}", ""])
    content = redact_text("\n".join(lines), config.secret_values())
    return content, "text/markdown", f"{_safe_name(session.title)}.md"


async def build_share_payload(
    db: AsyncSession,
    events: EventStore,
    config: AppConfig,
    session_id: str,
    *,
    hide_tool_io: bool = True,
) -> dict[str, Any]:
    snapshot = await build_snapshot(db, session_id, config, events, include_system_prompt=False)
    if snapshot is None:
        raise LookupError("会话不存在")
    snapshot.pop("systemPrompt", None)
    snapshot.pop("envInfo", None)
    snapshot.pop("lastEventId", None)
    if hide_tool_io:
        for call in snapshot["toolCalls"]:
            call["input"] = {"hidden": True}
            call["output"] = {"hidden": True}
    cleaned = redact_object(snapshot, config.secret_values())
    return cleaned


async def fork_session(
    db: AsyncSession,
    config: AppConfig,
    events: EventStore,
    source: Session,
    *,
    message_id: str | None,
    title_suffix: str = " (fork)",
    include_target: bool = True,
) -> Session:
    target = (
        (await db.execute(select(Message).where(Message.id == message_id, Message.session_id == source.id)))
        .scalars()
        .first()
    )
    cutoff = 10**9
    if target is not None:
        cutoff = target.seq if include_target else target.seq - 1
    messages = (
        (
            await db.execute(
                select(Message)
                .where(Message.session_id == source.id, Message.seq <= cutoff)
                .order_by(Message.seq.asc())
            )
        )
        .scalars()
        .all()
    )
    turns = (
        (await db.execute(select(Turn).where(Turn.session_id == source.id).order_by(Turn.seq.asc())))
        .scalars()
        .all()
    )
    from .models import new_id

    forked = Session(
        id=new_id("sess"),
        workspace_id=source.workspace_id,
        agent_id=source.agent_id,
        title=(source.title + title_suffix)[:200],
        model=source.model,
        reasoning_level=source.reasoning_level,
        mode=source.mode,
        status="idle",
        system_prompt=source.system_prompt,
        env_info=source.env_info,
        parent_session_id=source.id,
        fork_from_message_id=message_id,
    )
    db.add(forked)
    await db.flush()
    turn_map: dict[str, str] = {}
    for turn in turns:
        new_turn = Turn(
            session_id=forked.id,
            seq=turn.seq,
            status="completed"
            if turn.status in {"completed", "failed", "cancelled", "paused"}
            else "completed",
            started_at=turn.started_at,
            ended_at=turn.ended_at or utcnow(),
            usage_json=turn.usage_json,
        )
        db.add(new_turn)
        await db.flush()
        turn_map[turn.id] = new_turn.id
    for message in messages:
        db.add(
            Message(
                session_id=forked.id,
                turn_id=turn_map.get(message.turn_id or ""),
                seq=message.seq,
                role=message.role,
                content_json=message.content_json,
                raw_json=message.raw_json,
                client_message_id=None,
                status="complete" if message.status in {"complete", "streaming"} else "interrupted",
                created_at=message.created_at,
            )
        )
    await db.flush()
    await events.emit(
        db,
        forked.id,
        "session.created",
        {"session": {"id": forked.id, "title": forked.title, "parentSessionId": source.id}},
    )
    await db.commit()
    return forked


def _safe_name(title: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "-_ " else "_" for char in title).strip()
    return (cleaned or "session")[:80].replace(" ", "-")
