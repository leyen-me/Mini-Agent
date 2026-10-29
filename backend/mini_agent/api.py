"""REST + SSE API."""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import re
import secrets
import shutil
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import yaml
from fastapi import APIRouter, FastAPI, File, Form, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import func, or_, select

from .agents import agent_dto
from .commands import command_dto
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
from .hooks import hook_dto
from .ignore import WorkspaceIgnore
from .logging_utils import request_id_var, session_id_var
from .mcp import McpError, mcp_dto
from .models import (
    Agent,
    Checkpoint,
    Compaction,
    Event,
    Goal,
    GoalTask,
    Message,
    PendingInteraction,
    PermissionGrant,
    QueuedMessage,
    Session,
    SessionMcp,
    SessionSkill,
    Share,
    Task,
    ToolCall,
    Turn,
    Upload,
    UsageRecord,
    Workspace,
    new_id,
    utcnow,
)
from .plugins import plugin_dto
from .prompts import build_system_prompt
from .security import PathSecurityError, redact_text, resolve_workspace_path
from .serialization import (
    build_debug_requests,
    build_snapshot,
    session_dto,
)
from .uploads import upload_dto
from .webhooks import webhook_dto

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1")

APP_VERSION = "0.2.0"


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
    content: str = Field(default="", max_length=200_000)
    client_message_id: str | None = Field(default=None, max_length=120)
    mode: Literal["auto", "steer", "queue"] = "auto"
    attachment_ids: list[str] = Field(default_factory=list)


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
    network: dict[str, Any] | None = None
    search: dict[str, Any] | None = None
    tools: dict[str, Any] | None = None
    sandbox: dict[str, Any] | None = None
    retention: dict[str, Any] | None = None
    compaction: dict[str, Any] | None = None
    subagents: dict[str, Any] | None = None
    backup: dict[str, Any] | None = None


class ProviderCreateRequest(CamelModel):
    id: str
    name: str = ""
    protocol: Literal["openai-chat", "openai-responses", "anthropic", "mock"] = "openai-chat"
    base_url: str = ""
    auth_type: Literal["api_key", "oauth_client_credentials", "none"] = "api_key"
    api_key: str | None = None
    api_key_env: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    oauth: dict[str, Any] | None = None
    enabled: bool = True
    supports_balance: bool = False
    balance_path: str = "/user/balance"
    models: list[dict[str, Any]] = Field(default_factory=list)


class ProviderUpdateRequest(CamelModel):
    name: str | None = None
    protocol: Literal["openai-chat", "openai-responses", "anthropic", "mock"] | None = None
    base_url: str | None = None
    auth_type: Literal["api_key", "oauth_client_credentials", "none"] | None = None
    api_key: str | None = None
    api_key_env: str | None = None
    headers: dict[str, str] | None = None
    oauth: dict[str, Any] | None = None
    enabled: bool | None = None
    supports_balance: bool | None = None
    balance_path: str | None = None


class ProviderModelRequest(CamelModel):
    id: str
    name: str = ""
    context_window: int = Field(default=128_000, ge=1_000)
    max_output: int = Field(default=8_192, ge=1)
    capabilities: list[str] = Field(default_factory=lambda: ["text", "tools"])
    reasoning_levels: list[str] = Field(default_factory=lambda: ["off"])
    pricing: dict[str, float] = Field(default_factory=dict)
    structured_output: bool = False


class AgentPayload(CamelModel):
    name: str = "New Agent"
    description: str = ""
    system_prompt: str = ""
    read_only: bool = False
    enabled: bool = True
    default_model: str | None = None
    default_reasoning_level: str = "off"
    tools: list[str] | None = None
    permissions: dict[str, Any] = Field(default_factory=dict)
    skills: list[str] = Field(default_factory=list)
    mcp: list[str] = Field(default_factory=list)
    structured_output: dict[str, Any] | None = None


class AgentUpdatePayload(CamelModel):
    name: str | None = None
    description: str | None = None
    system_prompt: str | None = None
    read_only: bool | None = None
    enabled: bool | None = None
    default_model: str | None = None
    default_reasoning_level: str | None = None
    tools: list[str] | None = None
    permissions: dict[str, Any] | None = None
    skills: list[str] | None = None
    mcp: list[str] | None = None
    structured_output: dict[str, Any] | None = None


class SkillSaveRequest(CamelModel):
    scope: Literal["system", "workspace"] = "system"
    name: str
    content: str = ""
    description: str = ""
    workspace_id: str | None = None


class SkillToggleRequest(CamelModel):
    enabled: bool = True


class McpCreateRequest(CamelModel):
    name: str
    description: str = ""
    transport: Literal["stdio", "http", "sse"] = "stdio"
    command: str = ""
    args: list[str] = Field(default_factory=list)
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    timeout_seconds: int = 30
    scope: str = "system"


class McpUpdateRequest(CamelModel):
    name: str | None = None
    description: str | None = None
    transport: Literal["stdio", "http", "sse"] | None = None
    command: str | None = None
    args: list[str] | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    env: dict[str, str] | None = None
    enabled: bool | None = None
    timeout_seconds: int | None = None
    scope: str | None = None


class McpToggleRequest(CamelModel):
    session_id: str
    enabled: bool = True


class CommandPayload(CamelModel):
    name: str
    description: str = ""
    prompt_template: str
    agent_id: str | None = None
    model: str | None = None
    reasoning_level: str | None = None
    enabled: bool = True
    scope: str = "system"


class CommandUpdatePayload(CamelModel):
    name: str | None = None
    description: str | None = None
    prompt_template: str | None = None
    agent_id: str | None = None
    model: str | None = None
    reasoning_level: str | None = None
    enabled: bool | None = None
    scope: str | None = None


class CommandRenderRequest(CamelModel):
    arguments: str = ""


class HookPayload(CamelModel):
    name: str
    event: str
    command: str
    enabled: bool = True
    timeout_seconds: int = 30
    scope: str = "system"


