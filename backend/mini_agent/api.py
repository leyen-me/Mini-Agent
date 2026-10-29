"""REST + SSE API."""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import yaml
from fastapi import APIRouter, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import func, or_, select

from .compaction import compact_session
from .config import (
    AppConfig,
    DefaultsConfig,
    GeneralConfig,
    ResolutionError,
    deep_merge,
    sanitize_config,
)
from .context import Services
from .exports import build_share_payload, export_session, fork_session
from .logging_utils import request_id_var, session_id_var
from .models import (
    Agent,
    Goal,
    GoalTask,
    Message,
    PermissionGrant,
    QueuedMessage,
    Session,
    Share,
    Task,
    Workspace,
    new_id,
    utcnow,
)
from .prompts import build_system_prompt
from .security import PathSecurityError, resolve_workspace_path
from .serialization import (
    build_debug_requests,
    build_snapshot,
    session_dto,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1")

APP_VERSION = "0.1.0"


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.data = data


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


def ok(data: Any = None, message: str = "ok") -> dict[str, Any]:
    return {
        "code": "OK",
        "message": message,
        "data": data,
        "timestamp": utcnow(),
    }


def services(request: Request) -> Services:
    return request.app.state.services


def _request_id() -> str:
    return request_id_var.get() or str(uuid.uuid4())


class SendMessageRequest(CamelModel):
    content: str = Field(min_length=1, max_length=200_000)
    client_message_id: str | None = Field(default=None, max_length=120)
    mode: Literal["auto", "steer", "queue"] = "auto"


class CreateSessionRequest(CamelModel):
    workspace_id: str | None = None
    agent_id: str | None = None
    model: str | None = None
    title: str | None = Field(default=None, max_length=200)
    mode: Literal["build", "plan"] | None = None
    reasoning_level: str | None = None


class UpdateSessionRequest(CamelModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    pinned: bool | None = None
    archived: bool | None = None
    model: str | None = None
    reasoning_level: str | None = None
    mode: Literal["build", "plan"] | None = None
    agent_id: str | None = None


class AnswerRequest(CamelModel):
    answer: dict[str, Any]


class QueueUpdateRequest(CamelModel):
    content: str | None = Field(default=None, min_length=1, max_length=200_000)
    mode: Literal["steer", "queue"] | None = None


class ShareCreateRequest(CamelModel):
    expires_in_hours: int | None = Field(default=72, ge=1, le=24 * 365)
    hide_tool_io: bool = True


class GoalTaskInput(CamelModel):
    title: str = Field(min_length=1, max_length=500)
    status: Literal["pending", "in_progress", "completed", "blocked"] = "pending"


class GoalRequest(CamelModel):
    objective: str = Field(min_length=1, max_length=10_000)
    criteria: str = Field(min_length=1, max_length=10_000)
    tasks: list[GoalTaskInput] = Field(default_factory=list)


class GoalTaskUpdate(CamelModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    status: Literal["pending", "in_progress", "completed", "blocked"] | None = None


class WorkspaceCreateRequest(CamelModel):
    name: str = Field(min_length=1, max_length=200)
    root_path: str | None = None


class ConfigPatchRequest(CamelModel):
    general: dict[str, Any] | None = None
    defaults: dict[str, Any] | None = None


class ForkRequest(CamelModel):
    message_id: str | None = None


class EditMessageRequest(CamelModel):
    content: str = Field(min_length=1, max_length=200_000)
    client_message_id: str | None = None


# --------------------------------------------------------------------- helpers


async def _require_session(db, session_id: str) -> Session:
    row = await db.get(Session, session_id)
    if row is None:
        raise ApiError(404, "NOT_FOUND", "会话不存在")
    return row


def _require_model(config: AppConfig, model_ref: str | None) -> str:
    ref = model_ref or config.defaults.model
    try:
        provider, model = config.provider_for(ref)
    except ResolutionError as exc:
        raise ApiError(409, "NO_MODEL", str(exc)) from exc
    if provider.protocol == "openai-chat" and not provider.api_key:
        raise ApiError(409, "NO_MODEL", f"模型 {ref} 未配置 API Key，请在设置中配置服务商")
    return ref


def _model_config(config: AppConfig, ref: str):
    try:
        _, model = config.provider_for(ref)
        return model
    except ResolutionError as exc:
        raise ApiError(422, "INVALID_MODEL", str(exc)) from exc


async def _session_list(db, config: AppConfig, *, include_archived: bool = False) -> list[dict[str, Any]]:
    rows = (
        (
            await db.execute(
                select(Session).order_by(Session.pinned.desc(), Session.updated_at.desc()).limit(200)
            )
        )
        .scalars()
        .all()
    )
    if not include_archived:
        rows = [row for row in rows if not row.archived]
    return [session_dto(row) | {"messageCount": 0} for row in rows]


def _goal_payload(goal: Goal, tasks: list[GoalTask]) -> dict[str, Any]:
    return {
        "id": goal.id,
        "objective": goal.objective,
        "criteria": goal.criteria,
        "status": goal.status,
        "updatedAt": goal.updated_at,
        "tasks": [
            {
                "id": task.id,
                "title": task.title,
                "status": task.status,
                "position": task.position,
            }
            for task in tasks
        ],
    }


async def _all_grants(db) -> list[PermissionGrant]:
    return list(
        (await db.execute(select(PermissionGrant).order_by(PermissionGrant.created_at.desc())))
        .scalars()
        .all()
    )


def _persist_user_settings(config: AppConfig, patch: ConfigPatchRequest) -> None:
    """Persist user-editable settings into the workspace-local layer."""

    path = config.data_dir / "workspace.yaml"
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                existing = loaded
        except (OSError, yaml.YAMLError) as exc:
            raise ApiError(422, "CONFIG_INVALID", f"现有配置文件无法解析：{exc}") from exc
    update: dict[str, Any] = {}
    if patch.general is not None:
        allowed = {"language", "theme", "notifications"}
        unknown = set(patch.general) - allowed
        if unknown:
            raise ApiError(422, "INVALID_INPUT", f"不支持的通用配置：{', '.join(sorted(unknown))}")
        update["general"] = patch.general
    if patch.defaults is not None:
        allowed = {"model", "reasoning_level", "agent", "mode"}
        unknown = set(patch.defaults) - allowed
        if unknown:
            raise ApiError(422, "INVALID_INPUT", f"不支持的默认配置：{', '.join(sorted(unknown))}")
        update["defaults"] = patch.defaults
    merged = deep_merge(existing, update)
    new_general = deep_merge(config.general.model_dump(), update.get("general", {}))
    new_defaults = deep_merge(config.defaults.model_dump(), update.get("defaults", {}))
    try:
        candidate_general = GeneralConfig(**new_general)
        candidate_defaults = DefaultsConfig(**new_defaults)
    except ValueError as exc:
        raise ApiError(422, "INVALID_INPUT", str(exc)) from exc
    if candidate_defaults.model:
        try:
            config.provider_for(candidate_defaults.model)
        except ResolutionError as exc:
            raise ApiError(422, "INVALID_MODEL", str(exc)) from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".yaml.tmp")
    try:
        temp.write_text(yaml.safe_dump(merged, allow_unicode=True, sort_keys=False), encoding="utf-8")
        os.replace(temp, path)
    except OSError as exc:
        temp.unlink(missing_ok=True)
        raise ApiError(500, "CONFIG_WRITE_FAILED", f"配置写入失败：{exc}") from exc
    config.general = candidate_general
    config.defaults = candidate_defaults


# ---------------------------------------------------------------------- routes


@router.get("/config")
async def get_config(request: Request):
    app_services = services(request)
    return ok(sanitize_config(app_services.config))


@router.patch("/config")
async def patch_config(request: Request, body: ConfigPatchRequest):
    app_services = services(request)
    _persist_user_settings(app_services.config, body)
    if body.defaults and body.defaults.get("model"):
        _require_model(app_services.config, str(body.defaults["model"]))
    return ok(sanitize_config(app_services.config))


@router.get("/bootstrap")
async def bootstrap(request: Request):
    app_services = services(request)
    config = app_services.config
    async with app_services.db.session() as db:
        workspaces = (
            (await db.execute(select(Workspace).order_by(Workspace.created_at.asc()))).scalars().all()
        )
        agents = (await db.execute(select(Agent).order_by(Agent.id.asc()))).scalars().all()
        sessions = await _session_list(db, config)
    return ok(
        {
            "config": sanitize_config(config),
            "workspaces": [
                {
                    "id": workspace.id,
                    "name": workspace.name,
                    "rootPath": workspace.root_path,
                    "createdAt": workspace.created_at,
                }
                for workspace in workspaces
            ],
            "agents": [
                {
                    "id": agent.id,
                    "name": agent.name,
                    "description": agent.description,
                    "readOnly": agent.read_only,
                }
                for agent in agents
            ],
            "sessions": sessions,
            "activeSessions": app_services.runtime.active_sessions(),
            "recovery": app_services.recovery,
            "version": APP_VERSION,
        }
    )


@router.get("/sessions")
async def list_sessions(
    request: Request,
    workspace_id: str | None = None,
    search: str | None = None,
    status: str | None = None,
    pinned: bool | None = None,
    archived: bool | None = None,
    limit: int = Query(default=50, ge=1, le=200),
):
    app_services = services(request)
    async with app_services.db.session() as db:
        stmt = select(Session).order_by(Session.pinned.desc(), Session.updated_at.desc()).limit(limit)
        if workspace_id:
            stmt = stmt.where(Session.workspace_id == workspace_id)
        if status:
            statuses = [item for item in status.split(",") if item]
            stmt = stmt.where(Session.status.in_(statuses))
        if pinned is not None:
            stmt = stmt.where(Session.pinned.is_(pinned))
        if archived is not None:
            stmt = stmt.where(Session.archived.is_(archived))
        if search:
            pattern = f"%{search}%"
            subquery = select(Message.session_id).where(Message.content_json.ilike(pattern))
            stmt = stmt.where(or_(Session.title.ilike(pattern), Session.id.in_(subquery)))
        rows = (await db.execute(stmt)).scalars().all()
        counts = {}
        if rows:
            count_rows = (
                await db.execute(
                    select(Message.session_id, func.count(Message.id))
                    .where(Message.session_id.in_([row.id for row in rows]))
                    .group_by(Message.session_id)
                )
            ).all()
            counts = {session_id: count for session_id, count in count_rows}
    return ok(
        [
            session_dto(row)
            | {"messageCount": counts.get(row.id, 0), "active": app_services.runtime.is_busy(row.id)}
            for row in rows
        ]
    )


@router.post("/sessions")
async def create_session(request: Request, body: CreateSessionRequest):
    app_services = services(request)
    config = app_services.config
    if not config.model_configured():
        raise ApiError(409, "NO_MODEL", "尚未配置可用模型，请先在设置中配置服务商和模型")
    model_ref = _require_model(config, body.model)
    model = _model_config(config, model_ref)
    reasoning = body.reasoning_level or config.defaults.reasoning_level
    if reasoning not in model.reasoning_levels:
        reasoning = model.reasoning_levels[0] if model.reasoning_levels else "off"
    async with app_services.db.session() as db:
        workspace = (
            await db.get(Workspace, body.workspace_id)
            if body.workspace_id
            else (await db.execute(select(Workspace).limit(1))).scalars().first()
        )
        if workspace is None:
            raise ApiError(409, "NO_WORKSPACE", "尚未创建工作区")
        mode = body.mode or config.defaults.mode
        agent_id = body.agent_id or ("plan" if mode == "plan" else config.defaults.agent)
        agent = await db.get(Agent, agent_id)
        if agent is None:
            agent = await db.get(Agent, "build")
        if agent is None:
            raise ApiError(409, "NO_AGENT", "内置 Agent 缺失，请重新初始化数据库")
        if agent.read_only:
            mode = "plan"
        created = utcnow()
        system_prompt, env_info = build_system_prompt(
            config=config, agent=agent, workspace=workspace, created_at=created
        )
        session = Session(
            workspace_id=workspace.id,
            agent_id=agent.id,
            title=(body.title or "新会话")[:200],
            model=model_ref,
            reasoning_level=reasoning,
            mode=mode,
            status="idle",
            system_prompt=system_prompt,
            env_info=env_info,
            created_at=created,
            updated_at=created,
        )
        db.add(session)
        await db.flush()
        await app_services.events.emit(
            db,
            session.id,
            "session.created",
            {"session": session_dto(session)},
        )
        await db.commit()
        return ok({"session": session_dto(session)}, "created")


@router.get("/sessions/{session_id}")
async def get_session(request: Request, session_id: str):
    app_services = services(request)
    async with app_services.db.session() as db:
        snapshot = await build_snapshot(db, session_id, app_services.config, app_services.events)
    if snapshot is None:
        raise ApiError(404, "NOT_FOUND", "会话不存在")
    snapshot["active"] = app_services.runtime.is_busy(session_id)
    return ok(snapshot)


@router.patch("/sessions/{session_id}")
async def update_session(request: Request, session_id: str, body: UpdateSessionRequest):
    app_services = services(request)
    config = app_services.config
    async with app_services.db.session() as db:
        session = await _require_session(db, session_id)
        notices: list[str] = []
        if body.title is not None:
            session.title = body.title.strip()
        if body.pinned is not None:
            session.pinned = body.pinned
        if body.archived is not None:
            session.archived = body.archived
        if body.model is not None and body.model != session.model:
            _require_model(config, body.model)
            session.model = body.model
            notices.append("会话模型已切换。中途切换模型会导致前缀缓存失效，后续请求可能产生额外费用。")
        if body.reasoning_level is not None:
            model = _model_config(config, body.model or session.model)
            if body.reasoning_level not in model.reasoning_levels:
                raise ApiError(422, "INVALID_INPUT", f"该模型不支持推理等级 {body.reasoning_level}")
            session.reasoning_level = body.reasoning_level
        if body.mode is not None:
            session.mode = body.mode
            target_agent = "plan" if body.mode == "plan" else "build"
            if await db.get(Agent, target_agent):
                session.agent_id = target_agent
            notices.append(
                "Agent 模式已切换为 "
                + ("Plan（只读）" if body.mode == "plan" else "Build（可修改）")
                + "。系统 prompt 保持不变，新能力以本通知追加。"
            )
        if body.agent_id is not None and body.agent_id != session.agent_id:
            agent = await db.get(Agent, body.agent_id)
            if agent is None:
                raise ApiError(422, "INVALID_INPUT", "Agent 不存在")
            session.agent_id = agent.id
            session.mode = "plan" if agent.read_only else session.mode
        session.updated_at = utcnow()
        await db.flush()
        await app_services.events.emit(db, session_id, "session.updated", {"session": session_dto(session)})
        await db.commit()
        archived = session.archived
        payload = session_dto(session)
    if not app_services.runtime.is_busy(session_id):
        for notice in notices:
            await app_services.runtime.append_notice(session_id, notice)
    if archived and app_services.runtime.is_busy(session_id):
        await app_services.runtime.cancel(session_id)
    return ok({"session": payload})


@router.post("/sessions/{session_id}/messages")
async def send_message(request: Request, session_id: str, body: SendMessageRequest):
    app_services = services(request)
    async with app_services.db.session() as db:
        session = await _require_session(db, session_id)
        if not session.model:
            raise ApiError(409, "NO_MODEL", "会话未绑定模型")
    try:
        result = await app_services.runtime.send_message(
            session_id,
            body.content,
            client_message_id=body.client_message_id,
            mode=body.mode,
        )
    except LookupError as exc:
        raise ApiError(404, "NOT_FOUND", str(exc)) from exc
    except PermissionError as exc:
        raise ApiError(403, "FORBIDDEN", str(exc)) from exc
    except ValueError as exc:
        raise ApiError(422, "INVALID_INPUT", str(exc)) from exc
    return ok(result, "queued" if result.get("queued") else "accepted")


@router.post("/sessions/{session_id}/pause")
async def pause_session(request: Request, session_id: str):
    app_services = services(request)
    async with app_services.db.session() as db:
        await _require_session(db, session_id)
    await app_services.runtime.pause(session_id, reason="pause")
    return ok({"status": "paused"})


@router.post("/sessions/{session_id}/cancel")
async def cancel_session(request: Request, session_id: str):
    app_services = services(request)
    async with app_services.db.session() as db:
        await _require_session(db, session_id)
    await app_services.runtime.cancel(session_id)
    return ok({"status": "idle"})


@router.post("/sessions/{session_id}/resume")
async def resume_session(request: Request, session_id: str):
    app_services = services(request)
    try:
        turn_id = await app_services.runtime.resume(session_id)
    except LookupError as exc:
        raise ApiError(404, "NOT_FOUND", str(exc)) from exc
    except ValueError as exc:
        raise ApiError(409, "SESSION_BUSY", str(exc)) from exc
    return ok({"status": "running", "turnId": turn_id})


@router.post("/sessions/{session_id}/interactions/{interaction_id}/answer")
async def answer_interaction(request: Request, session_id: str, interaction_id: str, body: AnswerRequest):
    app_services = services(request)
    try:
        result = await app_services.runtime.answer_interaction(session_id, interaction_id, body.answer)
    except LookupError as exc:
        raise ApiError(404, "NOT_FOUND", str(exc)) from exc
    except ValueError as exc:
        raise ApiError(422, "INVALID_ANSWER", str(exc)) from exc
    return ok(result)


@router.patch("/sessions/{session_id}/queue/{queue_id}")
async def update_queue_item(request: Request, session_id: str, queue_id: str, body: QueueUpdateRequest):
    app_services = services(request)
    async with app_services.db.session() as db:
        item = await db.get(QueuedMessage, queue_id)
        if item is None or item.session_id != session_id or item.status != "pending":
            raise ApiError(404, "NOT_FOUND", "队列消息不存在或已处理")
        if body.content is not None:
            item.content = body.content
        if body.mode is not None:
            item.mode = body.mode
        await db.flush()
        await app_services.events.emit(
            db,
            session_id,
            "queue.updated",
            {"queue": await app_services.runtime._queue_payload(db, session_id)},
        )
        await db.commit()
    return ok({"queue": await _queue_for(app_services, session_id)})


@router.delete("/sessions/{session_id}/queue/{queue_id}")
async def delete_queue_item(request: Request, session_id: str, queue_id: str):
    app_services = services(request)
    async with app_services.db.session() as db:
        item = await db.get(QueuedMessage, queue_id)
        if item is None or item.session_id != session_id or item.status != "pending":
            raise ApiError(404, "NOT_FOUND", "队列消息不存在或已处理")
        item.status = "deleted"
        await db.flush()
        await app_services.events.emit(
            db,
            session_id,
            "queue.updated",
            {"queue": await app_services.runtime._queue_payload(db, session_id)},
        )
        await db.commit()
    return ok({"queue": await _queue_for(app_services, session_id)})


async def _queue_for(app_services: Services, session_id: str) -> list[dict[str, Any]]:
    async with app_services.db.session() as db:
        return await app_services.runtime._queue_payload(db, session_id)


@router.post("/sessions/{session_id}/compact")
async def compact(request: Request, session_id: str):
    app_services = services(request)
    if app_services.runtime.is_busy(session_id):
        raise ApiError(409, "SESSION_BUSY", "会话正在运行，请先暂停或等待本轮结束再压缩")
    async with app_services.db.session() as db:
        session = await _require_session(db, session_id)
        messages = (
            await db.execute(select(func.count(Message.id)).where(Message.session_id == session_id))
        ).scalar_one()
        if not messages:
            raise ApiError(422, "EMPTY_SESSION", "空会话无需压缩")
        session.status = "compacting"
        await app_services.events.emit(db, session_id, "session.status", {"status": "compacting"})
        await db.commit()
    try:
        async with app_services.db.session() as db:
            compaction = await compact_session(db, app_services.events, app_services.config, session_id)
    finally:
        async with app_services.db.session() as db:
            session = await db.get(Session, session_id)
            if session is not None:
                session.status = "idle"
                session.updated_at = utcnow()
            await app_services.events.emit(db, session_id, "session.status", {"status": "idle"})
            await db.commit()
    return ok(
        {
            "id": compaction.id,
            "summary": compaction.summary,
            "coveredUntilSeq": compaction.covered_until_seq,
            "tokensBefore": compaction.tokens_before,
            "tokensAfter": compaction.tokens_after,
        }
    )


@router.get("/sessions/{session_id}/export")
async def export(
    request: Request,
    session_id: str,
    format: Literal["markdown", "json"] = "json",
    include_reasoning: bool = True,
    include_tools: bool = True,
):
    app_services = services(request)
    async with app_services.db.session() as db:
        try:
            content, media_type, filename = await export_session(
                db,
                app_services.events,
                app_services.config,
                session_id,
                fmt=format,
                include_reasoning=include_reasoning,
                include_tools=include_tools,
            )
        except LookupError as exc:
            raise ApiError(404, "NOT_FOUND", str(exc)) from exc
    quoted = quote(filename)
    return Response(
        content=content,
        media_type=f"{media_type}; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename=\"session.{'md' if format == 'markdown' else 'json'}\"; filename*=UTF-8''{quoted}"
        },
    )


@router.get("/sessions/{session_id}/debug/requests")
async def debug_requests(request: Request, session_id: str):
    app_services = services(request)
    async with app_services.db.session() as db:
        await _require_session(db, session_id)
        rows = await build_debug_requests(db, session_id)
    return ok(rows)


@router.get("/sessions/{session_id}/events")
async def session_events(
    request: Request,
    session_id: str,
    after: int | None = Query(default=None, ge=0),
):
    app_services = services(request)
    async with app_services.db.session() as db:
        await _require_session(db, session_id)
    header = request.headers.get("last-event-id")
    last_id = after or 0
    if header:
        try:
            last_id = int(header)
        except ValueError as exc:
            raise ApiError(400, "INVALID_INPUT", "Last-Event-ID 必须是整数") from exc
    queue = await app_services.broker.subscribe(session_id)
    _ = session_id_var.set(session_id)

    async def generator():
        nonlocal last_id
        try:
            # Subscribe before replay to avoid a gap; dedupe by event id.
            while True:
                async with app_services.db.session() as db:
                    batch = await app_services.events.replay(db, session_id, last_id, limit=500)
                if not batch:
                    break
                for event in batch:
                    if event.id > last_id:
                        last_id = event.id
                        yield event.to_sse()
                if len(batch) < 500:
                    break
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if event.id <= last_id:
                    continue
                last_id = event.id
                yield event.to_sse()
        finally:
            await app_services.broker.unsubscribe(session_id, queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Request-ID": _request_id(),
        },
    )


