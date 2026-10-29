"""Session runtime: one writer per session, durable turns, tools and interactions."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .agents import AgentService
from .checkpoints import CheckpointService
from .commands import CommandService
from .compaction import build_model_messages, compact_session
from .config import AppConfig, ResolutionError
from .db import Database
from .events import EventStore
from .hooks import HookService
from .mcp import McpError, McpService
from .model_gateway import (
    BaseGateway,
    ModelError,
    ModelRequest,
    ToolCallRequest,
    Usage,
    messages_prefix_text,
)
from .models import (
    Agent,
    Compaction,
    Message,
    PendingInteraction,
    PermissionGrant,
    QueuedMessage,
    Session,
    Task,
    ToolCall,
    Turn,
    Upload,
    UsageRecord,
    Workspace,
    new_id,
    utcnow,
)
from .models import (
    ModelRequest as ModelRequestRow,
)
from .plugins import PluginService
from .prompts import build_system_prompt
from .security import TOOL_CATEGORY, PermissionDecision, PermissionEvaluator, redact_text
from .serialization import (
    interaction_dto,
    message_dto,
    parse_json,
    tool_call_dto,
)
from .skills import SkillService
from .tasks import TaskManager
from .tokens import calculate_cost, estimate_messages_tokens, estimate_tokens
from .tools import (
    ToolCancelled,
    ToolContext,
    ToolRegistry,
    ToolResult,
)
from .uploads import UploadService

logger = logging.getLogger(__name__)

STREAM_SAVE_INTERVAL = 0.05


class TurnPaused(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(slots=True)
class RuntimeState:
    session_id: str
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    seq_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    turn_task: asyncio.Task[None] | None = None
    current_turn_id: str | None = None
    stop_reason: str | None = None
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    interaction_futures: dict[str, asyncio.Future[dict[str, Any]]] = field(default_factory=dict)
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    tool_tasks: set[asyncio.Task[Any]] = field(default_factory=set)
    shutdown: bool = False

    @property
    def active(self) -> bool:
        return self.turn_task is not None and not self.turn_task.done()


@dataclass(slots=True)
class StreamOutcome:
    content: str
    reasoning: str
    tool_calls: list[ToolCallRequest]
    usage: Usage
    retries: int
    message_id: str
    finish_reason: str = ""


class RuntimeManager:
    def __init__(
        self,
        *,
        db: Database,
        config: AppConfig,
        events: EventStore,
        gateway: BaseGateway,
        registry: ToolRegistry,
        tasks: TaskManager,
        agents: AgentService | None = None,
        skills: SkillService | None = None,
        mcp: McpService | None = None,
        plugins: PluginService | None = None,
        hooks: HookService | None = None,
        uploads: UploadService | None = None,
        checkpoints: CheckpointService | None = None,
        commands: CommandService | None = None,
    ) -> None:
        self.db = db
        self.config = config
        self.events = events
        self.gateway = gateway
        self.registry = registry
        self.tasks = tasks
        self.agents = agents
        self.skills = skills
        self.mcp = mcp
        self.plugins = plugins
        self.hooks = hooks
        self.uploads = uploads
        self.checkpoints = checkpoints
        self.commands = commands
        self.permissions = PermissionEvaluator(config.permissions)
        self._states: dict[str, RuntimeState] = {}
        self._closing = False
        self._subagent_tasks: set[asyncio.Task[Any]] = set()

    # ------------------------------------------------------------------ state

    def state(self, session_id: str) -> RuntimeState:
        return self._states.setdefault(session_id, RuntimeState(session_id))

    def is_busy(self, session_id: str) -> bool:
        return self.state(session_id).active

    def active_sessions(self) -> list[str]:
        return sorted(sid for sid, state in self._states.items() if state.active)

    # -------------------------------------------------------------- messaging

    async def send_message(
        self,
        session_id: str,
        content: str,
        *,
        client_message_id: str | None = None,
        mode: str = "auto",
        attachment_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        content = content.strip()
        if not content and not attachment_ids:
            raise ValueError("消息不能为空")
        state = self.state(session_id)
        async with state.lock:
            async with self.db.session() as db:
                session = await db.get(Session, session_id)
                if session is None:
                    raise LookupError("会话不存在")
                if session.archived:
                    raise PermissionError("会话已归档，无法发送消息")
                if client_message_id:
                    existing = (
                        (
                            await db.execute(
                                select(Message).where(
                                    Message.session_id == session_id,
                                    Message.client_message_id == client_message_id,
                                )
                            )
                        )
                        .scalars()
                        .first()
                    )
                    if existing:
                        return {"queued": False, "duplicate": True, "message": message_dto(existing)}
                    existing_queue = (
                        (
                            await db.execute(
                                select(QueuedMessage).where(
                                    QueuedMessage.session_id == session_id,
                                    QueuedMessage.client_message_id == client_message_id,
                                )
                            )
                        )
                        .scalars()
                        .first()
                    )
                    if existing_queue:
                        return {
                            "queued": True,
                            "duplicate": True,
                            "queueItem": self._queue_dto(existing_queue),
                        }
                if session.status == "waiting_user":
                    raise PermissionError("会话正在等待用户回答，请先提交回答")
                active = state.active or session.status in {"running", "compacting"}
                if active:
                    delivery = "steer" if mode == "steer" else "queue"
                    item = QueuedMessage(
                        session_id=session_id,
                        mode=delivery,
                        content=content,
                        attachments_json=json.dumps(attachment_ids or []),
                        client_message_id=client_message_id,
                    )
                    db.add(item)
                    session.updated_at = utcnow()
                    await db.flush()
                    await self.events.emit(
                        db,
                        session_id,
                        "queue.updated",
                        {"queue": await self._queue_payload(db, session_id)},
                    )
                    await db.commit()
                    return {"queued": True, "duplicate": False, "queueItem": self._queue_dto(item)}
                if state.turn_task is not None and state.turn_task.done():
                    state.turn_task = None
                attachments = await self._load_attachments(db, session_id, attachment_ids or [])
                turn_id = await self._create_turn_in_db(
                    db,
                    session,
                    user_content=content,
                    client_message_id=client_message_id,
                    attachments=attachments,
                )
                await db.commit()
            await self._spawn_turn(state, turn_id)
            return {"queued": False, "duplicate": False, "turnId": turn_id}

    async def _create_turn_in_db(
        self,
        db: AsyncSession,
        session: Session,
        *,
        user_content: str | None,
        client_message_id: str | None = None,
        attachments: list[Upload] | None = None,
    ) -> str:
        message_row: Message | None = None
        if user_content is not None:
            text, raw_content, attachment_dto = self._build_user_content(
                session, user_content, attachments or []
            )
            message_row = await self._add_message(
                db,
                session.id,
                None,
                role="user",
                text=text,
                raw={"role": "user", "content": raw_content},
                client_message_id=client_message_id,
                content_extra={"attachments": attachment_dto},
            )
            for upload in attachments or []:
                upload.message_id = message_row.id
                upload.session_id = session.id
        turn_seq = (
            int(
                (
                    await db.execute(
                        select(func.coalesce(func.max(Turn.seq), 0)).where(Turn.session_id == session.id)
                    )
                ).scalar_one()
            )
            + 1
        )
        turn = Turn(session_id=session.id, seq=turn_seq, status="running", started_at=utcnow())
        db.add(turn)
        await db.flush()
        if message_row is not None:
            message_row.turn_id = turn.id
        if session.title in {"", "新会话"}:
            title_source = user_content or "附件消息"
            session.title = title_source.replace("\n", " ")[:40]
        session.status = "running"
        session.last_error = None
        session.updated_at = utcnow()
        await db.flush()
        if message_row is not None:
            await self.events.emit(db, session.id, "message.created", {"message": message_dto(message_row)})
        await self.events.emit(
            db,
            session.id,
            "turn.started",
            {
                "turn": {
                    "id": turn.id,
                    "seq": turn.seq,
                    "status": turn.status,
                    "startedAt": turn.started_at,
                }
            },
        )
        await self.events.emit(db, session.id, "session.status", {"status": "running"})
        await self.events.emit(db, session.id, "session.updated", {"session": self._session_payload(session)})
        return turn.id

    @staticmethod
    async def _load_attachments(db: AsyncSession, session_id: str, ids: list[str]) -> list[Upload]:
        uploads: list[Upload] = []
        for upload_id in ids:
            upload = await db.get(Upload, upload_id)
            if upload is None or (upload.session_id and upload.session_id != session_id):
                raise PermissionError(f"附件 {upload_id} 不属于当前会话")
            uploads.append(upload)
        return uploads

    def _build_user_content(
        self, session: Session, text: str, attachments: list[Upload]
    ) -> tuple[str, str | list[dict[str, Any]], list[dict[str, Any]]]:
        model = self._model_config(session)
        vision = bool(model and model.supports_vision)
        extra_text: list[str] = []
        parts: list[dict[str, Any]] = [{"type": "text", "text": text}] if text else []
        attachment_dto: list[dict[str, Any]] = []
        image_count = 0
        for upload in attachments:
            attachment_dto.append(
                {
                    "id": upload.id,
                    "filename": upload.filename,
                    "mime": upload.mime,
                    "kind": upload.kind,
                    "url": f"/api/v1/uploads/{upload.id}/content",
                    "width": upload.width,
                    "height": upload.height,
                }
            )
            if upload.kind == "image" and vision and self.uploads is not None:
                parts.append(self.uploads.openai_content(upload))
                image_count += 1
            else:
                label = "图片（当前模型不支持视觉，未发送图像）" if upload.kind == "image" else "附件"
                extra_text.append(f"[{label} {upload.filename}]\n{upload.extracted_text or ''}".strip())
        if extra_text:
            parts.append({"type": "text", "text": "\n\n".join(extra_text)})
        combined_text = text
        if extra_text:
            combined_text = (text + "\n\n" + "\n\n".join(extra_text)).strip()
        if image_count:
            return combined_text, parts, attachment_dto
        return combined_text, combined_text, attachment_dto

    async def _spawn_turn(self, state: RuntimeState, turn_id: str) -> None:
        state.current_turn_id = turn_id
        state.stop_reason = None
        state.cancel_event = asyncio.Event()
        task = asyncio.create_task(
            self._run_turn_wrapper(state, turn_id), name=f"turn:{state.session_id}:{turn_id}"
        )
        state.turn_task = task
        task.add_done_callback(self._turn_done)

    def _turn_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.error("turn task crashed", exc_info=exc)

    # -------------------------------------------------------------- turn loop

    async def _run_turn_wrapper(self, state: RuntimeState, turn_id: str) -> None:
        try:
            await self._run_turn(state, turn_id)
        except asyncio.CancelledError:
            await asyncio.shield(self._handle_stop(state, turn_id))
        except TurnPaused as exc:
            state.stop_reason = exc.reason
            await asyncio.shield(self._handle_stop(state, turn_id))
        except ModelError as exc:
            await asyncio.shield(self._fail_turn(state, turn_id, exc.code, exc.message, exc.to_payload()))
        except Exception as exc:  # noqa: BLE001
            logger.exception("unexpected turn failure", extra={"session_id": state.session_id})
            await asyncio.shield(
                self._fail_turn(
                    state,
                    turn_id,
                    "INTERNAL_ERROR",
                    f"运行出错：{type(exc).__name__}: {redact_text(str(exc))[:200]}",
                    {},
                )
            )
        finally:
            async with state.lock:
                if state.turn_task is task_or_none(state, asyncio.current_task()):
                    state.turn_task = None
                state.current_turn_id = None
                state.stop_reason = None
                state.cancel_event = asyncio.Event()
                state.interaction_futures.clear()
                state.tool_tasks.clear()
            if not state.shutdown and not self._closing:
                asyncio.create_task(self._post_turn(state), name=f"post-turn:{state.session_id}")

    async def _run_turn(self, state: RuntimeState, turn_id: str) -> None:
        session_id = state.session_id
        step = 0
        compacted_this_turn = False
        waited_once = False
        failure_counts: dict[str, int] = {}
        denied_signatures: set[str] = set()
        while True:
            if state.stop_reason:
                raise TurnPaused(state.stop_reason)
            session = await self._get_session(session_id)
            if session is None or session.status in {"paused"}:
                raise TurnPaused("pause")
            if step >= self.config.budget.max_steps_per_turn:
                await self._append_system_message(
                    state,
                    session_id,
                    turn_id,
                    f"已达到本轮最大步数（{self.config.budget.max_steps_per_turn}）。"
                    "系统暂停 Turn，等待用户决定是否继续。",
                )
                raise TurnPaused("budget")
            elapsed = await self._turn_elapsed(turn_id)
            if elapsed > self.config.budget.max_duration_seconds:
                await self._append_system_message(
                    state,
                    session_id,
                    turn_id,
                    f"本轮运行超过 {self.config.budget.max_duration_seconds} 秒，已暂停。",
                )
                raise TurnPaused("budget")
            cost = await self._turn_cost(turn_id)
            if self.config.budget.max_cost_per_turn and cost >= self.config.budget.max_cost_per_turn:
                await self._append_system_message(
                    state,
                    session_id,
                    turn_id,
                    f"本轮费用估算达到上限（${cost:.4f}），已暂停。",
                )
                raise TurnPaused("budget")

            await self._inject_steer(state, session_id, turn_id)
            await self._notify_terminal_tasks(state, session_id, turn_id)
            if not compacted_this_turn:
                compacted_this_turn = await self._maybe_auto_compact(state, session_id)

            session = await self._get_session(session_id)
            if session is None:
                raise RuntimeError("session disappeared")
            messages, prefix_text, prefix_hash, prefix_tokens, tools = await self._build_request(
                session, turn_id
            )
            agent_row = await self._get_agent(session.agent_id)
            structured_output = None
            if agent_row is not None and agent_row.structured_output_json:
                try:
                    structured_output = json.loads(agent_row.structured_output_json)
                except json.JSONDecodeError:
                    structured_output = None
            primary = session.model
            fallback_refs = [ref for ref in self.config.fallback_refs() if ref != primary]
            candidate_refs = [primary, *fallback_refs]
            outcome: StreamOutcome | None = None
            step += 1
            for index, model_ref in enumerate(candidate_refs):
                request = ModelRequest(
                    session_id=session_id,
                    turn_id=turn_id,
                    step=step,
                    model_ref=model_ref,
                    messages=messages,
                    tools=tools,
                    reasoning_level=session.reasoning_level,
                    max_output=self._model_max_output_ref(model_ref),
                    prefix_text=prefix_text,
                    prefix_hash=prefix_hash,
                    structured_output=structured_output,
                )
                request_row_id = await self._start_model_request(request, step, prefix_tokens)
                assistant = await self._create_streaming_message(state, session_id, turn_id)
                try:
                    outcome = await self._stream_model(
                        state,
                        request,
                        assistant,
                        request_row_id,
                        suppress_error_event=index + 1 < len(candidate_refs),
                    )
                    if index > 0:
                        async with self.db.session() as db:
                            await self.events.emit(
                                db,
                                session_id,
                                "model.fallback",
                                {
                                    "turnId": turn_id,
                                    "from": primary,
                                    "to": model_ref,
                                    "attempt": index,
                                    "message": f"主模型失败，已切换到备用模型 {model_ref}",
                                },
                            )
                            await db.commit()
                    break
                except ModelError as exc:
                    if exc.code == "CONTEXT_TOO_LONG" and not compacted_this_turn:
                        compacted_this_turn = True
                        await self._force_compact(state, session_id)
                        outcome = None
                        break
                    if index + 1 < len(candidate_refs):
                        logger.warning(
                            "falling back to secondary model",
                            extra={
                                "session_id": session_id,
                                "from": model_ref,
                                "to": candidate_refs[index + 1],
                            },
                        )
                        continue
                    raise
            if outcome is None:
                if compacted_this_turn:
                    continue
                raise ModelError("EMPTY_RESPONSE", "模型没有产生结果", retryable=True)
            if outcome.finish_reason == "length":
                await self._append_system_message(
                    state,
                    session_id,
                    turn_id,
                    "模型输出达到最大 token 限制，回答可能被截断。请继续或增加 maxOutput 后重试。",
                )
                await self.events_publish_session(
                    state.session_id,
                    "model.truncated",
                    {"turnId": turn_id, "messageId": outcome.message_id},
                )
                break
            if not outcome.tool_calls:
                if not waited_once:
                    waited_once = True
                    pending_ids = await self.tasks.wait_pending(
                        session_id, self.config.tasks.wait_timeout_seconds
                    )
                    if pending_ids:
                        notified = await self._notify_terminal_tasks(state, session_id, turn_id)
                        if notified:
                            continue
                        await self._append_system_message(
                            state,
                            session_id,
                            turn_id,
                            "后台任务仍在运行，本轮先结束。任务完成后会追加通知并自动继续。",
                        )
                if await self._notify_terminal_tasks(state, session_id, turn_id):
                    continue
                break
            await self._execute_tool_calls(
                state,
                session,
                turn_id,
                outcome.tool_calls,
                failure_counts,
                denied_signatures,
            )
        await self._complete_turn(state, turn_id)

    # ------------------------------------------------------------- streaming

    async def _stream_model(
        self,
        state: RuntimeState,
        request: ModelRequest,
        assistant: Message,
        request_row_id: str,
        *,
        suppress_error_event: bool = False,
    ) -> StreamOutcome:
        started = time.monotonic()
        content = ""
        reasoning = ""
        retries = 0
        tool_calls: list[ToolCallRequest] = []
        usage = Usage()
        finish_reason = ""
        try:
            async for chunk in self.gateway.stream(request):
                if chunk.kind == "retry":
                    retries = chunk.attempt
                    content = ""
                    reasoning = ""
                    tool_calls = []
                    await self._reset_stream_message(state, assistant.id)
                    await self.events_publish_session(
                        state.session_id,
                        "model.retry",
                        {
                            "turnId": request.turn_id,
                            "attempt": chunk.attempt,
                            "maxRetries": 3,
                            "reason": chunk.reason,
                            "errorCode": chunk.error_code,
                        },
                    )
                elif chunk.kind == "content":
                    content += chunk.text
                    await self._record_delta(
                        assistant.id,
                        content,
                        reasoning,
                        "content.delta",
                        {"messageId": assistant.id, "turnId": request.turn_id, "text": chunk.text},
                    )
                elif chunk.kind == "reasoning":
                    reasoning += chunk.text
                    await self._record_delta(
                        assistant.id,
                        content,
                        reasoning,
                        "reasoning.delta",
                        {"messageId": assistant.id, "turnId": request.turn_id, "text": chunk.text},
                    )
                elif chunk.kind == "completed":
                    if chunk.content:
                        content = chunk.content
                    if chunk.reasoning:
                        reasoning = chunk.reasoning
                    tool_calls = chunk.tool_calls
                    usage = chunk.usage or Usage()
                    finish_reason = chunk.finish_reason
            tool_calls = await self._ensure_unique_call_ids(tool_calls)
            content_json = {
                "text": content,
                "reasoning": reasoning,
                "tool_calls": [
                    {"id": call.id, "name": call.name, "arguments": call.arguments} for call in tool_calls
                ],
            }
            raw: dict[str, Any] = {"role": "assistant", "content": content}
            if reasoning:
                raw["reasoning_content"] = reasoning
            if tool_calls:
                raw["tool_calls"] = [call.to_openai() for call in tool_calls]
            async with self.db.session() as db:
                row = await db.get(Message, assistant.id)
                if row is not None:
                    row.content_json = json.dumps(content_json, ensure_ascii=False, separators=(",", ":"))
                    row.raw_json = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
                    row.status = "complete"
            await self._save_usage_and_request(
                request,
                request_row_id,
                usage,
                retries,
                int((time.monotonic() - started) * 1000),
            )
            await self.events_publish_session(
                state.session_id,
                "message.updated",
                {"message": await self._message_payload(assistant.id)},
            )
            await self._emit_usage(state.session_id, request.turn_id)
            return StreamOutcome(
                content=content,
                reasoning=reasoning,
                tool_calls=tool_calls,
                usage=usage,
                retries=retries,
                message_id=assistant.id,
                finish_reason=finish_reason,
            )
        except ModelError as exc:
            await self._delete_message(assistant.id)
            await self.events_publish_session(
                state.session_id,
                "message.removed",
                {
                    "messageId": assistant.id,
                    "turnId": request.turn_id,
                    "reason": "模型响应失败，已丢弃不完整输出",
                },
            )
            await self._finalize_model_request(
                request, request_row_id, Usage(), retries, int((time.monotonic() - started) * 1000)
            )
            if not suppress_error_event:
                await self.events_publish_session(
                    state.session_id, "error", {"turnId": request.turn_id, **exc.to_payload()}
                )
            raise
        except asyncio.CancelledError:
            async with self.db.session() as db:
                row = await db.get(Message, assistant.id)
                if row is not None:
                    row.status = "interrupted"
                    row.content_json = json.dumps(
                        {"text": content, "reasoning": reasoning, "tool_calls": []},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
            await self._finalize_model_request(
                request, request_row_id, Usage(), retries, int((time.monotonic() - started) * 1000)
            )
            raise

    async def _record_delta(
        self,
        message_id: str,
        content: str,
        reasoning: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """Persist streamed content and its event atomically.

        A snapshot taken after a refresh must never contain content whose event id is
        still pending, otherwise a Last-Event-ID replay would duplicate a fragment.
        """

        async with self.db.session() as db:
            row = await db.get(Message, message_id)
            if row is None or row.status != "streaming":
                return
            row.content_json = json.dumps(
                {"text": content, "reasoning": reasoning, "tool_calls": []},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            await self.events.emit(db, row.session_id, event_type, payload)
            await db.commit()

    async def _reset_stream_message(self, state: RuntimeState, message_id: str) -> None:
        async with self.db.session() as db:
            row = await db.get(Message, message_id)
            if row is not None:
                row.content_json = json.dumps({"text": "", "reasoning": "", "tool_calls": []})
                await self.events.emit(db, state.session_id, "message.reset", {"messageId": message_id})
                await db.commit()

    async def _create_streaming_message(self, state: RuntimeState, session_id: str, turn_id: str) -> Message:
        async with state.seq_lock:
            async with self.db.session() as db:
                seq = await self._next_message_seq(db, session_id)
                row = Message(
                    session_id=session_id,
                    turn_id=turn_id,
                    seq=seq,
                    role="assistant",
                    content_json=json.dumps({"text": "", "reasoning": "", "tool_calls": []}),
                    raw_json="{}",
                    status="streaming",
                )
                db.add(row)
                await db.flush()
                await self.events.emit(db, session_id, "message.created", {"message": message_dto(row)})
                await db.commit()
                return row

    async def _ensure_unique_call_ids(self, calls: list[ToolCallRequest]) -> list[ToolCallRequest]:
        seen: set[str] = set()
        async with self.db.session() as db:
            for call in calls:
                if not call.id or call.id in seen:
                    call.id = new_id("call")
                if await db.get(ToolCall, call.id) is not None:
                    call.id = new_id("call")
                seen.add(call.id)
        return calls

    # ---------------------------------------------------------------- tools

    async def allowed_tools(self, session: Session) -> list[dict[str, Any]]:
        """Base tools + enabled MCP/plugin tools, filtered by the agent allow-list."""

        tools = list(self.registry.openai_tools())
        if self.mcp is not None:
            try:
                tools.extend(await self.mcp.tools_for_session(session.id))
            except Exception:  # noqa: BLE001
                logger.exception("failed to enumerate MCP tools", extra={"session_id": session.id})
        if self.plugins is not None:
            try:
                tools.extend(await self.plugins.tools())
            except Exception:  # noqa: BLE001
                logger.exception("failed to enumerate plugin tools")
        agent = await self._get_agent(session.agent_id)
        allowed = AgentService.allowed_tools(agent) if agent is not None else None
        cleaned: list[dict[str, Any]] = []
        for item in tools:
            function = item.get("function") or {}
            name = str(function.get("name") or "")
            if not name or (allowed is not None and name not in allowed):
                continue
            cleaned.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": function.get("description", ""),
                        "parameters": function.get("parameters")
                        or {"type": "object", "properties": {}, "additionalProperties": False},
                    },
                }
            )
        return sorted(cleaned, key=lambda item: item["function"]["name"])

    async def _tool_definition(self, session: Session, name: str) -> tuple[dict[str, Any] | None, str, str]:
        spec = self.registry.get(name)
        if spec is not None:
            return spec.input_schema, TOOL_CATEGORY.get(name, "shell"), "static"
        if name.startswith("mcp__") and self.mcp is not None:
            try:
                for item in await self.mcp.tools_for_session(session.id):
                    if item["function"]["name"] == name:
                        return item["function"]["parameters"], "mcp", "mcp"
            except Exception:  # noqa: BLE001
                return None, "mcp", "mcp"
            return None, "mcp", "mcp"
        if name.startswith("plugin__") and self.plugins is not None:
            for item in await self.plugins.tools():
                if item["function"]["name"] == name:
                    return item["function"]["parameters"], "plugin", "plugin"
            return None, "plugin", "plugin"
        return None, "shell", "unknown"

    @staticmethod
    def _validate_dynamic(name: str, args: dict[str, Any], schema: dict[str, Any]) -> ToolResult | None:
        validator = Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(args), key=lambda error: list(error.path))
        if not errors:
            return None
        details = []
        for error in errors[:8]:
            path = ".".join(str(part) for part in error.path) or "$"
            if error.validator == "additionalProperties":
                match = __import__("re").search(r"'([^']+)'", error.message)
                if match:
                    path = match.group(1)
            details.append({"path": path, "message": error.message})
        return ToolResult.failure(
            "INVALID_INPUT",
            f"工具 {name} 参数校验失败，请修正后重试；本次未执行",
            {"errors": details},
        )

    async def _execute_dynamic(
        self,
        session: Session,
        name: str,
        args: dict[str, Any],
        kind: str,
        workspace_root: Path,
    ) -> ToolResult:
        if kind == "mcp" and self.mcp is not None:
            try:
                payload = await self.mcp.call(session.id, name, args)
            except PermissionError as exc:
                return ToolResult.failure("PERMISSION_DENIED", str(exc))
            except McpError as exc:
                return ToolResult.failure("TOOL_ERROR", str(exc))
            result = payload.get("result") or {}
            if isinstance(result, dict) and result.get("isError"):
                return ToolResult.failure("TOOL_ERROR", "MCP 工具返回错误", {"result": result})
            content = result.get("content") if isinstance(result, dict) else result
            texts = []
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        texts.append(str(item.get("text") or ""))
            text = "\n".join(texts) if texts else json.dumps(result, ensure_ascii=False)
            return ToolResult.success(
                {
                    "content": text[: self.config.budget.max_tool_output_chars],
                    "structuredContent": result.get("structuredContent")
                    if isinstance(result, dict)
                    else None,
                    "untrusted": True,
                    "server": payload.get("server"),
                }
            )
        if kind == "plugin" and self.plugins is not None:
            return await self.plugins.execute_tool(name, args, workspace_root=workspace_root)
        return ToolResult.failure("INVALID_INPUT", "未知工具")

    async def _execute_tool_calls(
        self,
        state: RuntimeState,
        session: Session,
        turn_id: str,
        calls: list[ToolCallRequest],
        failure_counts: dict[str, int],
        denied_signatures: set[str],
    ) -> None:
        workspace = await self._get_workspace(session.workspace_id)
        if workspace is None:
            raise RuntimeError("workspace not found")
        agent = await self._get_agent(session.agent_id)
        plan_mode = session.mode == "plan" or bool(agent and agent.read_only)
        base_permissions = (
            AgentService.effective_permissions(agent, self.config.permissions)
            if agent is not None
            else self.config.permissions
        )
        evaluator = PermissionEvaluator(base_permissions)
        allowed_tool_names = AgentService.allowed_tools(agent) if agent is not None else None
        workspace_root = Path(workspace.root_path)
        if (
            self.config.tools.parallel_read_only
            and len(calls) > 1
            and await self._parallel_readonly_eligible(
                session,
                calls,
                evaluator,
                plan_mode=plan_mode,
                allowed_tool_names=allowed_tool_names,
                failure_counts=failure_counts,
                denied_signatures=denied_signatures,
            )
        ):
            await self._execute_parallel_readonly(
                state,
                session,
                turn_id,
                calls,
                workspace_root,
                evaluator,
            )
            return
        for call in calls:
            args, parse_error = parse_tool_arguments(call.arguments)
            signature = f"{call.name}:{json.dumps(args, ensure_ascii=False, sort_keys=True)}"
            tool = ToolCall(
                id=call.id,
                session_id=session.id,
                turn_id=turn_id,
                message_id=(await self._assistant_message_for_turn(turn_id)) or "",
                name=call.name,
                input_json=json.dumps(args, ensure_ascii=False, separators=(",", ":")),
                status="validating",
                risk="low",
            )
            async with self.db.session() as db:
                db.add(tool)
                await db.flush()
                await self.events.emit(
                    db,
                    session.id,
                    "tool.started",
                    {"toolCall": tool_call_dto(tool)},
                )
                await db.commit()

            result: ToolResult | None = None
            status = "validating"

            if parse_error:
                result = ToolResult.failure(
                    "INVALID_INPUT",
                    "工具参数不是合法 JSON，已拒绝执行；请重新生成完整的参数对象",
                    {"parseError": parse_error, "rawArguments": call.arguments[:500]},
                )
            elif signature in denied_signatures:
                result = ToolResult.failure(
                    "REPEATED_CALL",
                    "该操作刚刚被用户拒绝，禁止用相同参数重复调用；请换一种方法或先用 ask_question 询问用户。",
                )
            elif failure_counts.get(signature, 0) >= self.config.budget.max_repeat_failures:
                result = ToolResult.failure(
                    "REPEATED_CALL",
                    f"相同工具与参数已连续失败 {failure_counts[signature]} 次。请停止重试，改变方法或询问用户。",
                )
            else:
                schema, category, tool_kind = await self._tool_definition(session, call.name)
                if allowed_tool_names is not None and call.name not in allowed_tool_names:
                    result = ToolResult.failure(
                        "PERMISSION_DENIED",
                        f"当前 Agent 未允许使用工具 {call.name}；请更换 Agent 或调整工具白名单。",
                    )
                elif tool_kind == "unknown" or schema is None:
                    result = ToolResult.failure("INVALID_INPUT", f"未知工具 {call.name}")
                elif tool_kind == "static":
                    result = self.registry.validate(call.name, args)
                else:
                    result = self._validate_dynamic(call.name, args, schema)
                if result is None:
                    grants = await self._permission_grants(call.name)
                    decision = evaluator.evaluate(
                        tool=call.name,
                        resource=self._permission_resource(call.name, args),
                        plan_mode=plan_mode,
                        grants=grants,
                        category_override=category,
                    )
                    await self._update_tool(
                        tool.id,
                        status="awaiting_permission",
                        risk=decision.risk,
                        emit=True,
                    )
                    if decision.action == "deny":
                        result = self._denied_result(decision)
                        denied_signatures.add(signature)
                    elif decision.action == "ask":
                        answer = await self._request_interaction(
                            state,
                            session.id,
                            turn_id,
                            "permission",
                            {
                                "toolCallId": tool.id,
                                "tool": call.name,
                                "input": args,
                                "risk": decision.risk,
                                "category": decision.category,
                                "resource": decision.resource,
                                "reason": decision.reason,
                            },
                        )
                        action = str(answer.get("action") or "deny")
                        if action == "allow_always":
                            await self._grant_permission(call.name, decision.resource)
                            result = None
                        elif action == "allow_once":
                            result = None
                        else:
                            reason = str(answer.get("reason") or "").strip()
                            result = ToolResult.failure(
                                "PERMISSION_DENIED",
                                "用户拒绝了该操作。不要用相同参数重试；请换一种方法，或先询问用户。"
                                + (f" 拒绝原因：{reason}" if reason else ""),
                            )
                            denied_signatures.add(signature)
                    if result is None:
                        await self._update_tool(
                            tool.id,
                            status="running",
                            started_at=utcnow(),
                            emit=True,
                        )
                        if self.hooks is not None:
                            try:
                                extra = await self.plugins.hooks() if self.plugins is not None else []
                                await self.hooks.run(
                                    "tool.before",
                                    {"tool": call.name, "input": args, "toolCallId": tool.id},
                                    session=session,
                                    workspace_root=workspace_root,
                                    extra_hooks=extra,
                                )
                            except Exception:  # noqa: BLE001
                                logger.exception("tool.before hook failed")
                        ctx = ToolContext(
                            session_id=session.id,
                            turn_id=turn_id,
                            tool_call_id=tool.id,
                            config=self.config,
                            db=self.db,
                            events=self.events,
                            task_manager=self.tasks,
                            workspace_root=workspace_root,
                            cancel_event=state.cancel_event,
                            request_interaction=lambda kind, payload: self._request_interaction(
                                state, session.id, turn_id, kind, payload
                            ),
                            checkpoints=self.checkpoints,
                            uploads=self.uploads,
                            skills=self.skills,
                            gateway=self.gateway,
                            model_ref=session.model,
                            spawn_agent=lambda tool_id=tool.id, **kwargs: self.spawn_subagent(
                                session, tool_call_id=tool_id, **kwargs
                            ),
                            session=session,
                        )
                        if tool_kind == "static":
                            coroutine = self.registry.execute(call.name, args, ctx)
                        else:
                            coroutine = self._execute_dynamic(
                                session, call.name, args, tool_kind, workspace_root
                            )
                        execution = asyncio.create_task(
                            coroutine,
                            name=f"tool:{tool.id}",
                        )
                        state.tool_tasks.add(execution)
                        try:
                            result = await execution
                        except asyncio.CancelledError:
                            execution.cancel()
                            raise
                        except ToolCancelled:
                            raise
                        finally:
                            state.tool_tasks.discard(execution)
                        if self.hooks is not None:
                            try:
                                extra = await self.plugins.hooks() if self.plugins is not None else []
                                await self.hooks.run(
                                    "tool.after",
                                    {
                                        "tool": call.name,
                                        "input": args,
                                        "toolCallId": tool.id,
                                        "status": self._status_for_result(result),
                                        "result": result.to_payload(),
                                    },
                                    session=session,
                                    workspace_root=workspace_root,
                                    extra_hooks=extra,
                                )
                            except Exception:  # noqa: BLE001
                                logger.exception("tool.after hook failed")

            if result is None:
                result = ToolResult.failure("INTERNAL", "工具未产生结果")
            status = self._status_for_result(result)
            async with self.db.session() as db:
                row = await db.get(ToolCall, tool.id)
                if row is not None:
                    row.status = status
                    row.output_json = result.to_json()
                    row.error_code = result.error_code
                    row.ended_at = utcnow()
                    await db.flush()
                    await self.events.emit(
                        db,
                        session.id,
                        "tool.completed",
                        {"toolCall": tool_call_dto(row)},
                    )
                    await db.commit()
            if result.code != 0:
                failure_counts[signature] = failure_counts.get(signature, 0) + 1
            else:
                failure_counts.pop(signature, None)
            await self._append_tool_message(state, session.id, turn_id, call.id, result)

    async def _parallel_readonly_eligible(
        self,
        session: Session,
        calls: list[ToolCallRequest],
        evaluator: PermissionEvaluator,
        *,
        plan_mode: bool,
        allowed_tool_names: list[str] | None,
        failure_counts: dict[str, int],
        denied_signatures: set[str],
    ) -> bool:
        for call in calls:
            spec = self.registry.get(call.name)
            if spec is None or not spec.read_only:
                return False
            if allowed_tool_names is not None and call.name not in allowed_tool_names:
                return False
            args, error = parse_tool_arguments(call.arguments)
            if error:
                return False
            signature = f"{call.name}:{json.dumps(args, ensure_ascii=False, sort_keys=True)}"
            if signature in denied_signatures:
                return False
            if failure_counts.get(signature, 0) >= self.config.budget.max_repeat_failures:
                return False
            grants = await self._permission_grants(call.name)
            decision = evaluator.evaluate(
                tool=call.name,
                resource=self._permission_resource(call.name, args),
                plan_mode=plan_mode,
                grants=grants,
            )
            if decision.action != "allow":
                return False
        return True

    async def _execute_parallel_readonly(
        self,
        state: RuntimeState,
        session: Session,
        turn_id: str,
        calls: list[ToolCallRequest],
        workspace_root: Path,
        evaluator: PermissionEvaluator,
    ) -> None:
        assistant_id = (await self._assistant_message_for_turn(turn_id)) or ""
        rows: list[tuple[ToolCall, dict[str, Any], str]] = []
        async with self.db.session() as db:
            for call in calls:
                args, _ = parse_tool_arguments(call.arguments)
                grants = await self._permission_grants(call.name)
                decision = evaluator.evaluate(
                    tool=call.name,
                    resource=self._permission_resource(call.name, args),
                    plan_mode=session.mode == "plan",
                    grants=grants,
                )
                row = ToolCall(
                    id=call.id,
                    session_id=session.id,
                    turn_id=turn_id,
                    message_id=assistant_id,
                    name=call.name,
                    input_json=json.dumps(args, ensure_ascii=False, separators=(",", ":")),
                    status="running",
                    risk=decision.risk,
                    started_at=utcnow(),
                )
                db.add(row)
                rows.append((row, args, decision.category))
            await db.flush()
            for row, _args, _category in rows:
                await self.events.emit(db, session.id, "tool.started", {"toolCall": tool_call_dto(row)})
            await db.commit()

        async def run_one(index: int) -> ToolResult:
            row, args, _category = rows[index]
            ctx = ToolContext(
                session_id=session.id,
                turn_id=turn_id,
                tool_call_id=row.id,
                config=self.config,
                db=self.db,
                events=self.events,
                task_manager=self.tasks,
                workspace_root=workspace_root,
                cancel_event=state.cancel_event,
                request_interaction=lambda kind, payload: self._request_interaction(
                    state, session.id, turn_id, kind, payload
                ),
                checkpoints=self.checkpoints,
                uploads=self.uploads,
                skills=self.skills,
                gateway=self.gateway,
                model_ref=session.model,
                spawn_agent=lambda tool_id=row.id, **kwargs: self.spawn_subagent(
                    session, tool_call_id=tool_id, **kwargs
                ),
                session=session,
            )
            return await self.registry.execute(row.name, args, ctx)

        if self.hooks is not None:
            extra = await self.plugins.hooks() if self.plugins is not None else []
            for row, args, _category in rows:
                try:
                    await self.hooks.run(
                        "tool.before",
                        {"tool": row.name, "input": args, "toolCallId": row.id},
                        session=session,
                        workspace_root=workspace_root,
                        extra_hooks=extra,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("tool.before hook failed")
        tasks = [
            asyncio.create_task(run_one(index), name=f"tool:{rows[index][0].id}")
            for index in range(len(rows))
        ]
        for task in tasks:
            state.tool_tasks.add(task)
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            raise
        finally:
            for task in tasks:
                state.tool_tasks.discard(task)

        for (row, _args, _category), result in zip(rows, results, strict=True):
            if isinstance(result, BaseException):
                if isinstance(result, ToolCancelled):
                    raise result
                result = ToolResult.failure("TOOL_ERROR", f"工具执行异常：{type(result).__name__}")
            status = self._status_for_result(result)
            async with self.db.session() as db:
                stored = await db.get(ToolCall, row.id)
                if stored is not None:
                    stored.status = status
                    stored.output_json = result.to_json()
                    stored.error_code = result.error_code
                    stored.ended_at = utcnow()
                    await db.flush()
                    await self.events.emit(
                        db,
                        session.id,
                        "tool.completed",
                        {"toolCall": tool_call_dto(stored)},
                    )
                    await db.commit()
            await self._append_tool_message(state, session.id, turn_id, row.id, result)
            if self.hooks is not None:
                try:
                    extra = await self.plugins.hooks() if self.plugins is not None else []
                    await self.hooks.run(
                        "tool.after",
                        {
                            "tool": row.name,
                            "toolCallId": row.id,
                            "status": status,
                            "result": result.to_payload(),
                        },
                        session=session,
                        workspace_root=workspace_root,
                        extra_hooks=extra,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("tool.after hook failed")

    async def _update_tool(self, tool_id: str, *, emit: bool = False, **values: Any) -> None:
        async with self.db.session() as db:
            row = await db.get(ToolCall, tool_id)
            if row is None:
                return
            for key, value in values.items():
                setattr(row, key, value)
            await db.flush()
            if emit:
                await self.events.emit(
                    db,
                    row.session_id,
                    "tool.updated",
                    {"toolCall": tool_call_dto(row)},
                )
            await db.commit()

    @staticmethod
    def _denied_result(decision: PermissionDecision) -> ToolResult:
        return ToolResult.failure(
            "PERMISSION_DENIED",
            f"{decision.reason}。不要用相同参数重试；请换一种方法或询问用户。",
            {"category": decision.category, "resource": decision.resource, "risk": decision.risk},
        )

    @staticmethod
    def _status_for_result(result: ToolResult) -> str:
        if result.code == 0:
            return "succeeded"
        if result.error_code == "PERMISSION_DENIED":
            return "denied"
        if result.error_code == "REPEATED_CALL":
            return "denied"
        if result.error_code == "TIMEOUT":
            return "timeout"
        if result.error_code == "USER_PAUSED" or result.error_code == "CANCELLED":
            return "cancelled"
        return "failed"

    @staticmethod
    def _permission_resource(name: str, args: dict[str, Any]) -> str:
        if name in {"shell", "shell_background"}:
            return str(args.get("command", ""))
        if name in {
            "read_file",
            "list_dir",
            "glob",
            "grep",
            "write_file",
            "edit_file",
            "multi_edit",
        }:
            return str(args.get("path", ""))
        if name == "web_fetch":
            return str(args.get("url", ""))
        if name.startswith("mcp__"):
            return name
        if name.startswith("plugin__"):
            return name
        return json.dumps(args, ensure_ascii=False, sort_keys=True)[:500]

    async def _permission_grants(self, tool: str) -> list[tuple[str, str]]:
        async with self.db.session() as db:
            rows = (
                (
                    await db.execute(
                        select(PermissionGrant).where(
                            PermissionGrant.tool == tool,
                            PermissionGrant.scope.in_(["global", "session"]),
                        )
                    )
                )
                .scalars()
                .all()
            )
            return [(row.tool, row.pattern) for row in rows]

    async def _grant_permission(self, tool: str, resource: str) -> None:
        async with self.db.session() as db:
            db.add(PermissionGrant(tool=tool, pattern=resource or "*", scope="global"))
            await db.commit()
        await self.events_publish_global("permissions.updated", {"tool": tool, "pattern": resource})

    async def _request_interaction(
        self,
        state: RuntimeState,
        session_id: str,
        turn_id: str,
        kind: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        async with self.db.session() as db:
            interaction = PendingInteraction(
                session_id=session_id,
                turn_id=turn_id,
                type=kind,
                payload=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                status="pending",
            )
            db.add(interaction)
            session = await db.get(Session, session_id)
            if session is not None:
                session.status = "waiting_user"
                session.updated_at = utcnow()
            await db.flush()
            await self.events.emit(
                db,
                session_id,
                "interaction.required",
                {"interaction": interaction_dto(interaction)},
            )
            await self.events.emit(db, session_id, "session.status", {"status": "waiting_user"})
            await db.commit()
            interaction_id = interaction.id
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        state.interaction_futures[interaction_id] = future
        try:
            answer = await future
        except asyncio.CancelledError:
            await self._cancel_interaction(interaction_id, "会话暂停或取消，交互已中止")
            raise
        finally:
            state.interaction_futures.pop(interaction_id, None)
        async with self.db.session() as db:
            session = await db.get(Session, session_id)
            if session is not None and session.status == "waiting_user":
                session.status = "running"
                session.updated_at = utcnow()
            await self.events.emit(db, session_id, "session.status", {"status": "running"})
            await db.commit()
        return answer

    async def answer_interaction(
        self, session_id: str, interaction_id: str, answer: dict[str, Any]
    ) -> dict[str, Any]:
        state = self.state(session_id)
        async with self.db.session() as db:
            interaction = await db.get(PendingInteraction, interaction_id)
            if interaction is None or interaction.session_id != session_id:
                raise LookupError("交互不存在")
            if interaction.status != "pending":
                raise ValueError("该交互已经处理")
            payload = parse_json(interaction.payload)
            normalized = self._normalize_answer(interaction.type, payload, answer)
            interaction.status = "answered"
            interaction.answer_json = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
            interaction.resolved_at = utcnow()
            session = await db.get(Session, session_id)
            if session is not None and session.status == "waiting_user":
                session.status = "running"
                session.updated_at = utcnow()
            await db.flush()
            await self.events.emit(
                db,
                session_id,
                "interaction.resolved",
                {
                    "interaction": interaction_dto(interaction),
                    "answer": normalized,
                },
            )
            await self.events.emit(db, session_id, "session.status", {"status": "running"})
            await db.commit()
        future = state.interaction_futures.get(interaction_id)
        if future is not None and not future.done():
            future.set_result(normalized)
        return normalized

    @staticmethod
    def _normalize_answer(kind: str, payload: dict[str, Any], answer: dict[str, Any]) -> dict[str, Any]:
        if kind == "permission":
            action = str(answer.get("action") or "")
            if action not in {"allow_once", "allow_always", "deny"}:
                raise ValueError("权限操作必须是 allow_once / allow_always / deny")
            return {"action": action, "reason": str(answer.get("reason") or "")[:500]}
        if kind == "ask_question":
            if answer.get("skipped"):
                return {"skipped": True, "answers": {}}
            answers = answer.get("answers")
            if not isinstance(answers, dict):
                raise ValueError("answers 必须是对象")
            questions = payload.get("questions") or []
            normalized: dict[str, Any] = {}
            for question in questions:
                question_id = question.get("id")
                if question_id not in answers:
                    if question.get("allow_skip"):
                        normalized[question_id] = None
                        continue
                    raise ValueError(f"问题 {question_id} 尚未回答")
                value = answers[question_id]
                qtype = question.get("type")
                options = {str(option.get("value")) for option in question.get("options") or []}
                if qtype == "single" and value is not None and str(value) not in options:
                    raise ValueError(f"问题 {question_id} 的选项不合法")
                if qtype == "multi":
                    if not isinstance(value, list) or any(str(item) not in options for item in value):
                        raise ValueError(f"问题 {question_id} 的多选值不合法")
                if qtype == "confirm" and not isinstance(value, bool):
                    raise ValueError(f"问题 {question_id} 需要布尔值")
                normalized[str(question_id)] = value
            skipped = all(value is None for value in normalized.values())
            return {"skipped": skipped, "answers": normalized}
        if kind == "budget":
            return {"action": str(answer.get("action") or "continue")}
        raise ValueError(f"不支持的交互类型：{kind}")

    async def _cancel_interaction(self, interaction_id: str, reason: str) -> None:
        async with self.db.session() as db:
            interaction = await db.get(PendingInteraction, interaction_id)
            if interaction is None or interaction.status != "pending":
                return
            interaction.status = "cancelled"
            interaction.resolved_at = utcnow()
            interaction.answer_json = json.dumps({"cancelled": True, "reason": reason}, ensure_ascii=False)
            await db.flush()
            await self.events.emit(
                db,
                interaction.session_id,
                "interaction.resolved",
                {"interaction": interaction_dto(interaction)},
            )
            await db.commit()

    # ----------------------------------------------------------- stop/cancel

    async def pause(self, session_id: str, reason: str = "pause") -> None:
        state = self.state(session_id)
        state.stop_reason = reason
        state.cancel_event.set()
        task = state.turn_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=10)
            except (TimeoutError, asyncio.CancelledError):
                pass
            return
        async with self.db.session() as db:
            session = await db.get(Session, session_id)
            if session is not None:
                session.status = "paused" if reason != "cancel" else "idle"
                session.updated_at = utcnow()
            await self.events.emit(
                db, session_id, "session.status", {"status": "paused" if reason != "cancel" else "idle"}
            )
            await db.commit()

    async def cancel(self, session_id: str) -> None:
        self.state(session_id)
        await self.pause(session_id, reason="cancel")
        async with self.db.session() as db:
            session = await db.get(Session, session_id)
            if session is not None and session.status == "paused":
                session.status = "idle"
                session.updated_at = utcnow()
            await self.events.emit(db, session_id, "session.status", {"status": "idle"})
            await db.commit()

    async def resume(self, session_id: str) -> str:
        state = self.state(session_id)
        async with state.lock:
            if state.active:
                raise ValueError("会话正在运行")
            async with self.db.session() as db:
                session = await db.get(Session, session_id)
                if session is None:
                    raise LookupError("会话不存在")
                if session.status not in {"paused", "error"}:
                    raise ValueError(f"当前状态 {session.status} 不能恢复")
                await self._append_system_message_in_db(
                    db,
                    session_id,
                    None,
                    "用户已恢复会话。请从最后一个安全状态继续，不要重新执行已经完成的危险操作。",
                )
                session.status = "idle"
                session.last_error = None
                session.updated_at = utcnow()
                await db.commit()
            turn_id = await self._start_auto_turn_locked(state, session_id)
        return turn_id

    async def _handle_stop(self, state: RuntimeState, turn_id: str) -> None:
        reason = state.stop_reason or "pause"
        for task in list(state.tool_tasks):
            if not task.done():
                task.cancel()
        if state.tool_tasks:
            await asyncio.gather(*list(state.tool_tasks), return_exceptions=True)
        state.cancel_event.set()
        async with self.db.session() as db:
            turn = await db.get(Turn, turn_id)
            session = await db.get(Session, state.session_id)
            if turn is None or session is None:
                return
            if turn.status in {"completed", "failed", "cancelled", "paused"}:
                return
            await self._fill_missing_tool_results(
                db,
                state.session_id,
                turn_id,
                code="USER_PAUSED",
                message="用户已暂停或取消会话，工具执行被中止",
            )
            interactions = (
                (
                    await db.execute(
                        select(PendingInteraction).where(
                            PendingInteraction.turn_id == turn_id,
                            PendingInteraction.status == "pending",
                        )
                    )
                )
                .scalars()
                .all()
            )
            for interaction in interactions:
                interaction.status = "cancelled"
                interaction.resolved_at = utcnow()
                interaction.answer_json = json.dumps(
                    {"cancelled": True, "reason": "会话暂停或取消"}, ensure_ascii=False
                )
                await self.events.emit(
                    db,
                    state.session_id,
                    "interaction.resolved",
                    {"interaction": interaction_dto(interaction)},
                )
                future = state.interaction_futures.get(interaction.id)
                if future and not future.done():
                    future.cancel()
            streaming = (
                (
                    await db.execute(
                        select(Message).where(Message.turn_id == turn_id, Message.status == "streaming")
                    )
                )
                .scalars()
                .all()
            )
            for message in streaming:
                message.status = "interrupted"
            if reason == "cancel":
                turn.status = "cancelled"
                session.status = "idle"
                notice = "用户取消了本轮。已经生成的内容和工具结果均保留。"
                event_type = "turn.cancelled"
            else:
                turn.status = "paused"
                session.status = "paused"
                notice = "会话已暂停：模型流和工具均已中止，未完成的工具调用已补齐结果。"
                if reason == "shutdown":
                    notice = "服务正在关闭，本轮已安全中断并落库。重启后可从会话历史继续。"
                elif reason == "budget":
                    notice = "本轮达到预算上限，已暂停。请检查用量后决定是否继续。"
                event_type = "turn.paused"
            turn.ended_at = utcnow()
            session.updated_at = utcnow()
            await db.flush()
            await self._append_system_message_in_db(db, state.session_id, None, notice)
            await self.events.emit(db, state.session_id, event_type, {"turnId": turn_id, "reason": reason})
            await self.events.emit(db, state.session_id, "session.status", {"status": session.status})
            await db.commit()
        await self.events_publish_global("session.stopped", {"sessionId": state.session_id, "reason": reason})
        await self._run_turn_end_hook(state.session_id, turn_id, "paused", reason)

    async def _fill_missing_tool_results(
        self, db: AsyncSession, session_id: str, turn_id: str, *, code: str, message: str
    ) -> None:
        """Ensure every model-issued tool_call has exactly one tool result.

        The model may have emitted several calls while only the first had started. We
        reconstruct the full call list from the persisted assistant ``tool_calls`` so the
        next provider request remains protocol-valid after a pause/failure/restart.
        """

        assistant_messages = (
            (
                await db.execute(
                    select(Message)
                    .where(Message.turn_id == turn_id, Message.role == "assistant")
                    .order_by(Message.seq.asc())
                )
            )
            .scalars()
            .all()
        )
        specs: list[tuple[str, str, str, str]] = []
        for assistant in assistant_messages:
            raw = parse_json(assistant.raw_json)
            for item in raw.get("tool_calls") or []:
                function = item.get("function") or {}
                specs.append(
                    (
                        str(item.get("id") or new_id("call")),
                        str(function.get("name") or "unknown"),
                        str(function.get("arguments") or "{}"),
                        assistant.id,
                    )
                )
        rows = (
            (
                await db.execute(
                    select(ToolCall).where(ToolCall.turn_id == turn_id).order_by(ToolCall.started_at.asc())
                )
            )
            .scalars()
            .all()
        )
        by_id = {row.id: row for row in rows}
        existing = await self._tool_result_ids(db, session_id)
        error_value = 49901 if code == "USER_PAUSED" else (49903 if code == "SERVER_RESTARTED" else 49902)
        result_payload = {
            "code": error_value,
            "message": message,
            "data": {},
            "truncated": False,
            "artifacts": [],
            "errorCode": code,
        }
        ordered: list[ToolCall] = []
        for call_id, name, arguments, message_id in specs:
            call = by_id.get(call_id)
            if call is None:
                parsed, _ = parse_tool_arguments(arguments)
                call = ToolCall(
                    id=call_id,
                    session_id=session_id,
                    turn_id=turn_id,
                    message_id=message_id,
                    name=name,
                    input_json=json.dumps(parsed, ensure_ascii=False, separators=(",", ":")),
                    status="cancelled" if code in {"USER_PAUSED", "CANCELLED"} else "failed",
                    risk="medium",
                )
                db.add(call)
                by_id[call.id] = call
            if call.ended_at is None:
                call.status = "cancelled" if code in {"USER_PAUSED", "CANCELLED"} else "failed"
                call.error_code = code
                call.ended_at = utcnow()
                call.output_json = json.dumps(result_payload, ensure_ascii=False, separators=(",", ":"))
                await db.flush()
                await self.events.emit(db, session_id, "tool.completed", {"toolCall": tool_call_dto(call)})
            ordered.append(call)
        # Any row not present in an assistant message still needs a result.
        for row in rows:
            if row not in ordered:
                ordered.append(row)
        for call in ordered:
            if call.id in existing:
                continue
            payload = parse_json(call.output_json) if call.output_json else result_payload
            if not call.output_json:
                call.output_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            await self._append_tool_message_in_db(db, session_id, turn_id, call.id, payload)
            existing.add(call.id)

    async def _fail_turn(
        self,
        state: RuntimeState,
        turn_id: str,
        code: str,
        message: str,
        extra: dict[str, Any],
    ) -> None:
        async with self.db.session() as db:
            turn = await db.get(Turn, turn_id)
            session = await db.get(Session, state.session_id)
            if turn is None or session is None:
                return
            if turn.status in {"completed", "failed", "cancelled", "paused"}:
                return
            await self._fill_missing_tool_results(
                db, state.session_id, turn_id, code="CANCELLED", message="本轮失败，工具调用被中止"
            )
            streaming = (
                (
                    await db.execute(
                        select(Message).where(Message.turn_id == turn_id, Message.status == "streaming")
                    )
                )
                .scalars()
                .all()
            )
            for row in streaming:
                row.status = "interrupted"
            turn.status = "failed"
            turn.ended_at = utcnow()
            turn.error_code = code
            turn.error_message = message
            session.status = "error"
            session.last_error = message
            session.updated_at = utcnow()
            await db.flush()
            await self.events.emit(
                db,
                state.session_id,
                "turn.failed",
                {"turnId": turn_id, "code": code, "message": message, **extra},
            )
            await self.events.emit(
                db, state.session_id, "session.status", {"status": "error", "message": message}
            )
            await db.commit()
        await self._run_turn_end_hook(state.session_id, turn_id, "failed", message)

    async def _complete_turn(self, state: RuntimeState, turn_id: str) -> None:
        async with self.db.session() as db:
            turn = await db.get(Turn, turn_id)
            session = await db.get(Session, state.session_id)
            if turn is None or session is None:
                return
            if turn.status != "running":
                return
            turn.status = "completed"
            turn.ended_at = utcnow()
            session.status = "idle"
            session.updated_at = utcnow()
            await db.flush()
            await self.events.emit(
                db,
                state.session_id,
                "turn.completed",
                {"turnId": turn_id, "status": "completed", "endedAt": turn.ended_at},
            )
            await self.events.emit(db, state.session_id, "session.status", {"status": "idle"})
            await self.events.emit(
                db, state.session_id, "session.updated", {"session": self._session_payload(session)}
            )
            await db.commit()
        await self._emit_usage(state.session_id, turn_id)
        await self._run_turn_end_hook(state.session_id, turn_id, "completed", None)

    async def _run_turn_end_hook(self, session_id: str, turn_id: str, status: str, error: str | None) -> None:
        if self.hooks is None:
            return
        try:
            session = await self._get_session(session_id)
            if session is None:
                return
            workspace = await self._get_workspace(session.workspace_id)
            extra = await self.plugins.hooks() if self.plugins is not None else []
            await self.hooks.run(
                "turn.end",
                {"sessionId": session_id, "turnId": turn_id, "status": status, "error": error},
                session=session,
                workspace_root=Path(workspace.root_path) if workspace else None,
                extra_hooks=extra,
            )
        except Exception:  # noqa: BLE001
            logger.exception("turn.end hook failed", extra={"session_id": session_id})

    # --------------------------------------------------------- task dispatch

    async def on_task_complete(self, task_id: str, session_id: str, status: str, summary: str) -> None:
        state = self.state(session_id)
        if state.active or state.current_turn_id:
            state.wake.set()
            return
        async with state.lock:
            if state.active:
                state.wake.set()
                return
            async with self.db.session() as db:
                session = await db.get(Session, session_id)
                if session is None:
                    return
                claimed = await self._claim_terminal_tasks(db, session_id)
                if not claimed:
                    return
                await self._append_task_notice_in_db(db, session_id, claimed)
                await db.commit()
                auto = self.config.tasks.auto_continue_after_completion and session.status in {
                    "idle",
                    "error",
                }
            if auto:
                await self._start_auto_turn_locked(state, session_id)

    async def _post_turn(self, state: RuntimeState) -> None:
        """Close the turn -> task-notification -> queue hand-off race safely."""

        await asyncio.sleep(0)
        if state.shutdown or self._closing:
            return
        async with state.lock:
            if state.active:
                return
            async with state.seq_lock:
                async with self.db.session() as db:
                    session = await db.get(Session, state.session_id)
                    if session is None or session.status != "idle":
                        return
                    tasks = await self._claim_terminal_tasks(db, state.session_id)
                    if tasks:
                        await self._append_task_notice_in_db(db, state.session_id, tasks)
                        await db.commit()
                        if self.config.tasks.auto_continue_after_completion:
                            await self._start_auto_turn_locked(state, state.session_id)
                            return
        await self._dispatch_queue(state.session_id)

    async def _dispatch_queue(self, session_id: str) -> None:
        state = self.state(session_id)
        if state.shutdown or self._closing:
            return
        async with state.lock:
            if state.active:
                return
            async with self.db.session() as db:
                session = await db.get(Session, session_id)
                if session is None or session.status != "idle":
                    return
                item = (
                    (
                        await db.execute(
                            select(QueuedMessage)
                            .where(
                                QueuedMessage.session_id == session_id,
                                QueuedMessage.status == "pending",
                            )
                            .order_by(QueuedMessage.created_at.asc())
                            .limit(1)
                        )
                    )
                    .scalars()
                    .first()
                )
                if item is None:
                    return
                item.status = "consumed"
                attachment_ids = json.loads(item.attachments_json or "[]")
                attachments = await self._load_attachments(db, session_id, attachment_ids)
                turn_id = await self._create_turn_in_db(
                    db,
                    session,
                    user_content=item.content,
                    client_message_id=item.client_message_id,
                    attachments=attachments,
                )
                await self.events.emit(
                    db,
                    session_id,
                    "queue.updated",
                    {"queue": await self._queue_payload(db, session_id)},
                )
                await db.commit()
            await self._spawn_turn(state, turn_id)

    async def _start_auto_turn_locked(self, state: RuntimeState, session_id: str) -> str:
        """Create and spawn a turn; the caller must hold ``state.lock``."""

        if state.active:
            return ""
        async with self.db.session() as db:
            session = await db.get(Session, session_id)
            if session is None:
                raise LookupError("会话不存在")
            if session.status not in {"idle", "error", "paused"}:
                raise ValueError(f"当前状态 {session.status} 不能启动")
            turn_id = await self._create_turn_in_db(db, session, user_content=None)
            await db.commit()
        await self._spawn_turn(state, turn_id)
        return turn_id

    async def _notify_terminal_tasks(self, state: RuntimeState, session_id: str, turn_id: str | None) -> int:
        async with state.seq_lock:
            async with self.db.session() as db:
                claimed = await self._claim_terminal_tasks(db, session_id)
                if not claimed:
                    return 0
                await self._append_task_notice_in_db(db, session_id, claimed)
                await db.commit()
                return len(claimed)

    async def _claim_terminal_tasks(self, db: AsyncSession, session_id: str) -> list[Task]:
        rows = (
            (
                await db.execute(
                    select(Task).where(
                        Task.session_id == session_id,
                        Task.status.in_(["succeeded", "failed", "cancelled", "timeout"]),
                        Task.notified.is_(False),
                    )
                )
            )
            .scalars()
            .all()
        )
        for task in rows:
            task.notified = True
        return list(rows)

    async def _append_task_notice_in_db(self, db: AsyncSession, session_id: str, tasks: list[Task]) -> None:
        lines = ["[后台任务完成通知]"]
        for task in tasks:
            lines.append(
                f"- taskId={task.id} 状态={task.status}。摘要：{(task.summary or task.error or '无')[:1_500]}"
                f"（完整输出：{task.output_ref or '无'}）"
            )
        lines.append("请核对后台任务结果并做自检；如仍需等待其他任务，请调用 wait。")
        await self._append_system_message_in_db(db, session_id, None, "\n".join(lines))

    # --------------------------------------------------------------- helpers

    async def _inject_steer(self, state: RuntimeState, session_id: str, turn_id: str) -> int:
        async with state.seq_lock:
            async with self.db.session() as db:
                items = (
                    (
                        await db.execute(
                            select(QueuedMessage)
                            .where(
                                QueuedMessage.session_id == session_id,
                                QueuedMessage.mode == "steer",
                                QueuedMessage.status == "pending",
                            )
                            .order_by(QueuedMessage.created_at.asc())
                        )
                    )
                    .scalars()
                    .all()
                )
                if not items:
                    return 0
                session = await db.get(Session, session_id)
                for item in items:
                    item.status = "consumed"
                    attachment_ids = json.loads(item.attachments_json or "[]")
                    attachments = await self._load_attachments(db, session_id, attachment_ids)
                    text, raw_content, attachment_dto = self._build_user_content(
                        session, item.content, attachments
                    )
                    row = await self._add_message(
                        db,
                        session_id,
                        turn_id,
                        role="user",
                        text=text,
                        raw={"role": "user", "content": raw_content},
                        content_extra={"attachments": attachment_dto},
                    )
                    for upload in attachments:
                        upload.message_id = row.id
                        upload.session_id = session_id
                    await self.events.emit(db, session_id, "message.created", {"message": message_dto(row)})
                if session:
                    session.updated_at = utcnow()
                await self.events.emit(
                    db,
                    session_id,
                    "queue.updated",
                    {"queue": await self._queue_payload(db, session_id)},
                )
                await db.commit()
                return len(items)

    async def _notify_terminal_tasks_for_turn(
        self, state: RuntimeState, session_id: str, turn_id: str
    ) -> int:
        return await self._notify_terminal_tasks(state, session_id, turn_id)

    async def compact_now(self, session_id: str) -> Compaction:
        state = self.state(session_id)
        await self._force_compact(state, session_id)
        async with self.db.session() as db:
            session = await db.get(Session, session_id)
            if session is not None and not state.active:
                session.status = "idle"
                session.updated_at = utcnow()
                await db.commit()
        async with self.db.session() as db:
            row = (
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
            if row is None:
                raise RuntimeError("压缩未产生结果")
            return row

    async def _maybe_auto_compact(self, state: RuntimeState, session_id: str) -> bool:
        if not self.config.compaction.enabled:
            return False
        session = await self._get_session(session_id)
        if session is None:
            return False
        model = self._model_config(session)
        if model is None:
            return False
        async with self.db.session() as db:
            rows = (
                (
                    await db.execute(
                        select(Message)
                        .where(Message.session_id == session_id, Message.status == "complete")
                        .order_by(Message.seq.asc())
                    )
                )
                .scalars()
                .all()
            )
            tokens = estimate_messages_tokens([parse_json(row.raw_json) for row in rows]) + estimate_tokens(
                session.system_prompt
            )
            if not rows or tokens < model.context_window * self.config.compaction.trigger_ratio:
                return False
            latest = (
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
            if latest and rows[-1].seq <= latest.covered_until_seq + 2:
                return False
            await db.commit()
        await self._force_compact(state, session_id)
        return True

    async def _force_compact(self, state: RuntimeState, session_id: str) -> None:
        async with self.db.session() as db:
            session = await db.get(Session, session_id)
            if session is not None:
                session.status = "compacting"
                session.updated_at = utcnow()
            await self.events.emit(db, session_id, "session.status", {"status": "compacting"})
            await db.commit()
        try:
            summary_override = None
            if self.config.compaction.use_model:
                summary_override = await self._llm_compaction_summary(session_id)
            async with self.db.session() as db:
                await compact_session(
                    db,
                    self.events,
                    self.config,
                    session_id,
                    summary_override=summary_override,
                )
            if self.config.compaction.reload_project_rules:
                await self._reload_session_prompt(session_id)
        finally:
            async with self.db.session() as db:
                session = await db.get(Session, session_id)
                if session is not None and session.status == "compacting":
                    session.status = "running"
                    session.updated_at = utcnow()
                await self.events.emit(db, session_id, "session.status", {"status": "running"})
                await db.commit()

    async def _llm_compaction_summary(self, session_id: str) -> str | None:
        model_ref = self.config.compaction.model or self.config.defaults.compaction_model
        if not model_ref:
            return None
        try:
            self.config.provider_for(model_ref)
        except ResolutionError:
            return None
        try:
            async with self.db.session() as db:
                rows = (
                    (
                        await db.execute(
                            select(Message)
                            .where(Message.session_id == session_id)
                            .order_by(Message.seq.asc())
                        )
                    )
                    .scalars()
                    .all()
                )
            transcript = "\n".join(
                f"{row.role}: {str(parse_json(row.content_json).get('text') or '')[:1_200]}"
                for row in rows[-80:]
            )[:40_000]
            prompt = (
                "请把下面的 Agent 会话压缩成可继续工作的摘要。必须保留：当前目标、用户约束、"
                "已完成工作、关键决定、未完成事项、阻塞问题、相关文件、后台任务 taskId、待回答问题。"
                "只输出摘要正文，不要编造未出现的事实。\n\n" + transcript
            )
            request = ModelRequest(
                session_id=session_id,
                turn_id="compaction",
                step=1,
                model_ref=model_ref,
                messages=[{"role": "user", "content": prompt}],
                tools=[],
                reasoning_level="off",
                max_output=self.config.compaction.summary_max_tokens,
                prefix_text=prompt,
                prefix_hash=__import__("hashlib").sha256(prompt.encode()).hexdigest(),
            )
            parts: list[str] = []
            async for chunk in self.gateway.stream(request):
                if chunk.kind == "content":
                    parts.append(chunk.text)
                elif chunk.kind == "completed" and chunk.content:
                    parts = [chunk.content]
            return "".join(parts)[:20_000] or None
        except Exception:  # noqa: BLE001
            logger.exception("LLM compaction failed; falling back to deterministic summary")
            return None

    async def _reload_session_prompt(self, session_id: str) -> None:
        async with self.db.session() as db:
            session = await db.get(Session, session_id)
            if session is None:
                return
            workspace = await db.get(Workspace, session.workspace_id)
            agent = await db.get(Agent, session.agent_id)
            if workspace is None or agent is None:
                return
        skills = await self.skills.metadata_for_session(session) if self.skills else []
        mcp_servers = await self.mcp.metadata_for_session(session.id) if self.mcp else []
        prompt, env_info = build_system_prompt(
            config=self.config,
            agent=agent,
            workspace=workspace,
            created_at=session.created_at,
            skills=skills,
            mcp_servers=mcp_servers,
        )
        async with self.db.session() as db:
            row = await db.get(Session, session_id)
            if row is None:
                return
            row.system_prompt = prompt
            row.env_info = env_info
            row.updated_at = utcnow()
            await db.commit()

    async def _build_request(
        self, session: Session, turn_id: str
    ) -> tuple[list[dict[str, Any]], str, str, int, list[dict[str, Any]]]:
        async with self.db.session() as db:
            rows = (
                (
                    await db.execute(
                        select(Message)
                        .where(Message.session_id == session.id, Message.status == "complete")
                        .order_by(Message.seq.asc())
                    )
                )
                .scalars()
                .all()
            )
            latest_compaction = (
                (
                    await db.execute(
                        select(Compaction)
                        .where(Compaction.session_id == session.id)
                        .order_by(Compaction.created_at.desc())
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
        raw_messages = [
            (row.seq, row.turn_id, parse_json(row.raw_json)) for row in rows if parse_json(row.raw_json)
        ]
        messages = build_model_messages(
            system_prompt=session.system_prompt,
            compaction=latest_compaction,
            raw_messages=raw_messages,
            current_turn_id=turn_id,
        )
        tools = await self.allowed_tools(session)
        prefix_text, prefix_hash = messages_prefix_text(messages, tools)
        return messages, prefix_text, prefix_hash, estimate_tokens(prefix_text), tools

    async def _start_model_request(self, request: ModelRequest, step: int, prefix_tokens: int) -> str:
        async with self.db.session() as db:
            row = ModelRequestRow(
                session_id=request.session_id,
                turn_id=request.turn_id,
                step=step,
                model=request.model_ref,
                prefix_hash=request.prefix_hash,
                prefix_tokens=prefix_tokens,
            )
            db.add(row)
            await db.flush()
            await db.commit()
            return row.id

    async def _finalize_model_request(
        self,
        request: ModelRequest,
        row_id: str,
        usage: Usage,
        retries: int,
        duration_ms: int,
    ) -> None:
        async with self.db.session() as db:
            row = await db.get(ModelRequestRow, row_id)
            if row is not None:
                row.cache_hit_tokens = usage.cache_hit_tokens
                row.cache_miss_tokens = usage.cache_miss_tokens
                row.retries = retries
                row.duration_ms = duration_ms
                await db.commit()

    async def _save_usage_and_request(
        self,
        request: ModelRequest,
        row_id: str,
        usage: Usage,
        retries: int,
        duration_ms: int,
    ) -> None:
        session = await self._get_session(request.session_id)
        if session is None:
            return
        try:
            _, model = self.config.provider_for(request.model_ref)
        except ResolutionError:
            model = None
        cost = (
            calculate_cost(
                model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_hit_tokens=usage.cache_hit_tokens,
                cache_miss_tokens=usage.cache_miss_tokens,
            )
            if model
            else 0.0
        )
        async with self.db.session() as db:
            db.add(
                UsageRecord(
                    session_id=request.session_id,
                    turn_id=request.turn_id,
                    model=request.model_ref,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cache_hit_tokens=usage.cache_hit_tokens,
                    cache_miss_tokens=usage.cache_miss_tokens,
                    cost=cost,
                )
            )
            row = await db.get(ModelRequestRow, row_id)
            if row is not None:
                row.cache_hit_tokens = usage.cache_hit_tokens
                row.cache_miss_tokens = usage.cache_miss_tokens
                row.retries = retries
                row.duration_ms = duration_ms
            turn = await db.get(Turn, request.turn_id)
            if turn is not None:
                current = parse_json(turn.usage_json)
                current["inputTokens"] = int(current.get("inputTokens", 0)) + usage.input_tokens
                current["outputTokens"] = int(current.get("outputTokens", 0)) + usage.output_tokens
                current["cacheHitTokens"] = int(current.get("cacheHitTokens", 0)) + usage.cache_hit_tokens
                current["cacheMissTokens"] = int(current.get("cacheMissTokens", 0)) + usage.cache_miss_tokens
                current["cost"] = round(float(current.get("cost", 0)) + cost, 8)
                turn.usage_json = json.dumps(current, ensure_ascii=False, separators=(",", ":"))
            await db.commit()

    async def _emit_usage(self, session_id: str, turn_id: str | None) -> None:
        async with self.db.session() as db:
            rows = (
                (await db.execute(select(UsageRecord).where(UsageRecord.session_id == session_id)))
                .scalars()
                .all()
            )
            payload = {
                "inputTokens": sum(row.input_tokens for row in rows),
                "outputTokens": sum(row.output_tokens for row in rows),
                "cacheHitTokens": sum(row.cache_hit_tokens for row in rows),
                "cacheMissTokens": sum(row.cache_miss_tokens for row in rows),
                "cost": round(sum(row.cost for row in rows), 8),
            }
            await self.events.emit(db, session_id, "usage.updated", {"usage": payload, "turnId": turn_id})
            await db.commit()

    async def append_notice(self, session_id: str, text: str, turn_id: str | None = None) -> None:
        """Append a system notice at a protocol-safe point (used by settings changes)."""

        state = self.state(session_id)
        await self._append_system_message(state, session_id, turn_id, text)

    async def _append_system_message(
        self, state: RuntimeState, session_id: str, turn_id: str | None, text: str
    ) -> None:
        async with state.seq_lock:
            async with self.db.session() as db:
                await self._append_system_message_in_db(db, session_id, turn_id, text)
                await db.commit()

    async def _append_system_message_in_db(
        self, db: AsyncSession, session_id: str, turn_id: str | None, text: str
    ) -> Message:
        row = await self._add_message(
            db,
            session_id,
            turn_id,
            role="system",
            text=text,
            raw={"role": "system", "content": text},
        )
        await self.events.emit(db, session_id, "message.created", {"message": message_dto(row)})
        return row

    async def _append_tool_message(
        self, state: RuntimeState, session_id: str, turn_id: str, call_id: str, result: ToolResult
    ) -> None:
        async with state.seq_lock:
            async with self.db.session() as db:
                await self._append_tool_message_in_db(db, session_id, turn_id, call_id, result.to_payload())
                await db.commit()

    async def _append_tool_message_in_db(
        self,
        db: AsyncSession,
        session_id: str,
        turn_id: str | None,
        call_id: str,
        result: dict[str, Any],
    ) -> Message:
        content = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        row = await self._add_message(
            db,
            session_id,
            turn_id,
            role="tool",
            text=content,
            raw={"role": "tool", "tool_call_id": call_id, "content": content},
        )
        await self.events.emit(db, session_id, "message.created", {"message": message_dto(row)})
        return row

    async def _add_message(
        self,
        db: AsyncSession,
        session_id: str,
        turn_id: str | None,
        *,
        role: str,
        text: str,
        raw: dict[str, Any],
        client_message_id: str | None = None,
        content_extra: dict[str, Any] | None = None,
    ) -> Message:
        seq = await self._next_message_seq(db, session_id)
        row = Message(
            session_id=session_id,
            turn_id=turn_id,
            seq=seq,
            role=role,
            content_json=json.dumps(
                {"text": text, **(content_extra or {})},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            raw_json=json.dumps(raw, ensure_ascii=False, separators=(",", ":")),
            client_message_id=client_message_id,
        )
        db.add(row)
        await db.flush()
        return row

    @staticmethod
    async def _next_message_seq(db: AsyncSession, session_id: str) -> int:
        return (
            int(
                (
                    await db.execute(
                        select(func.coalesce(func.max(Message.seq), 0)).where(
                            Message.session_id == session_id
                        )
                    )
                ).scalar_one()
            )
            + 1
        )

    async def _tool_result_ids(self, db: AsyncSession, session_id: str) -> set[str]:
        rows = (
            (
                await db.execute(
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

    async def _assistant_message_for_turn(self, turn_id: str) -> str:
        async with self.db.session() as db:
            row = (
                (
                    await db.execute(
                        select(Message)
                        .where(Message.turn_id == turn_id, Message.role == "assistant")
                        .order_by(Message.seq.desc())
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            return row.id if row else ""

    async def _delete_message(self, message_id: str) -> None:
        async with self.db.session() as db:
            row = await db.get(Message, message_id)
            if row is not None:
                await db.delete(row)
                await db.commit()

    async def _message_payload(self, message_id: str) -> dict[str, Any] | None:
        async with self.db.session() as db:
            row = await db.get(Message, message_id)
            return message_dto(row) if row else None

    async def spawn_subagent(
        self,
        parent: Session,
        *,
        task: str,
        agent_id: str | None = None,
        background: bool = False,
        tool_call_id: str | None = None,
    ) -> dict[str, Any]:
        task = task.strip()
        if not task:
            raise ValueError("子 Agent 任务不能为空")
        depth = 0
        cursor = parent
        async with self.db.session() as db:
            while cursor.parent_session_id:
                depth += 1
                cursor = await db.get(Session, cursor.parent_session_id)
                if cursor is None:
                    break
            siblings = (
                (
                    await db.execute(
                        select(Session).where(
                            Session.parent_session_id == parent.id,
                            Session.status.in_(["running", "waiting_user", "compacting"]),
                        )
                    )
                )
                .scalars()
                .all()
            )
        if depth >= self.config.subagents.max_depth:
            raise PermissionError(f"子 Agent 嵌套深度超限（最大 {self.config.subagents.max_depth} 层）")
        if len(siblings) >= self.config.subagents.max_concurrent:
            raise PermissionError(f"子 Agent 并发数超限（最大 {self.config.subagents.max_concurrent}）")
        workspace = await self._get_workspace(parent.workspace_id)
        if workspace is None:
            raise LookupError("工作区不存在")
        # A sub-agent must never gain more authority than its parent. We therefore
        # always execute with the parent Agent's tool allow-list and merged
        # permissions; an optional requested Agent only contributes its prompt,
        # default model and read-only constraint.
        agent = await self._get_agent(parent.agent_id) or await self._get_agent("build")
        if agent is None:
            raise LookupError("没有可用 Agent")
        requested = await self._get_agent(agent_id) if agent_id else None
        role_agent = requested if requested is not None and requested.enabled else None
        model_ref = parent.model
        if role_agent is not None and role_agent.default_model:
            try:
                self.config.provider_for(role_agent.default_model)
                model_ref = role_agent.default_model
            except ResolutionError:
                model_ref = parent.model
        try:
            _, model = self.config.provider_for(model_ref)
            reasoning = (
                role_agent.default_reasoning_level
                if role_agent and role_agent.default_reasoning_level in model.reasoning_levels
                else (model.reasoning_levels[0] if model.reasoning_levels else "off")
            )
        except ResolutionError:
            raise
        created = utcnow()
        system_prompt, env_info = build_system_prompt(
            config=self.config,
            agent=agent,
            workspace=workspace,
            created_at=created,
        )
        if role_agent is not None and role_agent.id != agent.id and role_agent.system_prompt.strip():
            system_prompt += "\n\n## 子任务角色\n" + role_agent.system_prompt.strip()
        child = Session(
            workspace_id=parent.workspace_id,
            agent_id=agent.id,
            title=f"子任务：{task.replace(chr(10), ' ')[:36]}",
            model=model_ref,
            reasoning_level=reasoning,
            mode=(
                "plan"
                if parent.mode == "plan" or agent.read_only or bool(role_agent and role_agent.read_only)
                else "build"
            ),
            status="idle",
            system_prompt=system_prompt,
            env_info=env_info,
            parent_session_id=parent.id,
            created_at=created,
            updated_at=created,
        )
        async with self.db.session() as db:
            db.add(child)
            await db.flush()
            await self.events.emit(db, child.id, "session.created", {"session": self._session_payload(child)})
            await db.commit()
        if self.skills is not None:
            await self.skills.ensure_defaults(child, agent)
        if self.mcp is not None:
            for server_id in json.loads(agent.mcp_json or "[]"):
                try:
                    await self.mcp.set_session_enabled(child.id, str(server_id), True)
                except LookupError:
                    continue
        metadata_skills = await self.skills.metadata_for_session(child) if self.skills else []
        metadata_mcp = await self.mcp.metadata_for_session(child.id) if self.mcp else []
        if metadata_skills or metadata_mcp:
            refreshed, env_info = build_system_prompt(
                config=self.config,
                agent=agent,
                workspace=workspace,
                created_at=created,
                skills=metadata_skills,
                mcp_servers=metadata_mcp,
            )
            async with self.db.session() as db:
                row = await db.get(Session, child.id)
                if row:
                    row.system_prompt = refreshed
                    row.env_info = env_info
                    await db.commit()
            child.system_prompt = refreshed
        if self.hooks is not None:
            try:
                await self.hooks.run(
                    "session.start",
                    {"sessionId": child.id, "parentSessionId": parent.id, "task": task},
                    session=child,
                    workspace_root=Path(workspace.root_path),
                )
            except Exception:  # noqa: BLE001
                logger.exception("subagent session.start hook failed")
        await self.send_message(child.id, task, mode="auto")
        if background:
            tracked = await self.tasks.start_coroutine(
                session_id=parent.id,
                tool_call_id=tool_call_id,
                name=f"子 Agent：{task[:60]}",
                coroutine=self._collect_subagent_result(
                    child.id, timeout=self.config.subagents.background_timeout_seconds
                ),
            )
            return {
                "taskId": tracked.id,
                "childSessionId": child.id,
                "status": "running",
                "background": True,
            }
        return await self._collect_subagent_result(child.id, timeout=self.config.subagents.timeout_seconds)

    async def _collect_subagent_result(self, session_id: str, *, timeout: int) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout
        state = self.state(session_id)
        session = await self._get_session(session_id)
        while asyncio.get_running_loop().time() < deadline:
            session = await self._get_session(session_id)
            if session is None:
                raise LookupError("子 Agent 会话不存在")
            if not state.active and session.status in {"idle", "error", "paused", "waiting_user"}:
                break
            await asyncio.sleep(0.1)
        else:
            await self.pause(session_id, reason="pause")
            return {
                "childSessionId": session_id,
                "status": "timeout",
                "errorCode": "TIMEOUT",
                "message": f"子 Agent 在 {timeout} 秒内未完成，已暂停",
            }
        async with self.db.session() as db:
            messages = (
                (
                    await db.execute(
                        select(Message)
                        .where(
                            Message.session_id == session_id,
                            Message.role == "assistant",
                            Message.status == "complete",
                        )
                        .order_by(Message.seq.desc())
                        .limit(1)
                    )
                )
                .scalars()
                .all()
            )
        content = ""
        if messages:
            content = str(parse_json(messages[0].content_json).get("text") or "")[:12_000]
        status = session.status if session else "error"
        result = {
            "childSessionId": session_id,
            "status": status,
            "summary": content,
            "parentSessionId": (await self._get_session(session_id)).parent_session_id
            if await self._get_session(session_id)
            else None,
        }
        if status == "error":
            result["errorCode"] = "SUBAGENT_FAILED"
            result["message"] = (session.last_error if session else None) or "子 Agent 执行失败"
        elif status == "waiting_user":
            result["errorCode"] = "SUBAGENT_WAITING_USER"
            result["message"] = "子 Agent 正在等待用户回答或授权，请打开子会话处理"
        return result

    async def _get_session(self, session_id: str) -> Session | None:
        async with self.db.session() as db:
            return await db.get(Session, session_id)

    async def _get_workspace(self, workspace_id: str) -> Workspace | None:
        async with self.db.session() as db:
            return await db.get(Workspace, workspace_id)

    async def _get_agent(self, agent_id: str) -> Agent | None:
        async with self.db.session() as db:
            return await db.get(Agent, agent_id)

    async def _turn_elapsed(self, turn_id: str) -> float:
        turn = None
        async with self.db.session() as db:
            turn = await db.get(Turn, turn_id)
        if turn is None:
            return 0
        from datetime import datetime

        try:
            started = datetime.fromisoformat(turn.started_at.replace("Z", "+00:00"))
            now = datetime.now(started.tzinfo)
            return (now - started).total_seconds()
        except ValueError:
            return 0

    async def _turn_cost(self, turn_id: str) -> float:
        async with self.db.session() as db:
            rows = (
                (await db.execute(select(UsageRecord).where(UsageRecord.turn_id == turn_id))).scalars().all()
            )
            return sum(row.cost for row in rows)

    def _model_config(self, session: Session):
        try:
            _, model = self.config.provider_for(session.model)
            return model
        except ResolutionError:
            return None

    def _model_max_output(self, session: Session) -> int:
        model = self._model_config(session)
        return model.max_output if model else 4_096

    def _model_max_output_ref(self, model_ref: str) -> int:
        try:
            _, model = self.config.provider_for(model_ref)
            return model.max_output
        except ResolutionError:
            return 4_096

    async def _queue_payload(self, db: AsyncSession, session_id: str) -> list[dict[str, Any]]:
        rows = (
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
        return [self._queue_dto(row) for row in rows]

    @staticmethod
    def _queue_dto(item: QueuedMessage) -> dict[str, Any]:
        return {
            "id": item.id,
            "mode": item.mode,
            "content": item.content,
            "attachments": json.loads(item.attachments_json or "[]"),
            "status": item.status,
            "clientMessageId": item.client_message_id,
            "createdAt": item.created_at,
        }

    @staticmethod
    def _session_payload(session: Session) -> dict[str, Any]:
        return {
            "id": session.id,
            "workspaceId": session.workspace_id,
            "agentId": session.agent_id,
            "title": session.title,
            "model": session.model,
            "reasoningLevel": session.reasoning_level,
            "mode": session.mode,
            "status": session.status,
            "pinned": session.pinned,
            "archived": session.archived,
            "lastError": session.last_error,
            "createdAt": session.created_at,
            "updatedAt": session.updated_at,
        }

    async def events_publish_session(self, session_id: str, event_type: str, payload: dict[str, Any]) -> None:
        async with self.db.session() as db:
            await self.events.emit(db, session_id, event_type, payload)
            await db.commit()

    async def events_publish_global(self, event_type: str, payload: dict[str, Any]) -> None:
        session_id = payload.get("sessionId")
        if session_id:
            await self.events_publish_session(str(session_id), event_type, payload)

    async def shutdown(self, timeout: float = 8.0) -> None:
        self._closing = True
        states = list(self._states.values())
        for state in states:
            state.shutdown = True
            state.stop_reason = "shutdown"
            state.cancel_event.set()
        for state in states:
            if state.active:
                state.turn_task.cancel()  # type: ignore[union-attr]
        tasks = [state.turn_task for state in states if state.turn_task is not None]
        if tasks:
            await asyncio.wait(tasks, timeout=timeout)
        for state in states:
            for task in list(state.tool_tasks):
                task.cancel()
        await self.tasks.shutdown()


def task_or_none(state: RuntimeState, current: asyncio.Task[Any] | None) -> asyncio.Task[Any] | None:
    return state.turn_task if state.turn_task is current else None


def parse_tool_arguments(raw: str) -> tuple[dict[str, Any], str | None]:
    if not raw or not raw.strip():
        return {}, None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, f"{exc.msg} (line {exc.lineno})"
    if not isinstance(parsed, dict):
        return {}, "工具参数必须是 JSON 对象"
    return parsed, None