class HookUpdatePayload(CamelModel):
    name: str | None = None
    event: str | None = None
    command: str | None = None
    enabled: bool | None = None
    timeout_seconds: int | None = None
    scope: str | None = None


class PluginToggleRequest(CamelModel):
    enabled: bool


class PluginTrustRequest(CamelModel):
    trusted: bool


class SessionImportRequest(CamelModel):
    data: dict[str, Any]


class ToggleRequest(CamelModel):
    enabled: bool = True


class AgentTestRequest(CamelModel):
    content: str = Field(min_length=1)
    workspace_id: str | None = None


class ProviderFetchRequest(CamelModel):
    import_models: bool = True


class WebhookPayload(CamelModel):
    name: str = "Webhook"
    url: str
    events: list[str] = Field(default_factory=lambda: ["*"])
    secret: str = ""
    enabled: bool = True


class WebhookUpdatePayload(CamelModel):
    name: str | None = None
    url: str | None = None
    events: list[str] | None = None
    secret: str | None = None
    enabled: bool | None = None


class BackupRequest(CamelModel):
    include_artifacts: bool | None = None


class RestoreRequest(CamelModel):
    backup_id: str


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
        allowed = {
            "model",
            "reasoning_level",
            "agent",
            "mode",
            "fallback_models",
            "vision_model",
            "compaction_model",
        }
        unknown = set(patch.defaults) - allowed
        if unknown:
            raise ApiError(422, "INVALID_INPUT", f"不支持的默认配置：{', '.join(sorted(unknown))}")
        update["defaults"] = patch.defaults
    for section in (
        "network",
        "search",
        "tools",
        "sandbox",
        "retention",
        "compaction",
        "subagents",
        "backup",
    ):
        value = getattr(patch, section, None)
        if value is not None:
            update[section] = value
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
    from .config import (
        BackupConfig,
        CompactionConfig,
        NetworkConfig,
        RetentionConfig,
        SandboxConfig,
        SearchConfig,
        SubagentConfig,
        ToolsConfig,
    )

    candidates: dict[str, Any] = {}
    for section, model_cls in (
        ("network", NetworkConfig),
        ("search", SearchConfig),
        ("tools", ToolsConfig),
        ("sandbox", SandboxConfig),
        ("retention", RetentionConfig),
        ("compaction", CompactionConfig),
        ("subagents", SubagentConfig),
        ("backup", BackupConfig),
    ):
        value = getattr(patch, section, None)
        if value is not None:
            base = getattr(config, section).model_dump()
            try:
                candidates[section] = model_cls(**deep_merge(base, value))
            except ValueError as exc:
                raise ApiError(422, "INVALID_INPUT", str(exc)) from exc
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
    for section, value in candidates.items():
        setattr(config, section, value)
    from .config import apply_policy

    apply_policy(config)


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
        agent = await db.get(Agent, agent_id) or await db.get(Agent, "build")
        if agent is None:
            raise ApiError(409, "NO_AGENT", "内置 Agent 缺失，请重新初始化数据库")
        if not agent.enabled:
            raise ApiError(409, "AGENT_DISABLED", "该 Agent 已停用")
        if agent.read_only:
            mode = "plan"
        model_ref = _require_model(config, body.model or agent.default_model)
        model = _model_config(config, model_ref)
        reasoning = body.reasoning_level or agent.default_reasoning_level or config.defaults.reasoning_level
        if reasoning not in model.reasoning_levels:
            reasoning = model.reasoning_levels[0] if model.reasoning_levels else "off"
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
        await db.commit()
    # Defaults are enabled after the session exists so Skill/MCP services can scope to it.
    await app_services.skills.ensure_defaults(session, agent)
    for server_id in json.loads(agent.mcp_json or "[]"):
        try:
            await app_services.mcp.set_session_enabled(session.id, str(server_id), True)
        except LookupError:
            continue
    metadata_skills = await app_services.skills.metadata_for_session(session)
    metadata_mcp = await app_services.mcp.metadata_for_session(session.id)
    refreshed, env_info = build_system_prompt(
        config=config,
        agent=agent,
        workspace=workspace,
        created_at=created,
        skills=metadata_skills,
        mcp_servers=metadata_mcp,
    )
    session.system_prompt = refreshed
    session.env_info = env_info
    async with app_services.db.session() as db:
        row = await db.get(Session, session.id)
        if row:
            row.system_prompt = refreshed
            row.env_info = env_info
        await app_services.events.emit(db, session.id, "session.created", {"session": session_dto(session)})
        await db.commit()
    if app_services.hooks is not None:
        await app_services.hooks.run(
            "session.start",
            {"sessionId": session.id, "agentId": session.agent_id},
            session=session,
            workspace_root=Path(workspace.root_path),
        )
    return ok({"session": session_dto(session)}, "created")