@router.get("/sessions/{session_id}/shares")
async def list_shares(request: Request, session_id: str):
    app_services = services(request)
    async with app_services.db.session() as db:
        await _require_session(db, session_id)
        rows = (
            (
                await db.execute(
                    select(Share).where(Share.session_id == session_id).order_by(Share.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
    return ok(
        [
            {
                "id": row.id,
                "token": row.token,
                "expiresAt": row.expires_at,
                "revoked": row.revoked,
                "createdAt": row.created_at,
                "url": f"/api/v1/shares/{row.token}",
            }
            for row in rows
        ]
    )


@router.post("/sessions/{session_id}/shares")
async def create_share(request: Request, session_id: str, body: ShareCreateRequest):
    app_services = services(request)
    token = secrets.token_urlsafe(32)
    expires_at = None
    if body.expires_in_hours:
        expires_at = (
            (datetime.now(UTC) + timedelta(hours=body.expires_in_hours))
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
    async with app_services.db.session() as db:
        await _require_session(db, session_id)
        share = Share(
            session_id=session_id,
            token=token,
            expires_at=expires_at,
            hide_tool_io=body.hide_tool_io,
        )
        db.add(share)
        await db.flush()
        await db.commit()
        return ok(
            {
                "id": share.id,
                "token": token,
                "url": f"/api/v1/shares/{token}",
                "expiresAt": expires_at,
            },
            "created",
        )


@router.delete("/shares/{share_id}")
async def revoke_share(request: Request, share_id: str):
    app_services = services(request)
    async with app_services.db.session() as db:
        share = await db.get(Share, share_id)
        if share is None:
            raise ApiError(404, "NOT_FOUND", "分享不存在")
        share.revoked = True
        await db.commit()
    return ok({"id": share_id, "revoked": True})


@router.get("/shares/{token}")
async def get_share(request: Request, token: str):
    app_services = services(request)
    async with app_services.db.session() as db:
        share = (await db.execute(select(Share).where(Share.token == token))).scalars().first()
        if share is None or share.revoked:
            raise ApiError(404, "NOT_FOUND", "分享链接不存在或已撤销")
        if share.expires_at:
            try:
                expiry = datetime.fromisoformat(share.expires_at.replace("Z", "+00:00"))
                if expiry < datetime.now(UTC):
                    raise ApiError(410, "SHARE_EXPIRED", "分享链接已过期")
            except ValueError:
                pass
        try:
            payload = await build_share_payload(
                db,
                app_services.events,
                app_services.config,
                share.session_id,
                hide_tool_io=share.hide_tool_io,
            )
        except LookupError as exc:
            raise ApiError(404, "NOT_FOUND", str(exc)) from exc
    payload["share"] = {
        "expiresAt": share.expires_at,
        "createdAt": share.created_at,
    }
    return ok(payload)


@router.post("/sessions/{session_id}/fork")
async def fork(request: Request, session_id: str, body: ForkRequest):
    app_services = services(request)
    async with app_services.db.session() as db:
        source = await _require_session(db, session_id)
        forked = await fork_session(
            db,
            app_services.config,
            app_services.events,
            source,
            message_id=body.message_id,
        )
    return ok({"session": session_dto(forked)}, "created")


@router.post("/sessions/{session_id}/messages/{message_id}/edit")
async def edit_message(request: Request, session_id: str, message_id: str, body: EditMessageRequest):
    app_services = services(request)
    async with app_services.db.session() as db:
        source = await _require_session(db, session_id)
        message = await db.get(Message, message_id)
        if message is None or message.session_id != session_id or message.role != "user":
            raise ApiError(404, "NOT_FOUND", "只能编辑本会话中的用户消息")
        forked = await fork_session(
            db,
            app_services.config,
            app_services.events,
            source,
            message_id=message_id,
            title_suffix=" (edited)",
            include_target=False,
        )
    await app_services.runtime.send_message(forked.id, body.content, client_message_id=body.client_message_id)
    return ok({"session": session_dto(forked), "branch": True}, "created")


@router.get("/workspaces")
async def list_workspaces(request: Request):
    app_services = services(request)
    async with app_services.db.session() as db:
        rows = (await db.execute(select(Workspace).order_by(Workspace.created_at.asc()))).scalars().all()
    return ok(
        [
            {"id": row.id, "name": row.name, "rootPath": row.root_path, "createdAt": row.created_at}
            for row in rows
        ]
    )


@router.post("/workspaces")
async def create_workspace(request: Request, body: WorkspaceCreateRequest):
    app_services = services(request)
    base = app_services.config.data_dir / "workspaces"
    if body.root_path:
        path = Path(body.root_path).expanduser().resolve()
        if not path.is_dir():
            raise ApiError(422, "INVALID_INPUT", "工作区目录不存在")
        allowed = app_services.config.security.allow_outside_workspace or path.is_relative_to(
            app_services.config.data_dir.resolve()
        )
        if not allowed:
            raise ApiError(403, "FORBIDDEN", "出于安全考虑，新工作区必须位于应用数据目录内")
    else:
        slug = "".join(char if char.isalnum() or char in "-_" else "-" for char in body.name).strip("-")
        path = (base / (slug or new_id("workspace"))).resolve()
        path.mkdir(parents=True, exist_ok=True)
    async with app_services.db.session() as db:
        workspace = Workspace(name=body.name.strip(), root_path=str(path))
        db.add(workspace)
        await db.commit()
    return ok(
        {
            "id": workspace.id,
            "name": workspace.name,
            "rootPath": workspace.root_path,
            "createdAt": workspace.created_at,
        },
        "created",
    )


@router.get("/workspaces/{workspace_id}/files")
async def workspace_files(request: Request, workspace_id: str, path: str = ".", show_hidden: bool = False):
    app_services = services(request)
    async with app_services.db.session() as db:
        workspace = await db.get(Workspace, workspace_id)
        if workspace is None:
            raise ApiError(404, "NOT_FOUND", "工作区不存在")
    try:
        target = resolve_workspace_path(Path(workspace.root_path), path)
    except PathSecurityError as exc:
        raise ApiError(403, "PATH_DENIED", str(exc)) from exc
    if not target.exists():
        raise ApiError(404, "NOT_FOUND", "路径不存在")
    entries: list[dict[str, Any]] = []
    if target.is_dir():
        ignored = {".git", "node_modules", ".venv", "__pycache__", ".DS_Store", "dist", "build"}
        for child in sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            if child.name in ignored or (not show_hidden and child.name.startswith(".")):
                continue
            try:
                stat = child.stat()
                entries.append(
                    {
                        "name": child.name,
                        "path": str(child.relative_to(Path(workspace.root_path))),
                        "type": "dir" if child.is_dir() else "file",
                        "size": stat.st_size if child.is_file() else None,
                    }
                )
            except OSError:
                continue
    return ok(
        {
            "path": str(target.relative_to(Path(workspace.root_path))) or ".",
            "isDirectory": target.is_dir(),
            "entries": entries,
        }
    )


@router.get("/permissions")
async def list_permissions(request: Request):
    app_services = services(request)
    async with app_services.db.session() as db:
        grants = await _all_grants(db)
    return ok(
        {
            "config": app_services.config.permissions.model_dump(),
            "grants": [
                {
                    "id": grant.id,
                    "tool": grant.tool,
                    "pattern": grant.pattern,
                    "scope": grant.scope,
                    "createdAt": grant.created_at,
                }
                for grant in grants
            ],
        }
    )


@router.delete("/permissions/grants/{grant_id}")
async def revoke_grant(request: Request, grant_id: str):
    app_services = services(request)
    async with app_services.db.session() as db:
        grant = await db.get(PermissionGrant, grant_id)
        if grant is None:
            raise ApiError(404, "NOT_FOUND", "授权记录不存在")
        await db.delete(grant)
        await db.commit()
    return ok({"id": grant_id, "revoked": True})


@router.put("/sessions/{session_id}/goal")
async def upsert_goal(request: Request, session_id: str, body: GoalRequest):
    app_services = services(request)
    async with app_services.db.session() as db:
        await _require_session(db, session_id)
        goal = (await db.execute(select(Goal).where(Goal.session_id == session_id))).scalars().first()
        if goal is None:
            goal = Goal(session_id=session_id, objective=body.objective, criteria=body.criteria)
            db.add(goal)
            await db.flush()
        else:
            goal.objective = body.objective
            goal.criteria = body.criteria
            goal.status = "active"
            goal.updated_at = utcnow()
            await db.execute(GoalTask.__table__.delete().where(GoalTask.goal_id == goal.id))
        for index, item in enumerate(body.tasks):
            db.add(
                GoalTask(
                    goal_id=goal.id,
                    title=item.title,
                    status=item.status,
                    position=index,
                )
            )
        await db.flush()
        tasks = (
            (
                await db.execute(
                    select(GoalTask).where(GoalTask.goal_id == goal.id).order_by(GoalTask.position)
                )
            )
            .scalars()
            .all()
        )
        await app_services.events.emit(db, session_id, "goal.updated", {"goal": _goal_payload(goal, tasks)})
        await db.commit()
        payload = _goal_payload(goal, tasks)
    return ok({"goal": payload})


@router.patch("/sessions/{session_id}/goal/tasks/{task_id}")
async def update_goal_task(request: Request, session_id: str, task_id: str, body: GoalTaskUpdate):
    app_services = services(request)
    async with app_services.db.session() as db:
        goal = (await db.execute(select(Goal).where(Goal.session_id == session_id))).scalars().first()
        if goal is None:
            raise ApiError(404, "NOT_FOUND", "会话没有目标")
        task = await db.get(GoalTask, task_id)
        if task is None or task.goal_id != goal.id:
            raise ApiError(404, "NOT_FOUND", "目标任务不存在")
        if body.title is not None:
            task.title = body.title
        if body.status is not None:
            task.status = body.status
        task.updated_at = utcnow()
        goal.updated_at = utcnow()
        await db.flush()
        tasks = (
            (
                await db.execute(
                    select(GoalTask).where(GoalTask.goal_id == goal.id).order_by(GoalTask.position)
                )
            )
            .scalars()
            .all()
        )
        await app_services.events.emit(db, session_id, "goal.updated", {"goal": _goal_payload(goal, tasks)})
        await db.commit()
    return ok({"goal": _goal_payload(goal, tasks)})


@router.get("/system/status")
async def system_status(request: Request):
    app_services = services(request)
    async with app_services.db.session() as db:
        session_count = (await db.execute(select(func.count(Session.id)))).scalar_one()
        task_count = (
            await db.execute(select(func.count(Task.id)).where(Task.status.in_(["queued", "running"])))
        ).scalar_one()
    uptime_seconds = int((datetime.now(UTC) - app_services.started_at).total_seconds())
    return ok(
        {
            "version": APP_VERSION,
            "uptimeSeconds": uptime_seconds,
            "activeSessions": app_services.runtime.active_sessions(),
            "sseSubscribers": app_services.broker.subscriber_count,
            "sessions": session_count,
            "runningTasks": task_count,
            "modelConfigured": app_services.config.model_configured(),
            "recovery": app_services.recovery,
            "dataDir": str(app_services.config.data_dir),
        }
    )


@router.get("/healthz")
async def healthz(request: Request):
    app_services = services(request)
    try:
        async with app_services.db.session() as db:
            await db.execute(select(1))
        db_ok = True
    except Exception:  # noqa: BLE001
        db_ok = False
    return JSONResponse(
        status_code=200 if db_ok else 503,
        content={
            "status": "ok" if db_ok else "degraded",
            "version": APP_VERSION,
            "database": "ok" if db_ok else "unavailable",
            "modelConfigured": app_services.config.model_configured(),
        },
    )


@router.get("/readyz")
async def readyz(request: Request):
    app_services = services(request)
    if not app_services.config.model_configured():
        return JSONResponse(
            status_code=503,
            content={"status": "not-ready", "reason": "NO_MODEL"},
        )
    async with app_services.db.session() as db:
        await db.execute(select(1))
    return {"status": "ready"}


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError):
        return JSONResponse(
            status_code=exc.status,
            content={
                "code": exc.code,
                "message": exc.message,
                "data": exc.data,
                "timestamp": utcnow(),
                "requestId": _request_id(),
            },
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError):
        details = [
            {"path": ".".join(str(part) for part in error["loc"]), "message": error["msg"]}
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "code": "INVALID_INPUT",
                "message": "请求参数校验失败",
                "data": {"errors": details},
                "timestamp": utcnow(),
                "requestId": _request_id(),
            },
        )

    @app.exception_handler(LookupError)
    async def _lookup_error(_request: Request, exc: LookupError):
        return JSONResponse(
            status_code=404,
            content={
                "code": "NOT_FOUND",
                "message": str(exc),
                "data": None,
                "timestamp": utcnow(),
                "requestId": _request_id(),
            },
        )

    @app.exception_handler(PermissionError)
    async def _permission_error(_request: Request, exc: PermissionError):
        return JSONResponse(
            status_code=403,
            content={
                "code": "FORBIDDEN",
                "message": str(exc),
                "data": None,
                "timestamp": utcnow(),
                "requestId": _request_id(),
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, exc: Exception):
        logger.exception("unhandled API error")
        return JSONResponse(
            status_code=500,
            content={
                "code": "INTERNAL_ERROR",
                "message": "服务器内部错误，请查看日志（请求 ID 可用于排查）",
                "data": None,
                "timestamp": utcnow(),
                "requestId": _request_id(),
            },
        )


def install_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        token = request_id_var.set(request_id)
        session_token = session_id_var.set("")
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = request_id
            if request.url.path.startswith("/api/"):
                response.headers.setdefault("Cache-Control", "no-store")
            return response
        finally:
            duration = (time.perf_counter() - start) * 1000
            if request.url.path != "/api/v1/healthz":
                logger.info(
                    "http request",
                    extra={
                        "method": request.method,
                        "path": request.url.path,
                        "status": status,
                        "duration_ms": round(duration, 1),
                    },
                )
            session_id_var.reset(session_token)
            request_id_var.reset(token)
