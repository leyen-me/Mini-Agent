"""SQLAlchemy models for the persisted agent state.

The schema intentionally mirrors the domain model in ``create-common-agent-guide.md``.
Every mutable piece of runtime state (turns, messages, tool calls, tasks, interactions and
events) is persisted so a browser refresh or a server restart never loses user-visible work.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> str:
    """Return a stable, timezone-aware ISO timestamp for SQLite storage."""

    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


class Base(DeclarativeBase):
    pass


class Workspace(Base):
    __tablename__ = "workspace"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("ws"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    root_path: Mapped[str] = mapped_column(Text, nullable=False)
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False, default="local")
    alias: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)


class Agent(Base):
    __tablename__ = "agent"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    read_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    default_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    default_reasoning_level: Mapped[str] = mapped_column(String(32), nullable=False, default="off")
    tools_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    permissions_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    skills_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    mcp_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    structured_output_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)


class Session(Base):
    __tablename__ = "session"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("sess"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id"), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False, default="build")
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="新会话")
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    reasoning_level: Mapped[str] = mapped_column(String(32), nullable=False, default="off")
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="build")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="idle")
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    env_info: Mapped[str] = mapped_column(Text, nullable=False, default="")
    parent_session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fork_from_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)


class Turn(Base):
    __tablename__ = "turn"
    __table_args__ = (UniqueConstraint("session_id", "seq", name="uq_turn_session_seq"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("turn"))
    session_id: Mapped[str] = mapped_column(ForeignKey("session.id"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="running")
    started_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)
    ended_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    usage_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class Message(Base):
    __tablename__ = "message"
    __table_args__ = (
        UniqueConstraint("session_id", "seq", name="uq_message_session_seq"),
        UniqueConstraint("session_id", "client_message_id", name="uq_message_client_id"),
        Index("ix_message_session_seq", "session_id", "seq"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("msg"))
    session_id: Mapped[str] = mapped_column(ForeignKey("session.id"), nullable=False)
    turn_id: Mapped[str | None] = mapped_column(ForeignKey("turn.id"), nullable=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    raw_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    client_message_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="complete")
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)


class ToolCall(Base):
    __tablename__ = "tool_call"
    __table_args__ = (Index("ix_tool_call_turn", "turn_id"),)

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("session.id"), nullable=False)
    turn_id: Mapped[str] = mapped_column(ForeignKey("turn.id"), nullable=False)
    message_id: Mapped[str] = mapped_column(ForeignKey("message.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    input_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    output_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="validating")
    risk: Mapped[str] = mapped_column(String(16), nullable=False, default="low")
    started_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    ended_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)


class Task(Base):
    __tablename__ = "task"
    __table_args__ = (Index("ix_task_session", "session_id", "status"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("task"))
    session_id: Mapped[str] = mapped_column(ForeignKey("session.id"), nullable=False)
    tool_call_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    notified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)


class QueuedMessage(Base):
    __tablename__ = "queued_message"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("qmsg"))
    session_id: Mapped[str] = mapped_column(ForeignKey("session.id"), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="queue")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    attachments_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    client_message_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)


class PendingInteraction(Base):
    __tablename__ = "pending_interaction"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("int"))
    session_id: Mapped[str] = mapped_column(ForeignKey("session.id"), nullable=False)
    turn_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    type: Mapped[str] = mapped_column(String(24), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    answer_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)
    resolved_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class Event(Base):
    __tablename__ = "event"
    __table_args__ = (Index("ix_event_session_id", "session_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("session.id"), nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)


class UsageRecord(Base):
    __tablename__ = "usage_record"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("use"))
    session_id: Mapped[str] = mapped_column(ForeignKey("session.id"), nullable=False)
    turn_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_hit_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_miss_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost: Mapped[float] = mapped_column(nullable=False, default=0.0)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)


class Compaction(Base):
    __tablename__ = "compaction"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("cmp"))
    session_id: Mapped[str] = mapped_column(ForeignKey("session.id"), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    covered_until_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    tokens_before: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_after: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)


class Goal(Base):
    __tablename__ = "goal"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("goal"))
    session_id: Mapped[str] = mapped_column(ForeignKey("session.id"), nullable=False, unique=True)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    criteria: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)


class GoalTask(Base):
    __tablename__ = "goal_task"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("gtask"))
    goal_id: Mapped[str] = mapped_column(ForeignKey("goal.id"), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)


class PermissionGrant(Base):
    __tablename__ = "permission_grant"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("grant"))
    tool: Mapped[str] = mapped_column(String(80), nullable=False)
    pattern: Mapped[str] = mapped_column(Text, nullable=False, default="*")
    scope: Mapped[str] = mapped_column(String(64), nullable=False, default="global")
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)


class ModelRequest(Base):
    __tablename__ = "model_request"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("req"))
    session_id: Mapped[str] = mapped_column(ForeignKey("session.id"), nullable=False)
    turn_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    step: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    prefix_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prefix_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_hit_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_miss_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)


class Share(Base):
    __tablename__ = "share"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("share"))
    session_id: Mapped[str] = mapped_column(ForeignKey("session.id"), nullable=False)
    token: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    expires_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    hide_tool_io: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)


class Checkpoint(Base):
    __tablename__ = "checkpoint"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("ckpt"))
    session_id: Mapped[str] = mapped_column(ForeignKey("session.id"), nullable=False)
    turn_id: Mapped[str] = mapped_column(String(64), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    existed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    backup_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    before_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    after_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    operation: Mapped[str] = mapped_column(String(32), nullable=False, default="write")
    diff: Mapped[str] = mapped_column(Text, nullable=False, default="")
    applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)


class Provider(Base):
    __tablename__ = "provider"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    protocol: Mapped[str] = mapped_column(String(32), nullable=False, default="openai-chat")
    base_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    auth_type: Mapped[str] = mapped_column(String(32), nullable=False, default="api_key")
    api_key_env: Mapped[str | None] = mapped_column(String(120), nullable=True)
    headers_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    oauth_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    supports_balance: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    balance_path: Mapped[str] = mapped_column(String(200), nullable=False, default="/user/balance")
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)


class ProviderModel(Base):
    __tablename__ = "provider_model"
    __table_args__ = (UniqueConstraint("provider_id", "model_id", name="uq_provider_model"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("pm"))
    provider_id: Mapped[str] = mapped_column(ForeignKey("provider.id"), nullable=False)
    model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    context_window: Mapped[int] = mapped_column(Integer, nullable=False, default=128_000)
    max_output: Mapped[int] = mapped_column(Integer, nullable=False, default=8_192)
    capabilities_json: Mapped[str] = mapped_column(Text, nullable=False, default='["text","tools"]')
    reasoning_levels_json: Mapped[str] = mapped_column(Text, nullable=False, default='["off"]')
    pricing_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    supports_structured_output: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)


class SessionSkill(Base):
    __tablename__ = "session_skill"
    __table_args__ = (UniqueConstraint("session_id", "skill_name", "scope", name="uq_session_skill"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("ssk"))
    session_id: Mapped[str] = mapped_column(ForeignKey("session.id"), nullable=False)
    skill_name: Mapped[str] = mapped_column(String(200), nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False, default="workspace")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)


class McpServer(Base):
    __tablename__ = "mcp_server"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("mcp"))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    transport: Mapped[str] = mapped_column(String(16), nullable=False, default="stdio")
    command: Mapped[str] = mapped_column(Text, nullable=False, default="")
    args_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    headers_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    env_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    tools_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    scope: Mapped[str] = mapped_column(String(32), nullable=False, default="system")
    last_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)


class SessionMcp(Base):
    __tablename__ = "session_mcp"
    __table_args__ = (UniqueConstraint("session_id", "server_id", name="uq_session_mcp"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("smcp"))
    session_id: Mapped[str] = mapped_column(ForeignKey("session.id"), nullable=False)
    server_id: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)


class CustomCommand(Base):
    __tablename__ = "custom_command"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("cmd"))
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    prompt_template: Mapped[str] = mapped_column(Text, nullable=False)
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reasoning_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    scope: Mapped[str] = mapped_column(String(32), nullable=False, default="system")
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)


class Plugin(Base):
    __tablename__ = "plugin"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    version: Mapped[str] = mapped_column(String(40), nullable=False, default="0.0.0")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    path: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    trusted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    manifest_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)


class Hook(Base):
    __tablename__ = "hook"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("hook"))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    command: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    scope: Mapped[str] = mapped_column(String(32), nullable=False, default="system")
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)


class Upload(Base):
    __tablename__ = "upload"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("up"))
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime: Mapped[str] = mapped_column(String(120), nullable=False, default="application/octet-stream")
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="file")
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)


class FileRead(Base):
    __tablename__ = "file_read"
    __table_args__ = (UniqueConstraint("session_id", "path", name="uq_file_read_session_path"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("fr"))
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    read_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)


class Webhook(Base):
    __tablename__ = "webhook"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("wh"))
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    url: Mapped[str] = mapped_column(Text, nullable=False)
    events_json: Mapped[str] = mapped_column(Text, nullable=False, default='["*"]')
    secret: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)


class WebhookDelivery(Base):
    __tablename__ = "webhook_delivery"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("whd"))
    webhook_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    response_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)


class Backup(Base):
    __tablename__ = "backup"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("bak"))
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    includes_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow)