@router.get("/sessions/{session_id}")
async def get_session(request: Request, session_id: str):
    app_services = services(request)
    async with app_services.db.session() as db:
        snapshot = await build_snapshot(db, session_id, app_services.config, app_services.events)
    if snapshot is None:
        raise ApiError(404, "NOT_FOUND", "会话不存在")
    snapshot["active"] = app_services.runtime.is_busy(session_id)
    async with app_services.db.session() as db:
        session_row = await _require_session(db, session_id)
        children = (
            (
                await db.execute(
                    select(Session)
                    .where(Session.parent_session_id == session_id)
                    .order_by(Session.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
    try:
        snapshot["skills"] = await app_services.skills.list(session_row)
    except Exception:  # noqa: BLE001
        snapshot["skills"] = []
    try:
        snapshot["mcp"] = await app_services.mcp.list_for_session(session_id)
    except Exception:  # noqa: BLE001
        snapshot["mcp"] = []
    snapshot["childSessions"] = [
        {"id": child.id, "title": child.title, "status": child.status} for child in children
    ]
    return ok(snapshot)


@router.patch("/sessions/{session_id}")
async def update_session(request: Request, session_id: str, body: UpdateSessionRequest):
    app_services = services(request)
    config = app_services.config
    async with app_services.db.session() as db:
        session = await _require_session(db, session_id)
        notices: list[str] = []
        agent_changed = False
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
            if not agent.enabled:
                raise ApiError(422, "AGENT_DISABLED", "该 Agent 已停用")
            session.agent_id = agent.id
            session.mode = "plan" if agent.read_only else session.mode
            agent_changed = True
            notices.append("Agent 已切换，system prompt 与默认 Skill/MCP 将重建。")
        session.updated_at = utcnow()
        await db.flush()
        await app_services.events.emit(db, session_id, "session.updated", {"session": session_dto(session)})
        await db.commit()
        archived = session.archived
        payload = session_dto(session)
    if agent_changed:
        async with app_services.db.session() as db:
            row = await _require_session(db, session_id)
            workspace = await db.get(Workspace, row.workspace_id)
            agent = await db.get(Agent, row.agent_id)
        if row is not None and workspace is not None and agent is not None:
            await app_services.skills.ensure_defaults(row, agent)
            for server_id in json.loads(agent.mcp_json or "[]"):
                try:
                    await app_services.mcp.set_session_enabled(session_id, str(server_id), True)
                except LookupError:
                    continue
            metadata_skills = await app_services.skills.metadata_for_session(row)
            metadata_mcp = await app_services.mcp.metadata_for_session(session_id)
            refreshed, env_info = build_system_prompt(
                config=config,
                agent=agent,
                workspace=workspace,
                created_at=row.created_at,
                skills=metadata_skills,
                mcp_servers=metadata_mcp,
            )
            async with app_services.db.session() as db:
                target = await db.get(Session, session_id)
                if target is not None:
                    target.system_prompt = refreshed
                    target.env_info = env_info
                    target.updated_at = utcnow()
                    await db.flush()
                    await app_services.events.emit(
                        db, session_id, "session.updated", {"session": session_dto(target)}
                    )
                    await db.commit()
                    payload = session_dto(target)
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
            attachment_ids=body.attachment_ids,
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
        await _require_session(db, session_id)
        message_count = (
            await db.execute(select(func.count(Message.id)).where(Message.session_id == session_id))
        ).scalar_one()
        if not message_count:
            raise ApiError(422, "EMPTY_SESSION", "空会话无需压缩")
    compaction = await app_services.runtime.compact_now(session_id)
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
            {
                "id": row.id,
                "name": row.name,
                "alias": row.alias,
                "description": row.description,
                "rootPath": row.root_path,
                "createdAt": row.created_at,
                "updatedAt": row.updated_at,
            }
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
            "alias": workspace.alias,
            "description": workspace.description,
            "rootPath": workspace.root_path,
            "createdAt": workspace.created_at,
            "updatedAt": workspace.updated_at,
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
        ignore = WorkspaceIgnore(Path(workspace.root_path))
        for child in sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            if ignore.ignored(child, is_dir=child.is_dir()) or (
                not show_hidden and child.name.startswith(".")
            ):
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


class WorkspaceUpdateRequest(CamelModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    alias: str | None = Field(default=None, max_length=120)
    description: str | None = None


@router.patch("/workspaces/{workspace_id}")
async def update_workspace(request: Request, workspace_id: str, body: WorkspaceUpdateRequest):
    app_services = services(request)
    async with app_services.db.session() as db:
        workspace = await db.get(Workspace, workspace_id)
        if workspace is None:
            raise ApiError(404, "NOT_FOUND", "工作区不存在")
        if body.name is not None:
            workspace.name = body.name
        if body.alias is not None:
            workspace.alias = body.alias
        if body.description is not None:
            workspace.description = body.description
        workspace.updated_at = utcnow()
        await db.commit()
        return ok(
            {
                "id": workspace.id,
                "name": workspace.name,
                "alias": workspace.alias,
                "description": workspace.description,
                "rootPath": workspace.root_path,
                "createdAt": workspace.created_at,
                "updatedAt": workspace.updated_at,
            }
        )


@router.delete("/workspaces/{workspace_id}")
async def delete_workspace(request: Request, workspace_id: str, confirm: bool = False):
    if not confirm:
        raise ApiError(422, "CONFIRM_REQUIRED", "删除工作区需要 confirm=true")
    app_services = services(request)
    async with app_services.db.session() as db:
        workspace = await db.get(Workspace, workspace_id)
        if workspace is None:
            raise ApiError(404, "NOT_FOUND", "工作区不存在")
        used = (
            (await db.execute(select(Session.id).where(Session.workspace_id == workspace_id).limit(1)))
            .scalars()
            .first()
        )
        if used:
            raise ApiError(409, "WORKSPACE_IN_USE", "工作区仍有会话，请先删除会话")
        await db.delete(workspace)
        await db.commit()
    return ok({"id": workspace_id, "deleted": True})


@router.get("/workspaces/{workspace_id}/search")
async def search_workspace(
    request: Request,
    workspace_id: str,
    q: str,
    mode: Literal["name", "content"] = "content",
    limit: int = Query(default=50, ge=1, le=200),
):
    app_services = services(request)
    async with app_services.db.session() as db:
        workspace = await db.get(Workspace, workspace_id)
        if workspace is None:
            raise ApiError(404, "NOT_FOUND", "工作区不存在")
    root = Path(workspace.root_path).resolve()
    ignore = WorkspaceIgnore(root)
    needle = q.strip()
    if not needle:
        raise ApiError(422, "INVALID_INPUT", "q 不能为空")
    results: list[dict[str, Any]] = []
    regex = None
    if mode == "content":
        try:
            regex = re.compile(needle)
        except re.error as exc:
            raise ApiError(422, "INVALID_INPUT", f"正则表达式无效：{exc}") from exc
    for current, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if not ignore.ignored(Path(current) / name, is_dir=True)]
        for name in files:
            path = Path(current) / name
            if ignore.ignored(path) or path.is_symlink():
                continue
            relative = str(path.relative_to(root))
            if mode == "name":
                if needle.lower() in relative.lower():
                    results.append({"path": relative, "line": None, "text": None})
                continue
            try:
                if path.stat().st_size > 1_000_000 or b"\x00" in path.read_bytes()[:1024]:
                    continue
                for number, line in enumerate(
                    path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
                ):
                    if regex and regex.search(line):
                        results.append({"path": relative, "line": number, "text": line[:300]})
                        if len(results) >= limit:
                            break
            except OSError:
                continue
            if len(results) >= limit:
                break
        if len(results) >= limit:
            break
    return ok({"query": needle, "mode": mode, "results": results, "count": len(results)})


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


# ---------------------------------------------------------- providers / agents


@router.get("/providers")
async def list_providers(request: Request):
    app_services = services(request)
    return ok(app_services.providers.public_list())


@router.post("/providers")
async def create_provider(request: Request, body: ProviderCreateRequest):
    app_services = services(request)
    payload = body.model_dump(exclude_none=True)
    provider = await app_services.providers.create(payload)
    return ok(
        {
            "provider": app_services.providers.model_public(provider.id, provider.models[0])
            if provider.models
            else None,
            "id": provider.id,
        },
        "created",
    )


@router.patch("/providers/{provider_id}")
async def update_provider(request: Request, provider_id: str, body: ProviderUpdateRequest):
    app_services = services(request)
    await app_services.providers.update(provider_id, body.model_dump(exclude_unset=True, exclude_none=False))
    return ok(app_services.providers.public_list())


@router.delete("/providers/{provider_id}")
async def delete_provider(request: Request, provider_id: str):
    app_services = services(request)
    await app_services.providers.delete(provider_id)
    return ok({"id": provider_id, "deleted": True})


@router.post("/providers/{provider_id}/test")
async def test_provider(request: Request, provider_id: str):
    app_services = services(request)
    return ok(await app_services.providers.test_connection(provider_id))


@router.get("/providers/{provider_id}/balance")
async def provider_balance(request: Request, provider_id: str):
    app_services = services(request)
    return ok(await app_services.providers.balance(provider_id))


@router.post("/providers/{provider_id}/fetch-models")
async def fetch_provider_models(request: Request, provider_id: str, body: ProviderFetchRequest):
    app_services = services(request)
    models = await app_services.providers.fetch_models(provider_id)
    imported = []
    if body.import_models:
        for item in models:
            try:
                await app_services.providers.upsert_model(
                    provider_id,
                    {
                        "id": item["id"],
                        "name": item.get("name") or item["id"],
                        "capabilities": item.get("capabilities") or ["text"],
                        "reasoning_levels": item.get("reasoning_levels") or ["off"],
                    },
                )
                imported.append(item["id"])
            except Exception:  # noqa: BLE001
                continue
    return ok({"models": models, "imported": imported})


@router.post("/providers/{provider_id}/models")
async def upsert_provider_model(request: Request, provider_id: str, body: ProviderModelRequest):
    app_services = services(request)
    model = await app_services.providers.upsert_model(provider_id, body.model_dump())
    return ok({"model": app_services.providers.model_public(provider_id, model)}, "created")


@router.delete("/providers/{provider_id}/models/{model_id}")
async def delete_provider_model(request: Request, provider_id: str, model_id: str):
    app_services = services(request)
    await app_services.providers.delete_model(provider_id, model_id)
    return ok({"id": model_id, "deleted": True})


@router.get("/agents")
async def list_agents(request: Request, include_disabled: bool = True):
    app_services = services(request)
    items = await app_services.agents.list(include_disabled=include_disabled)
    return ok([agent_dto(agent) for agent in items])


@router.get("/agents/{agent_id}")
async def get_agent(request: Request, agent_id: str):
    app_services = services(request)
    return ok(agent_dto(await app_services.agents.get(agent_id)))


@router.post("/agents")
async def create_agent(request: Request, body: AgentPayload):
    app_services = services(request)
    agent = await app_services.agents.create(body.model_dump())
    return ok({"agent": agent_dto(agent)}, "created")


@router.patch("/agents/{agent_id}")
async def update_agent(request: Request, agent_id: str, body: AgentUpdatePayload):
    app_services = services(request)
    agent = await app_services.agents.update(agent_id, body.model_dump(exclude_unset=True))
    return ok({"agent": agent_dto(agent)})


@router.delete("/agents/{agent_id}")
async def delete_agent(request: Request, agent_id: str):
    app_services = services(request)
    await app_services.agents.delete(agent_id)
    return ok({"id": agent_id, "deleted": True})


@router.post("/agents/{agent_id}/duplicate")
async def duplicate_agent(request: Request, agent_id: str):
    app_services = services(request)
    agent = await app_services.agents.duplicate(agent_id)
    return ok({"agent": agent_dto(agent)}, "created")


@router.get("/agents/{agent_id}/export")
async def export_agent(request: Request, agent_id: str):
    app_services = services(request)
    return ok(await app_services.agents.export(agent_id))


@router.post("/agents/import")
async def import_agent(request: Request, body: dict[str, Any]):
    app_services = services(request)
    agent = await app_services.agents.import_agent(body)
    return ok({"agent": agent_dto(agent)}, "created")


@router.post("/agents/from-session/{session_id}")
async def agent_from_session(request: Request, session_id: str):
    app_services = services(request)
    agent = await app_services.agents.from_session(session_id)
    return ok({"agent": agent_dto(agent)}, "created")


@router.post("/agents/{agent_id}/test")
async def test_agent(request: Request, agent_id: str, body: AgentTestRequest):
    app_services = services(request)
    agent = await app_services.agents.get(agent_id)
    config = app_services.config
    model_ref = _require_model(config, agent.default_model)
    created = await create_session(
        request,
        CreateSessionRequest(agentId=agent_id, model=model_ref, workspace_id=body.workspace_id),
    )
    session_id = created["data"]["session"]["id"]
    await app_services.runtime.send_message(session_id, body.content, mode="auto")
    return ok({"sessionId": session_id}, "accepted")


# ----------------------------------------------------------- skills / mcp


@router.get("/skills")
async def list_skills(request: Request, session_id: str | None = None):
    app_services = services(request)
    session_row = None
    if session_id:
        async with app_services.db.session() as db:
            session_row = await _require_session(db, session_id)
    return ok(await app_services.skills.list(session_row))


@router.post("/skills")
async def save_skill(request: Request, body: SkillSaveRequest):
    app_services = services(request)
    workspace_root = None
    if body.workspace_id:
        async with app_services.db.session() as db:
            workspace = await db.get(Workspace, body.workspace_id)
            if workspace is None:
                raise ApiError(404, "NOT_FOUND", "工作区不存在")
            workspace_root = Path(workspace.root_path)
    return ok(
        app_services.skills.save(body.scope, body.name, body.content, body.description, workspace_root),
        "saved",
    )


@router.delete("/skills/{scope}/{name}")
async def delete_skill(request: Request, scope: str, name: str, workspace_id: str | None = None):
    app_services = services(request)
    workspace_root = None
    if workspace_id:
        async with app_services.db.session() as db:
            workspace = await db.get(Workspace, workspace_id)
            if workspace is None:
                raise ApiError(404, "NOT_FOUND", "工作区不存在")
            workspace_root = Path(workspace.root_path)
    app_services.skills.delete(scope, name, workspace_root)
    return ok({"name": name, "scope": scope, "deleted": True})


@router.post("/sessions/{session_id}/skills/{name}/toggle")
async def toggle_session_skill(request: Request, session_id: str, name: str, body: ToggleRequest):
    app_services = services(request)
    async with app_services.db.session() as db:
        session = await _require_session(db, session_id)
    result = await app_services.skills.enable(session, name, body.enabled)
    await app_services.runtime.append_notice(
        session_id,
        f"Skill {name} 已{'启用' if body.enabled else '停用'}。需要时请调用 load_skill 读取完整内容；system prompt 不会被改写。",
    )
    return ok(result)


@router.get("/skills/{name}")
async def get_skill(request: Request, name: str, session_id: str | None = None):
    app_services = services(request)
    session_row = None
    if session_id:
        async with app_services.db.session() as db:
            session_row = await _require_session(db, session_id)
    return ok(await app_services.skills.get(name, session_row))


@router.get("/mcp/servers")
async def list_mcp_servers(request: Request, session_id: str | None = None):
    app_services = services(request)
    if session_id:
        return ok(await app_services.mcp.list_for_session(session_id))
    return ok([mcp_dto(row) for row in await app_services.mcp.list()])


@router.post("/mcp/servers")
async def create_mcp_server(request: Request, body: McpCreateRequest):
    app_services = services(request)
    row = await app_services.mcp.create(body.model_dump())
    return ok({"server": mcp_dto(row)}, "created")


@router.patch("/mcp/servers/{server_id}")
async def update_mcp_server(request: Request, server_id: str, body: McpUpdateRequest):
    app_services = services(request)
    row = await app_services.mcp.update(server_id, body.model_dump(exclude_unset=True))
    return ok({"server": mcp_dto(row)})


@router.delete("/mcp/servers/{server_id}")
async def delete_mcp_server(request: Request, server_id: str):
    app_services = services(request)
    await app_services.mcp.delete(server_id)
    return ok({"id": server_id, "deleted": True})


@router.post("/mcp/servers/{server_id}/test")
async def test_mcp_server(request: Request, server_id: str):
    app_services = services(request)
    return ok(await app_services.mcp.test(server_id))


@router.post("/mcp/servers/{server_id}/refresh")
async def refresh_mcp_server(request: Request, server_id: str):
    app_services = services(request)
    return ok({"tools": await app_services.mcp.refresh_tools(server_id)})


@router.post("/sessions/{session_id}/mcp/{server_id}/toggle")
async def toggle_session_mcp(request: Request, session_id: str, server_id: str, body: ToggleRequest):
    app_services = services(request)
    await app_services.mcp.set_session_enabled(session_id, server_id, body.enabled)
    await app_services.runtime.append_notice(
        session_id,
        f"MCP 服务 {server_id} 已{'启用' if body.enabled else '停用'}。工具列表会在下一步刷新。",
    )
    return ok({"serverId": server_id, "enabled": body.enabled})


# --------------------------------------------------------- commands / hooks


@router.get("/commands")
async def list_commands(request: Request, enabled_only: bool = False):
    app_services = services(request)
    commands = [command_dto(row) for row in await app_services.commands.list(enabled_only)]
    for plugin, manifest in await app_services.plugins.enabled_manifests():
        for item in manifest.get("commands", []):
            commands.append(
                {
                    "id": f"plugin:{plugin.id}:{item.get('name')}",
                    "name": str(item.get("name") or "").lstrip("/"),
                    "description": str(item.get("description") or ""),
                    "promptTemplate": str(item.get("promptTemplate") or item.get("prompt_template") or ""),
                    "agentId": item.get("agentId"),
                    "model": item.get("model"),
                    "reasoningLevel": item.get("reasoningLevel"),
                    "enabled": True,
                    "scope": f"plugin:{plugin.id}",
                    "pluginId": plugin.id,
                    "createdAt": plugin.created_at,
                    "updatedAt": plugin.updated_at,
                }
            )
    return ok(commands)


@router.post("/commands")
async def create_command(request: Request, body: CommandPayload):
    app_services = services(request)
    row = await app_services.commands.create(body.model_dump())
    return ok({"command": command_dto(row)}, "created")


@router.patch("/commands/{command_id}")
async def update_command(request: Request, command_id: str, body: CommandUpdatePayload):
    app_services = services(request)
    row = await app_services.commands.update(command_id, body.model_dump(exclude_unset=True))
    return ok({"command": command_dto(row)})


@router.delete("/commands/{command_id}")
async def delete_command(request: Request, command_id: str):
    app_services = services(request)
    await app_services.commands.delete(command_id)
    return ok({"id": command_id, "deleted": True})


@router.post("/commands/{command_id}/render")
async def render_command(request: Request, command_id: str, body: CommandRenderRequest):
    app_services = services(request)
    return ok(await app_services.commands.render(command_id, body.arguments))


@router.post("/commands/resolve")
async def resolve_command(request: Request, body: dict[str, Any]):
    app_services = services(request)
    text = str(body.get("text") or "")
    resolved = await app_services.commands.resolve_slash(text)
    if resolved is not None:
        return ok(resolved)
    if text.startswith("/"):
        name, _, rest = text[1:].partition(" ")
        from .commands import render_template

        for plugin, manifest in await app_services.plugins.enabled_manifests():
            for item in manifest.get("commands", []):
                if str(item.get("name") or "").lstrip("/") == name:
                    template = str(item.get("promptTemplate") or item.get("prompt_template") or "")
                    return ok(
                        {
                            "command": {
                                "id": f"plugin:{plugin.id}:{name}",
                                "name": name,
                                "description": item.get("description") or "",
                                "promptTemplate": template,
                                "agentId": item.get("agentId"),
                                "model": item.get("model"),
                                "reasoningLevel": item.get("reasoningLevel"),
                                "enabled": True,
                                "scope": f"plugin:{plugin.id}",
                                "pluginId": plugin.id,
                            },
                            "prompt": render_template(template, rest),
                        }
                    )
    raise ApiError(404, "NOT_FOUND", "不是已启用的自定义命令")


@router.get("/hooks")
async def list_hooks(request: Request):
    app_services = services(request)
    return ok([hook_dto(row) for row in await app_services.hooks.list()])


@router.post("/hooks")
async def create_hook(request: Request, body: HookPayload):
    app_services = services(request)
    row = await app_services.hooks.create(body.model_dump())
    return ok({"hook": hook_dto(row)}, "created")


@router.patch("/hooks/{hook_id}")
async def update_hook(request: Request, hook_id: str, body: HookUpdatePayload):
    app_services = services(request)
    row = await app_services.hooks.update(hook_id, body.model_dump(exclude_unset=True))
    return ok({"hook": hook_dto(row)})


@router.delete("/hooks/{hook_id}")
async def delete_hook(request: Request, hook_id: str):
    app_services = services(request)
    await app_services.hooks.delete(hook_id)
    return ok({"id": hook_id, "deleted": True})


# -------------------------------------------------------------- plugins


@router.get("/plugins")
async def list_plugins(request: Request):
    app_services = services(request)
    return ok([plugin_dto(row) for row in await app_services.plugins.list()])


@router.post("/plugins/install")
async def install_plugin(request: Request, file: UploadFile = File(...), trusted: bool = Form(False)):
    app_services = services(request)
    data = await file.read()
    if len(data) > 20 * 1024 * 1024:
        raise ApiError(422, "INVALID_INPUT", "插件包超过 20MB")
    row = await app_services.plugins.install_zip(data, trusted=trusted)
    return ok({"plugin": plugin_dto(row)}, "created")


@router.post("/plugins/{plugin_id}/enable")
async def enable_plugin(request: Request, plugin_id: str, body: PluginToggleRequest):
    app_services = services(request)
    row = await app_services.plugins.set_enabled(plugin_id, body.enabled)
    return ok({"plugin": plugin_dto(row)})


@router.post("/plugins/{plugin_id}/trust")
async def trust_plugin(request: Request, plugin_id: str, body: PluginTrustRequest):
    app_services = services(request)
    row = await app_services.plugins.set_trusted(plugin_id, body.trusted)
    return ok({"plugin": plugin_dto(row)})


@router.delete("/plugins/{plugin_id}")
async def uninstall_plugin(request: Request, plugin_id: str):
    app_services = services(request)
    await app_services.plugins.uninstall(plugin_id)
    return ok({"id": plugin_id, "deleted": True})


# --------------------------------------------------------------- uploads


@router.post("/uploads")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    session_id: str | None = Form(None),
):
    app_services = services(request)
    data = await file.read()
    upload = await app_services.uploads.save(
        data,
        filename=file.filename or "upload.bin",
        mime=file.content_type or "",
        session_id=session_id,
    )
    return ok({"upload": upload_dto(upload)}, "created")


@router.get("/uploads/{upload_id}")
async def get_upload(request: Request, upload_id: str):
    app_services = services(request)
    upload = await app_services.uploads.get(upload_id)
    return ok(upload_dto(upload))


@router.get("/uploads/{upload_id}/content")
async def upload_content(request: Request, upload_id: str):
    app_services = services(request)
    upload = await app_services.uploads.get(upload_id)
    path = app_services.uploads.absolute_path(upload)
    return FileResponse(path, media_type=upload.mime, filename=upload.filename)


# ------------------------------------------------------------ checkpoints


@router.get("/sessions/{session_id}/checkpoints")
async def list_checkpoints(request: Request, session_id: str, turn_id: str | None = None):
    app_services = services(request)
    if turn_id:
        rows = await app_services.checkpoints.list_for_turn(session_id, turn_id)
    else:
        rows = await app_services.checkpoints.list_for_session(session_id)
    return ok(
        [
            {
                "id": row.id,
                "turnId": row.turn_id,
                "path": row.path,
                "operation": row.operation,
                "applied": row.applied,
                "createdAt": row.created_at,
            }
            for row in rows
        ]
    )


@router.post("/sessions/{session_id}/turns/{turn_id}/undo")
async def undo_turn(request: Request, session_id: str, turn_id: str):
    app_services = services(request)
    return ok(await app_services.checkpoints.undo_turn(session_id, turn_id))


@router.post("/sessions/{session_id}/turns/{turn_id}/redo")
async def redo_turn(request: Request, session_id: str, turn_id: str):
    app_services = services(request)
    return ok(await app_services.checkpoints.redo_turn(session_id, turn_id))


@router.get("/checkpoints/{checkpoint_id}/diff")
async def checkpoint_diff(request: Request, checkpoint_id: str):
    app_services = services(request)
    return ok(await app_services.checkpoints.diff(checkpoint_id))


# --------------------------------------------------------- webhooks / ops


@router.get("/webhooks")
async def list_webhooks(request: Request):
    app_services = services(request)
    return ok(
        [
            {
                "id": row.id,
                **{
                    k: v
                    for k, v in __import__("mini_agent.webhooks", fromlist=["webhook_dto"])
                    .webhook_dto(row)
                    .items()
                    if k != "id"
                },
            }
            for row in await app_services.webhooks.list()
        ]
    )


@router.post("/webhooks")
async def create_webhook(request: Request, body: WebhookPayload):
    app_services = services(request)
    row = await app_services.webhooks.create(body.model_dump())
    return ok({"webhook": webhook_dto(row)}, "created")


@router.patch("/webhooks/{webhook_id}")
async def update_webhook(request: Request, webhook_id: str, body: WebhookUpdatePayload):
    app_services = services(request)
    row = await app_services.webhooks.update(webhook_id, body.model_dump(exclude_unset=True))
    return ok({"webhook": webhook_dto(row)})


@router.delete("/webhooks/{webhook_id}")
async def delete_webhook(request: Request, webhook_id: str):
    app_services = services(request)
    await app_services.webhooks.delete(webhook_id)
    return ok({"id": webhook_id, "deleted": True})


@router.post("/webhooks/{webhook_id}/test")
async def test_webhook(request: Request, webhook_id: str):
    app_services = services(request)
    return ok(await app_services.webhooks.test(webhook_id))


@router.get("/backups")
async def list_backups(request: Request):
    app_services = services(request)
    rows = await app_services.backups.list()
    return ok(
        [
            {
                "id": row.id,
                "filename": row.filename,
                "size": row.size,
                "includes": json.loads(row.includes_json or "[]"),
                "createdAt": row.created_at,
            }
            for row in rows
        ]
    )


@router.post("/backups")
async def create_backup(request: Request, body: BackupRequest):
    app_services = services(request)
    row = await app_services.backups.create(include_artifacts=body.include_artifacts)
    return ok(
        {"id": row.id, "filename": row.filename, "size": row.size, "createdAt": row.created_at}, "created"
    )


@router.get("/backups/{backup_id}/download")
async def download_backup(request: Request, backup_id: str):
    app_services = services(request)
    path = app_services.backups.path(backup_id)
    return FileResponse(path, media_type="application/zip", filename=path.name)


@router.delete("/backups/{backup_id}")
async def delete_backup(request: Request, backup_id: str):
    app_services = services(request)
    await app_services.backups.delete(backup_id)
    return ok({"id": backup_id, "deleted": True})


@router.post("/backups/restore")
async def restore_backup(request: Request, body: RestoreRequest):
    app_services = services(request)
    result = await app_services.backups.stage_restore(body.backup_id)
    return ok(result, "restore staged; restart the service to apply it")


@router.post("/system/retention/cleanup")
async def cleanup_retention(request: Request):
    app_services = services(request)
    return ok(await app_services.retention.cleanup())


@router.get("/system/logs/export")
async def export_logs(request: Request):
    app_services = services(request)
    path = app_services.logs.export_zip()
    return FileResponse(path, media_type="application/zip", filename=path.name)


@router.get("/system/policy")
async def policy_info(request: Request):
    app_services = services(request)
    config = app_services.config
    return ok(
        {
            "path": str(config.policy_path) if config.policy_path else None,
            "source": config.source,
            "enforced": config.policy_path is not None,
        }
    )


@router.get("/system/budget")
async def budget_info(request: Request):
    return ok(services(request).config.budget.model_dump())


@router.get("/system/retention")
async def retention_info(request: Request):
    return ok(services(request).config.retention.model_dump())


@router.get("/system/config-raw")
async def config_raw(request: Request):
    app_services = services(request)
    path = app_services.config.config_path
    if path is None or not path.exists():
        raise ApiError(404, "NOT_FOUND", "当前使用内置配置，没有原始配置文件")
    text = path.read_text(encoding="utf-8", errors="replace")
    return ok({"path": str(path), "content": redact_text(text, app_services.config.secret_values())})


# ---------------------------------------------------------- delete / import


@router.delete("/sessions/{session_id}")
async def delete_session(request: Request, session_id: str, confirm: bool = False):
    if not confirm:
        raise ApiError(422, "CONFIRM_REQUIRED", "删除会话需要 confirm=true")
    app_services = services(request)
    if app_services.runtime.is_busy(session_id):
        await app_services.runtime.cancel(session_id)
    async with app_services.db.session() as db:
        await _require_session(db, session_id)
        children = (
            (await db.execute(select(Session.id).where(Session.parent_session_id == session_id)))
            .scalars()
            .all()
        )
        if children:
            raise ApiError(409, "HAS_CHILDREN", "该会话包含子 Agent 会话，请先删除子会话")
        data_root = app_services.config.data_dir.resolve()
        for upload in (
            (await db.execute(select(Upload).where(Upload.session_id == session_id))).scalars().all()
        ):
            target = (data_root / upload.path).resolve()
            if target.is_relative_to(data_root):
                target.unlink(missing_ok=True)
        for task in (await db.execute(select(Task).where(Task.session_id == session_id))).scalars().all():
            shutil.rmtree(data_root / "artifacts" / task.id, ignore_errors=True)
        for checkpoint in (
            (await db.execute(select(Checkpoint).where(Checkpoint.session_id == session_id))).scalars().all()
        ):
            for relative in (checkpoint.backup_path, checkpoint.after_path):
                if not relative:
                    continue
                target = (data_root / relative).resolve()
                if target.is_relative_to(data_root):
                    target.unlink(missing_ok=True)
        for model in (
            Event,
            UsageRecord,
            Compaction,
            Goal,
            PendingInteraction,
            QueuedMessage,
            SessionSkill,
            SessionMcp,
            Share,
            Task,
            ToolCall,
            Upload,
            Message,
            Turn,
            Checkpoint,
        ):
            await db.execute(model.__table__.delete().where(model.__table__.c.session_id == session_id))
        session_row = await db.get(Session, session_id)
        if session_row:
            await db.delete(session_row)
        await db.commit()
    return ok({"id": session_id, "deleted": True})


@router.post("/sessions/import")
async def import_session(request: Request, body: SessionImportRequest):
    app_services = services(request)
    data = body.data
    raw_session = data.get("session") or {}
    config = app_services.config
    async with app_services.db.session() as db:
        workspace = await db.get(Workspace, raw_session.get("workspaceId") or "")
        if workspace is None:
            workspace = (await db.execute(select(Workspace).limit(1))).scalars().first()
        if workspace is None:
            raise ApiError(409, "NO_WORKSPACE", "没有可用工作区")
        agent_id = raw_session.get("agentId") or config.defaults.agent
        agent = await db.get(Agent, agent_id) or await db.get(Agent, "build")
        if agent is None:
            raise ApiError(409, "NO_AGENT", "没有可用 Agent")
        model_ref = _require_model(config, raw_session.get("model"))
        created = utcnow()
        system_prompt = str(data.get("systemPrompt") or "")
        env_info = ""
        if not system_prompt:
            system_prompt, env_info = build_system_prompt(
                config=config, agent=agent, workspace=workspace, created_at=created
            )
        session = Session(
            workspace_id=workspace.id,
            agent_id=agent.id,
            title=str(raw_session.get("title") or "导入会话")[:200],
            model=model_ref,
            reasoning_level=str(raw_session.get("reasoningLevel") or "off"),
            mode=str(raw_session.get("mode") or "build"),
            status="idle",
            system_prompt=system_prompt,
            env_info=env_info,
            created_at=created,
            updated_at=created,
        )
        db.add(session)
        await db.flush()
        raw_messages = data.get("rawMessages") or data.get("messages") or []
        turn_map: dict[str, str] = {}
        for item in data.get("turns") or []:
            turn = Turn(
                session_id=session.id,
                seq=int(item.get("seq") or len(turn_map) + 1),
                status="completed",
                started_at=item.get("startedAt") or created,
                ended_at=item.get("endedAt") or created,
                usage_json=json.dumps(item.get("usage") or {}),
            )
            db.add(turn)
            await db.flush()
            turn_map[str(item.get("id"))] = turn.id
        seq = 0
        for item in raw_messages:
            seq += 1
            content = item.get("content") or {}
            raw = item.get("raw") or {"role": item.get("role"), "content": content.get("text", "")}
            message = Message(
                session_id=session.id,
                turn_id=turn_map.get(str(item.get("turnId") or "")),
                seq=seq,
                role=str(item.get("role") or "user"),
                content_json=json.dumps(content, ensure_ascii=False),
                raw_json=json.dumps(raw, ensure_ascii=False),
                status="complete",
            )
            db.add(message)
        await db.flush()
        await app_services.events.emit(db, session.id, "session.created", {"session": session_dto(session)})
        await db.commit()
    return ok({"session": session_dto(session)}, "created")


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

    @app.exception_handler(ValueError)
    async def _value_error(_request: Request, exc: ValueError):
        return JSONResponse(
            status_code=422,
            content={
                "code": "INVALID_INPUT",
                "message": str(exc),
                "data": None,
                "timestamp": utcnow(),
                "requestId": _request_id(),
            },
        )

    @app.exception_handler(McpError)
    async def _mcp_error(_request: Request, exc: McpError):
        return JSONResponse(
            status_code=502,
            content={
                "code": "MCP_ERROR",
                "message": str(exc),
                "data": None,
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


_rate_buckets: dict[str, collections.deque[float]] = {}


def _is_loopback(host: str | None) -> bool:
    return host in {"127.0.0.1", "::1", "localhost", "testclient"}


def install_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        token = request_id_var.set(request_id)
        session_token = session_id_var.set("")
        start = time.perf_counter()
        status = 500
        path = request.url.path
        app_services = getattr(request.app.state, "services", None)
        protected_path = path.startswith("/api/") or path in {"/metrics", "/docs", "/openapi.json", "/redoc"}
        if app_services is not None and protected_path and request.method != "OPTIONS":
            config = app_services.config
            client_host = request.client.host if request.client else None
            forwarded = (
                request.headers.get("x-forwarded-for", "").split(",")[0].strip()
                if config.api.trust_proxy_headers
                else ""
            )
            # Never trust a client-supplied X-Forwarded-For as loopback; when
            # any forwarding header is present, require authentication.
            is_local = _is_loopback(client_host) and not forwarded
            public_share = path.startswith("/api/v1/shares/")
            if config.api.token and config.api.require_token_remote and not is_local and not public_share:
                import hmac as _hmac

                header = request.headers.get("authorization", "")
                supplied = header[7:].strip() if header.lower().startswith("bearer ") else ""
                if not (supplied and _hmac.compare_digest(supplied, config.api.token)):
                    return JSONResponse(
                        status_code=401,
                        content={
                            "code": "UNAUTHORIZED",
                            "message": "缺少或无效的 API Token",
                            "data": None,
                            "timestamp": utcnow(),
                            "requestId": request_id,
                        },
                        headers={"X-Request-ID": request_id},
                    )
            if config.api.rate_limit_per_minute > 0 and path not in {
                "/api/v1/healthz",
                "/api/v1/readyz",
                "/api/v1/system/status",
            }:
                key = (request.headers.get("authorization") or client_host or "anonymous")[:200]
                now = time.monotonic()
                if len(_rate_buckets) > 4_096:
                    for stale_key in list(_rate_buckets):
                        stale = _rate_buckets[stale_key]
                        if not stale or now - stale[-1] > 120:
                            _rate_buckets.pop(stale_key, None)
                bucket = _rate_buckets.setdefault(key, collections.deque())
                while bucket and now - bucket[0] > 60:
                    bucket.popleft()
                if len(bucket) >= config.api.rate_limit_per_minute:
                    return JSONResponse(
                        status_code=429,
                        content={
                            "code": "RATE_LIMITED",
                            "message": "请求过于频繁，请稍后重试",
                            "data": None,
                            "timestamp": utcnow(),
                            "requestId": request_id,
                        },
                        headers={"Retry-After": "60", "X-Request-ID": request_id},
                    )
                bucket.append(now)
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
