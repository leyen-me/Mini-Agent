"""Session runtime: one writer per session, durable turns, tools and interactions."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .compaction import build_model_messages, compact_session
from .config import AppConfig, ResolutionError
from .db import Database
from .events import EventStore
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
    UsageRecord,
    Workspace,
    new_id,
    utcnow,
)
from .models import (
    ModelRequest as ModelRequestRow,
)
from .security import PermissionDecision, PermissionEvaluator, redact_text
from .serialization import (
    interaction_dto,
    message_dto,
    parse_json,
    tool_call_dto,
)
from .tasks import TaskManager
from .tokens import calculate_cost, estimate_messages_tokens, estimate_tokens
from .tools import (
    ToolCancelled,
    ToolContext,
    ToolRegistry,
    ToolResult,
)

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
    ) -> None:
        self.db = db
        self.config = config
        self.events = events
        self.gateway = gateway
        self.registry = registry
        self.tasks = tasks
        self.permissions = PermissionEvaluator(config.permissions)
        self._states: dict[str, RuntimeState] = {}
        self._closing = False

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
    ) -> dict[str, Any]:
        content = content.strip()
        if not content:
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
                active = state.active or session.status in {"running", "waiting_user", "compacting"}
                if active:
                    delivery = "steer" if mode == "steer" else "queue"
                    item = QueuedMessage(
                        session_id=session_id,
                        mode=delivery,
                        content=content,
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
                if session.status == "waiting_user":
                    raise PermissionError("会话正在等待用户回答，请先提交回答")
                if state.turn_task is not None and state.turn_task.done():
                    state.turn_task = None
                turn_id = await self._create_turn_in_db(
                    db, session, user_content=content, client_message_id=client_message_id
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
    ) -> str:
        message_row: Message | None = None
        if user_content is not None:
            message_row = await self._add_message(
                db,
                session.id,
                None,
                role="user",
                text=user_content,
                raw={"role": "user", "content": user_content},
                client_message_id=client_message_id,
            )
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
        if session.title in {"", "新会话"} and user_content:
            session.title = user_content.replace("\n", " ")[:40]
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
            messages, prefix_text, prefix_hash, prefix_tokens = await self._build_request(session, turn_id)
            tools = self.registry.openai_tools()
            request = ModelRequest(
                session_id=session_id,
                turn_id=turn_id,
                step=step + 1,
                model_ref=session.model,
                messages=messages,
                tools=tools,
                reasoning_level=session.reasoning_level,
                max_output=self._model_max_output(session),
                prefix_text=prefix_text,
                prefix_hash=prefix_hash,
            )
            request_row_id = await self._start_model_request(request, step + 1, prefix_tokens)
            assistant = await self._create_streaming_message(state, session_id, turn_id)
            step += 1
            try:
                outcome = await self._stream_model(state, request, assistant, request_row_id)
            except ModelError as exc:
                if exc.code == "CONTEXT_TOO_LONG" and not compacted_this_turn:
                    compacted_this_turn = True
                    await self._force_compact(state, session_id)
                    continue
                raise
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
    ) -> StreamOutcome:
        started = time.monotonic()
        content = ""
        reasoning = ""
        retries = 0
        tool_calls: list[ToolCallRequest] = []
        usage = Usage()
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
                result = self.registry.validate(call.name, args)
                if result is None:
                    grants = await self._permission_grants(call.name)
                    decision = self.permissions.evaluate(
                        tool=call.name,
                        resource=self._permission_resource(call.name, args),
                        plan_mode=plan_mode,
                        grants=grants,
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
                        ctx = ToolContext(
                            session_id=session.id,
                            turn_id=turn_id,
                            tool_call_id=tool.id,
                            config=self.config,
                            db=self.db,
                            events=self.events,
                            task_manager=self.tasks,
                            workspace_root=Path(workspace.root_path),
                            cancel_event=state.cancel_event,
                            request_interaction=lambda kind, payload: self._request_interaction(
                                state, session.id, turn_id, kind, payload
                            ),
                        )
                        execution = asyncio.create_task(
                            self.registry.execute(call.name, args, ctx),
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
                if session.status not in {"paused", "error", "idle"}:
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
                turn_id = await self._create_turn_in_db(
                    db, session, user_content=item.content, client_message_id=item.client_message_id
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
                        Task.status.in_(["succeeded", "failed", "cancelled"]),
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
                    row = await self._add_message(
                        db,
                        session_id,
                        turn_id,
                        role="user",
                        text=item.content,
                        raw={"role": "user", "content": item.content},
                    )
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
            async with self.db.session() as db:
                await compact_session(db, self.events, self.config, session_id)
        finally:
            async with self.db.session() as db:
                session = await db.get(Session, session_id)
                if session is not None and session.status == "compacting":
                    session.status = "running"
                    session.updated_at = utcnow()
                await self.events.emit(db, session_id, "session.status", {"status": "running"})
                await db.commit()

    async def _build_request(
        self, session: Session, turn_id: str
    ) -> tuple[list[dict[str, Any]], str, str, int]:
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
        prefix_text, prefix_hash = messages_prefix_text(messages, self.registry.openai_tools())
        return messages, prefix_text, prefix_hash, estimate_tokens(prefix_text)

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
            _, model = self.config.provider_for(session.model)
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
                    model=session.model,
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
    ) -> Message:
        seq = await self._next_message_seq(db, session_id)
        row = Message(
            session_id=session_id,
            turn_id=turn_id,
            seq=seq,
            role=role,
            content_json=json.dumps({"text": text}, ensure_ascii=False, separators=(",", ":")),
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
