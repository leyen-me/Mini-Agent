"""Read models (DTOs) shared by REST snapshots and SSE consumers."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import AppConfig
from .events import EventStore
from .models import (
    Agent,
    Compaction,
    Goal,
    GoalTask,
    Message,
    ModelRequest,
    PendingInteraction,
    QueuedMessage,
    Session,
    Task,
    ToolCall,
    Turn,
    UsageRecord,
    Workspace,
)
from .tokens import estimate_messages_tokens, estimate_tokens


def parse_json(value: str | None, default: Any = None) -> Any:
    if not value:
        return default if default is not None else {}
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else {}


def message_dto(message: Message) -> dict[str, Any]:
    content = parse_json(message.content_json)
    return {
        "id": message.id,
        "sessionId": message.session_id,
        "turnId": message.turn_id,
        "seq": message.seq,
        "role": message.role,
        "content": {
            "text": content.get("text", ""),
            "reasoning": content.get("reasoning", ""),
            "toolCalls": content.get("tool_calls", []),
        },
        "status": message.status,
        "clientMessageId": message.client_message_id,
        "createdAt": message.created_at,
    }


def tool_call_dto(call: ToolCall) -> dict[str, Any]:
    return {
        "id": call.id,
        "sessionId": call.session_id,
        "turnId": call.turn_id,
        "messageId": call.message_id,
        "name": call.name,
        "input": parse_json(call.input_json),
        "output": parse_json(call.output_json) if call.output_json else None,
        "status": call.status,
        "risk": call.risk,
        "errorCode": call.error_code,
        "startedAt": call.started_at,
        "endedAt": call.ended_at,
    }


def interaction_dto(interaction: PendingInteraction) -> dict[str, Any]:
    return {
        "id": interaction.id,
        "sessionId": interaction.session_id,
        "turnId": interaction.turn_id,
        "type": interaction.type,
        "payload": parse_json(interaction.payload),
        "status": interaction.status,
        "answer": parse_json(interaction.answer_json) if interaction.answer_json else None,
        "createdAt": interaction.created_at,
    }


def task_dto(task: Task) -> dict[str, Any]:
    return {
        "id": task.id,
        "sessionId": task.session_id,
        "name": task.name,
        "status": task.status,
        "progress": task.progress,
        "outputRef": task.output_ref,
        "summary": task.summary,
        "error": task.error,
        "createdAt": task.created_at,
        "updatedAt": task.updated_at,
    }


def session_dto(session: Session) -> dict[str, Any]:
    return {
        "id": session.id,
        "workspaceId": session.workspace_id,
        "agentId": session.agent_id,
        "title": session.title,
        "model": session.model,
        "reasoningLevel": session.reasoning_level,
        "mode": session.mode,
        "status": session.status,
        "parentSessionId": session.parent_session_id,
        "forkFromMessageId": session.fork_from_message_id,
        "pinned": session.pinned,
        "archived": session.archived,
        "lastError": session.last_error,
        "createdAt": session.created_at,
        "updatedAt": session.updated_at,
    }


async def build_snapshot(
    db: AsyncSession,
    session_id: str,
    config: AppConfig,
    events: EventStore,
    *,
    include_system_prompt: bool = True,
) -> dict[str, Any] | None:
    session = await db.get(Session, session_id)
    if session is None:
        return None
    workspace = await db.get(Workspace, session.workspace_id)
    agent = await db.get(Agent, session.agent_id)

    messages = (
        (
            await db.execute(
                select(Message).where(Message.session_id == session_id).order_by(Message.seq.asc())
            )
        )
        .scalars()
        .all()
    )
    turns = (
        (await db.execute(select(Turn).where(Turn.session_id == session_id).order_by(Turn.seq.asc())))
        .scalars()
        .all()
    )
    calls = (
        (
            await db.execute(
                select(ToolCall).where(ToolCall.session_id == session_id).order_by(ToolCall.started_at.asc())
            )
        )
        .scalars()
        .all()
    )
    queued = (
        (
            await db.execute(
                select(QueuedMessage)
                .where(QueuedMessage.session_id == session_id, QueuedMessage.status == "pending")
                .order_by(QueuedMessage.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    interactions = (
        (
            await db.execute(
                select(PendingInteraction)
                .where(PendingInteraction.session_id == session_id, PendingInteraction.status == "pending")
                .order_by(PendingInteraction.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    tasks = (
        (
            await db.execute(
                select(Task).where(Task.session_id == session_id).order_by(Task.created_at.desc()).limit(50)
            )
        )
        .scalars()
        .all()
    )
    usage_rows = (
        (await db.execute(select(UsageRecord).where(UsageRecord.session_id == session_id))).scalars().all()
    )
    goal = (await db.execute(select(Goal).where(Goal.session_id == session_id))).scalars().first()
    goal_tasks: list[GoalTask] = []
    if goal:
        goal_tasks = (
            (
                await db.execute(
                    select(GoalTask).where(GoalTask.goal_id == goal.id).order_by(GoalTask.position.asc())
                )
            )
            .scalars()
            .all()
        )
    compaction = (
        (
            await db.execute(
                select(Compaction)
                .where(Compaction.session_id == session_id)
                .order_by(Compaction.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )

    model = None
    try:
        _, model = config.provider_for(session.model)
    except Exception:  # noqa: BLE001 - snapshot must remain readable if config changed
        model = None

    total_input = sum(row.input_tokens for row in usage_rows)
    total_output = sum(row.output_tokens for row in usage_rows)
    total_hit = sum(row.cache_hit_tokens for row in usage_rows)
    total_miss = sum(row.cache_miss_tokens for row in usage_rows)
    total_cost = round(sum(row.cost for row in usage_rows), 8)

    context_messages = [{"role": "system", "content": session.system_prompt}]
    if compaction:
        context_messages.append({"role": "system", "content": compaction.summary})
    for message in messages:
        if compaction and message.seq <= compaction.covered_until_seq:
            continue
        raw = parse_json(message.raw_json)
        if raw:
            context_messages.append(raw)
    context_used = estimate_messages_tokens(context_messages)
    context_window = model.context_window if model else 128_000

    last_event_id = await events.latest_id(db, session_id)

    payload: dict[str, Any] = {
        "session": session_dto(session),
        "workspace": (
            {
                "id": workspace.id,
                "name": workspace.name,
                "rootPath": workspace.root_path,
                "createdAt": workspace.created_at,
            }
            if workspace
            else None
        ),
        "agent": (
            {
                "id": agent.id,
                "name": agent.name,
                "description": agent.description,
                "readOnly": agent.read_only,
            }
            if agent
            else None
        ),
        "messages": [message_dto(message) for message in messages],
        "turns": [
            {
                "id": turn.id,
                "sessionId": turn.session_id,
                "seq": turn.seq,
                "status": turn.status,
                "startedAt": turn.started_at,
                "endedAt": turn.ended_at,
                "errorCode": turn.error_code,
                "errorMessage": turn.error_message,
                "usage": parse_json(turn.usage_json),
            }
            for turn in turns
        ],
        "toolCalls": [tool_call_dto(call) for call in calls],
        "queue": [
            {
                "id": item.id,
                "mode": item.mode,
                "content": item.content,
                "status": item.status,
                "createdAt": item.created_at,
            }
            for item in queued
        ],
        "interactions": [interaction_dto(interaction) for interaction in interactions],
        "tasks": [task_dto(task) for task in tasks],
        "usage": {
            "inputTokens": total_input,
            "outputTokens": total_output,
            "cacheHitTokens": total_hit,
            "cacheMissTokens": total_miss,
            "cost": total_cost,
            "contextUsed": context_used,
            "contextWindow": context_window,
            "contextPercent": round(min(100.0, context_used / max(1, context_window) * 100), 2),
            "model": session.model,
        },
        "goal": (
            {
                "id": goal.id,
                "objective": goal.objective,
                "criteria": goal.criteria,
                "status": goal.status,
                "tasks": [
                    {"id": task.id, "title": task.title, "status": task.status, "position": task.position}
                    for task in goal_tasks
                ],
                "updatedAt": goal.updated_at,
            }
            if goal
            else None
        ),
        "compaction": (
            {
                "id": compaction.id,
                "summary": compaction.summary,
                "coveredUntilSeq": compaction.covered_until_seq,
                "tokensBefore": compaction.tokens_before,
                "tokensAfter": compaction.tokens_after,
                "createdAt": compaction.created_at,
            }
            if compaction
            else None
        ),
        "lastEventId": last_event_id,
        "serverTime": messages[-1].created_at if messages else (session.updated_at if session else None),
    }
    if include_system_prompt:
        payload["systemPrompt"] = session.system_prompt
        payload["envInfo"] = session.env_info
    return payload


async def build_debug_requests(db: AsyncSession, session_id: str, limit: int = 30) -> list[dict[str, Any]]:
    rows = (
        (
            await db.execute(
                select(ModelRequest)
                .where(ModelRequest.session_id == session_id)
                .order_by(ModelRequest.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": row.id,
            "turnId": row.turn_id,
            "step": row.step,
            "model": row.model,
            "prefixHash": row.prefix_hash,
            "prefixTokens": row.prefix_tokens,
            "cacheHitTokens": row.cache_hit_tokens,
            "cacheMissTokens": row.cache_miss_tokens,
            "retries": row.retries,
            "durationMs": row.duration_ms,
            "createdAt": row.created_at,
        }
        for row in rows
    ]


def context_estimate_for_messages(messages: list[dict], system_prompt: str) -> int:
    return estimate_tokens(system_prompt) + estimate_messages_tokens(messages)
