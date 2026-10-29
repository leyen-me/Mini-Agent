"""Background task manager (``taskId`` lifecycle, cancellation and completion events)."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from sqlalchemy import select

from .config import AppConfig
from .db import Database
from .events import EventStore
from .models import Task, utcnow
from .security import redact_text

logger = logging.getLogger(__name__)

CompletionCallback = Callable[[str, str, str, str], Awaitable[None]]


class TaskManager:
    def __init__(
        self,
        db: Database,
        config: AppConfig,
        events: EventStore,
        on_complete: CompletionCallback | None = None,
    ) -> None:
        self.db = db
        self.config = config
        self.events = events
        self.on_complete = on_complete
        self._runners: dict[str, asyncio.Task[None]] = {}
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._waiters: dict[str, asyncio.Event] = {}
        self._cancel_requested: set[str] = set()

    async def start_shell(
        self,
        *,
        session_id: str,
        tool_call_id: str | None,
        command: str,
        description: str,
        cwd: Path,
        env: dict[str, str],
    ) -> Task:
        task = Task(
            session_id=session_id,
            tool_call_id=tool_call_id,
            name=description or "后台命令",
            status="queued",
            progress=0,
        )
        async with self.db.session() as db:
            db.add(task)
            await db.flush()
            await self.events.emit(
                db,
                session_id,
                "task.updated",
                {
                    "task": {
                        "id": task.id,
                        "name": task.name,
                        "status": task.status,
                        "progress": task.progress,
                    }
                },
            )
            await db.commit()
        waiter = asyncio.Event()
        self._waiters[task.id] = waiter
        runner = asyncio.create_task(
            self._run_shell(task.id, command, cwd, env),
            name=f"task:{task.id}",
        )
        self._runners[task.id] = runner
        runner.add_done_callback(lambda _task: self._waiters.pop(task.id, None))
        return task

    async def _run_shell(self, task_id: str, command: str, cwd: Path, env: dict[str, str]) -> None:
        await self._update(task_id, status="running", progress=5)
        output_dir = self.config.data_dir / "artifacts" / task_id
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "output.log"
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=str(cwd),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
            self._processes[task_id] = process
            output = bytearray()
            assert process.stdout is not None
            while True:
                chunk = await process.stdout.read(8192)
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > 5_000_000:
                    output.extend(b"\n[output capped at 5MB]\n")
                    await self._kill(process)
                    break
            returncode = await process.wait()
            text = output.decode("utf-8", errors="replace")
            text = redact_text(text, self.config.secret_values())
            output_path.write_text(text, encoding="utf-8")
            cancelled = task_id in self._cancel_requested
            status = "cancelled" if cancelled else ("succeeded" if returncode == 0 else "failed")
            summary = self._summarize(text, returncode, status)
            await self._update(
                task_id,
                status=status,
                progress=100,
                output_ref=str(output_path.relative_to(self.config.data_dir)),
                summary=summary,
                error=None if status == "succeeded" else f"退出码 {returncode}",
            )
            await self._notify(task_id, status, summary)
        except asyncio.CancelledError:
            if process and process.returncode is None:
                await self._kill(process)
            await self._update(task_id, status="cancelled", progress=100, error="任务被取消")
            await self._notify(task_id, "cancelled", "任务被取消")
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("background task failed", extra={"task_id": task_id})
            await self._update(task_id, status="failed", progress=100, error=redact_text(str(exc)))
            await self._notify(task_id, "failed", f"任务启动或执行失败：{exc}")
        finally:
            self._processes.pop(task_id, None)
            waiter = self._waiters.get(task_id)
            if waiter:
                waiter.set()

    @staticmethod
    def _summarize(text: str, returncode: int, status: str) -> str:
        head = text[:4_000]
        tail = text[-2_000:] if len(text) > 4_000 else ""
        parts = [f"状态：{status}，退出码：{returncode}", f"输出：\n{head}"]
        if tail:
            parts.append(f"\n……（截断，末尾如下）……\n{tail}")
        return "".join(parts)

    async def _update(self, task_id: str, **values: Any) -> None:
        async with self.db.session() as db:
            task = await db.get(Task, task_id)
            if task is None:
                return
            for key, value in values.items():
                setattr(task, key, value)
            task.updated_at = utcnow()
            await db.flush()
            await self.events.emit(
                db,
                task.session_id,
                "task.updated",
                {
                    "task": {
                        "id": task.id,
                        "name": task.name,
                        "status": task.status,
                        "progress": task.progress,
                        "summary": task.summary,
                        "error": task.error,
                    }
                },
            )
            await db.commit()

    async def _notify(self, task_id: str, status: str, summary: str) -> None:
        if not self.on_complete:
            return
        async with self.db.session() as db:
            task = await db.get(Task, task_id)
            if task is None:
                return
            session_id = task.session_id
        try:
            await self.on_complete(task_id, session_id, status, summary)
        except Exception:  # noqa: BLE001
            logger.exception("task completion callback failed", extra={"task_id": task_id})

    async def cancel(self, task_id: str) -> Task | None:
        async with self.db.session() as db:
            task = await db.get(Task, task_id)
        if task is None:
            return None
        if task.status in {"succeeded", "failed", "cancelled"}:
            return task
        self._cancel_requested.add(task_id)
        runner = self._runners.get(task_id)
        process = self._processes.get(task_id)
        if process and process.returncode is None:
            await self._kill(process)
        if runner and not runner.done():
            runner.cancel()
            try:
                await asyncio.wait_for(runner, timeout=5)
            except (TimeoutError, asyncio.CancelledError):
                pass
        self._cancel_requested.discard(task_id)
        async with self.db.session() as db:
            return await db.get(Task, task_id)

    @staticmethod
    async def _kill(process: asyncio.subprocess.Process) -> None:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            try:
                process.terminate()
            except ProcessLookupError:
                return
        try:
            await asyncio.wait_for(process.wait(), timeout=3)
        except TimeoutError:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                try:
                    process.kill()
                except ProcessLookupError:
                    pass

    async def wait(self, task_id: str, timeout: float) -> Task | None:
        waiter = self._waiters.get(task_id)
        if waiter and not waiter.is_set():
            try:
                await asyncio.wait_for(waiter.wait(), timeout=timeout)
            except TimeoutError:
                pass
        async with self.db.session() as db:
            return await db.get(Task, task_id)

    async def list_for_session(self, session_id: str, limit: int = 50) -> list[Task]:
        async with self.db.session() as db:
            rows = (
                (
                    await db.execute(
                        select(Task)
                        .where(Task.session_id == session_id)
                        .order_by(Task.created_at.desc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            return list(rows)

    async def wait_pending(self, session_id: str, timeout: float) -> list[str]:
        """Wait for all currently unfinished tasks, bounded by ``timeout`` seconds."""

        async with self.db.session() as db:
            rows = (
                (
                    await db.execute(
                        select(Task).where(
                            Task.session_id == session_id,
                            Task.status.in_(["queued", "running"]),
                        )
                    )
                )
                .scalars()
                .all()
            )
            task_ids = [task.id for task in rows]
        deadline = time.monotonic() + timeout
        for task_id in task_ids:
            remaining = max(0.01, deadline - time.monotonic())
            await self.wait(task_id, remaining)
        return task_ids

    async def shutdown(self) -> None:
        for task_id in list(self._runners):
            try:
                await self.cancel(task_id)
            except Exception:  # noqa: BLE001
                logger.exception("failed to cancel task during shutdown", extra={"task_id": task_id})
        for runner in list(self._runners.values()):
            if not runner.done():
                runner.cancel()
        self._runners.clear()
