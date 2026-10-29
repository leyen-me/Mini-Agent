"""Deterministic context compaction.

The summary deliberately carries the information that must survive a cache-breaking
compaction: goal + criteria, user constraints, completed work, key decisions, open
questions, background task ids and related files. Raw history stays in the database.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import AppConfig
from .events import EventStore
from .models import (
    Compaction,
    Goal,
    GoalTask,
    Message,
    PendingInteraction,
    QueuedMessage,
    Session,
    Task,
    ToolCall,
    Turn,
)
from .serialization import parse_json
from .tokens import estimate_messages_tokens, estimate_tokens


def _bullets(items: list[str], empty: str = "无") -> str:
    if not items:
        return empty
    return "\n".join(f"- {item}" for item in items)


def _truncate(text: str, limit: int = 1_200) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


async def plan_compaction(db: AsyncSession, session_id: str, keep_recent_tokens: int) -> tuple[int, int]:
    """Return ``(covered_until_message_seq, kept_tokens)``.

    The cut only happens at a completed Turn boundary so an assistant ``tool_calls``
    message is never separated from its ``tool`` result messages.
    """

    turns = (
        (await db.execute(select(Turn).where(Turn.session_id == session_id).order_by(Turn.seq.asc())))
        .scalars()
        .all()
    )
    entries: list[dict[str, int]] = []
    for turn in turns:
        rows = (
            (
                await db.execute(
                    select(Message)
                    .where(Message.session_id == session_id, Message.turn_id == turn.id)
                    .order_by(Message.seq.asc())
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            continue
        tokens = estimate_messages_tokens([parse_json(message.raw_json) for message in rows])
        entries.append(
            {
                "turn_seq": turn.seq,
                "min_seq": min(message.seq for message in rows),
                "max_seq": max(message.seq for message in rows),
                "tokens": tokens,
            }
        )
    kept_start = len(entries)
    kept_tokens = 0
    for index in range(len(entries) - 1, -1, -1):
        tokens = entries[index]["tokens"]
        if kept_tokens + tokens > keep_recent_tokens and kept_tokens > 0:
            break
        kept_tokens += tokens
        kept_start = index
    covered_until = entries[kept_start - 1]["max_seq"] if kept_start > 0 else 0
    return covered_until, kept_tokens


async def build_compaction_summary(db: AsyncSession, session_id: str, covered_until_seq: int) -> str:
    session = await db.get(Session, session_id)
    if session is None:
        raise ValueError("session not found")
    goal = (await db.execute(select(Goal).where(Goal.session_id == session_id))).scalars().first()
    goal_tasks: list[GoalTask] = []
    if goal:
        goal_tasks = (
            (
                await db.execute(
                    select(GoalTask).where(GoalTask.goal_id == goal.id).order_by(GoalTask.position)
                )
            )
            .scalars()
            .all()
        )
    messages = (
        (
            await db.execute(
                select(Message)
                .where(Message.session_id == session_id, Message.seq <= covered_until_seq)
                .order_by(Message.seq.asc())
            )
        )
        .scalars()
        .all()
    )
    user_messages = [message for message in messages if message.role == "user"]
    assistant_messages = [message for message in messages if message.role == "assistant"]
    calls = (
        (
            await db.execute(
                select(ToolCall).where(ToolCall.session_id == session_id).order_by(ToolCall.started_at.asc())
            )
        )
        .scalars()
        .all()
    )
    interactions = (
        (
            await db.execute(
                select(PendingInteraction).where(
                    PendingInteraction.session_id == session_id,
                    PendingInteraction.status == "pending",
                )
            )
        )
        .scalars()
        .all()
    )
    queued = (
        (
            await db.execute(
                select(QueuedMessage).where(
                    QueuedMessage.session_id == session_id,
                    QueuedMessage.status == "pending",
                )
            )
        )
        .scalars()
        .all()
    )
    tasks = (
        (await db.execute(select(Task).where(Task.session_id == session_id).order_by(Task.created_at.asc())))
        .scalars()
        .all()
    )

    completed_tasks = [task for task in goal_tasks if task.status == "completed"]
    open_tasks = [task for task in goal_tasks if task.status in {"pending", "in_progress", "blocked"}]
    files: list[str] = []
    for call in calls:
        args = parse_json(call.input_json)
        path = args.get("path")
        if isinstance(path, str) and path not in files:
            files.append(path)

    sections = [
        "# 上下文压缩摘要（系统自动生成）",
        "",
        "## 当前目标",
        (
            f"目标：{goal.objective}\n完成标准：{goal.criteria}\n状态：{goal.status}"
            if goal
            else "未开启目标模式。"
        ),
        "",
        "## 用户要求和约束",
        _bullets(
            [
                _truncate(parse_json(message.content_json).get("text", ""), 600)
                for message in user_messages[-8:]
            ]
        ),
        "",
        "## 已完成的工作",
        _bullets(
            [f"任务：{task.title}" for task in completed_tasks]
            + [
                f"助手输出：{_truncate(parse_json(message.content_json).get('text', ''), 300)}"
                for message in assistant_messages[-4:]
                if parse_json(message.content_json).get("text")
            ]
        ),
        "",
        "## 关键决定",
        _bullets(
            [f"{call.name} 调用结果：{call.status}" for call in calls[-8:] if call.status == "succeeded"]
        ),
        "",
        "## 未完成事项",
        _bullets(
            [f"待办：{task.title}（{task.status}）" for task in open_tasks]
            + [f"排队消息：{_truncate(item.content, 200)}" for item in queued]
        ),
        "",
        "## 阻塞问题",
        _bullets(
            [
                f"等待用户回答：{_truncate(json.dumps(parse_json(item.payload), ensure_ascii=False), 800)}"
                for item in interactions
            ]
            + [
                f"失败工具调用：{call.name}（{call.error_code or call.status}）"
                for call in calls
                if call.status in {"failed", "timeout", "denied"}
            ]
        ),
        "",
        "## 相关文件",
        _bullets(files),
        "",
        "## 后台任务（taskId 必须保留）",
        _bullets(
            [
                f"taskId={task.id}；名称={task.name}；状态={task.status}；输出={task.output_ref or '无'}"
                for task in tasks
            ]
        ),
        "",
        "## 待回答的问题",
        _bullets(
            [
                f"interactionId={item.id}；type={item.type}；payload={_truncate(json.dumps(parse_json(item.payload), ensure_ascii=False), 1_000)}"
                for item in interactions
            ]
        ),
        "",
        f"> 压缩覆盖到消息 seq={covered_until_seq}。原始消息完整保存在数据库中，未被删除。",
    ]
    return "\n".join(sections)


async def compact_session(
    db: AsyncSession,
    events: EventStore,
    config: AppConfig,
    session_id: str,
    *,
    keep_recent_tokens: int | None = None,
    summary_override: str | None = None,
) -> Compaction:
    session = await db.get(Session, session_id)
    if session is None:
        raise ValueError("session not found")
    all_messages = (
        (
            await db.execute(
                select(Message).where(Message.session_id == session_id).order_by(Message.seq.asc())
            )
        )
        .scalars()
        .all()
    )
    tokens_before = estimate_tokens(session.system_prompt) + estimate_messages_tokens(
        [parse_json(message.raw_json) for message in all_messages]
    )
    keep = keep_recent_tokens or config.compaction.keep_recent_tokens
    covered_until_seq, kept_tokens = await plan_compaction(db, session_id, keep)
    summary = summary_override or await build_compaction_summary(db, session_id, covered_until_seq)
    tokens_after = estimate_tokens(session.system_prompt) + estimate_tokens(summary) + kept_tokens
    compaction = Compaction(
        session_id=session_id,
        summary=summary,
        covered_until_seq=covered_until_seq,
        tokens_before=tokens_before,
        tokens_after=tokens_after,
    )
    db.add(compaction)
    await db.flush()
    await events.emit(
        db,
        session_id,
        "compaction.created",
        {
            "compaction": {
                "id": compaction.id,
                "summary": compaction.summary,
                "coveredUntilSeq": compaction.covered_until_seq,
                "tokensBefore": compaction.tokens_before,
                "tokensAfter": compaction.tokens_after,
            }
        },
    )
    await db.commit()
    return compaction


def build_model_messages(
    *,
    system_prompt: str,
    compaction: Compaction | None,
    raw_messages: list[tuple[int, str | None, dict[str, Any]]],
    current_turn_id: str | None = None,
) -> list[dict[str, Any]]:
    """Rebuild the exact provider payload from persisted raw messages.

    History is append-only. Only an explicit compaction may replace an old prefix, and
    then it inserts one stable summary message. Reasoning content is stripped from
    previous turns (DeepSeek thinking-mode contract) but kept inside the current turn's
    tool loop.
    """

    result: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    if compaction:
        result.append(
            {
                "role": "system",
                "content": f"[Context compaction]\n{compaction.summary}",
            }
        )
    for seq, turn_id, raw in raw_messages:
        if compaction and seq <= compaction.covered_until_seq:
            continue
        if raw.get("role") == "assistant" and "reasoning_content" in raw:
            if current_turn_id is None or turn_id != current_turn_id:
                raw = {key: value for key, value in raw.items() if key != "reasoning_content"}
        result.append(raw)
    return result


def compaction_dto(compaction: Compaction | None) -> dict[str, Any] | None:
    if compaction is None:
        return None
    return {
        "id": compaction.id,
        "summary": compaction.summary,
        "coveredUntilSeq": compaction.covered_until_seq,
        "tokensBefore": compaction.tokens_before,
        "tokensAfter": compaction.tokens_after,
        "createdAt": compaction.created_at,
    }
