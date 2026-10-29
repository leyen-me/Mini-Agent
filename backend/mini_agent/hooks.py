"""Lifecycle hooks executed as isolated subprocesses."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select

from .config import AppConfig
from .db import Database
from .models import Hook, Session, utcnow
from .security import redact_text

logger = logging.getLogger(__name__)
HOOK_EVENTS = {"session.start", "turn.end", "tool.before", "tool.after", "compaction.after"}


def hook_dto(row: Hook) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "event": row.event,
        "command": row.command,
        "enabled": row.enabled,
        "timeoutSeconds": row.timeout_seconds,
        "scope": row.scope,
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
    }


class HookService:
    def __init__(self, db: Database, config: AppConfig) -> None:
        self.db = db
        self.config = config

    async def list(self) -> list[Hook]:
        async with self.db.session() as db:
            return list((await db.execute(select(Hook).order_by(Hook.created_at.asc()))).scalars().all())

    async def create(self, payload: dict[str, Any]) -> Hook:
        event = str(payload.get("event") or "")
        if event not in HOOK_EVENTS:
            raise ValueError(f"event 必须是 {', '.join(sorted(HOOK_EVENTS))}")
        command = str(payload.get("command") or "").strip()
        if not command:
            raise ValueError("hook 命令不能为空")
        async with self.db.session() as db:
            row = Hook(
                name=str(payload.get("name") or event),
                event=event,
                command=command,
                enabled=bool(payload.get("enabled", True)),
                timeout_seconds=int(payload.get("timeoutSeconds") or payload.get("timeout_seconds") or 30),
                scope=str(payload.get("scope") or "system"),
            )
            db.add(row)
            await db.commit()
            return row

    async def update(self, hook_id: str, payload: dict[str, Any]) -> Hook:
        async with self.db.session() as db:
            row = await db.get(Hook, hook_id)
            if row is None:
                raise LookupError("Hook 不存在")
            mapping = {
                "name": "name",
                "event": "event",
                "command": "command",
                "enabled": "enabled",
                "timeoutSeconds": "timeout_seconds",
                "timeout_seconds": "timeout_seconds",
                "scope": "scope",
            }
            for key, attribute in mapping.items():
                if key in payload:
                    setattr(row, attribute, payload[key])
            if row.event not in HOOK_EVENTS:
                raise ValueError("非法 hook event")
            row.updated_at = utcnow()
            await db.commit()
            return row

    async def delete(self, hook_id: str) -> None:
        async with self.db.session() as db:
            row = await db.get(Hook, hook_id)
            if row is None:
                raise LookupError("Hook 不存在")
            await db.delete(row)
            await db.commit()

    async def run(
        self,
        event: str,
        payload: dict[str, Any],
        *,
        session: Session | None = None,
        workspace_root: Path | None = None,
        extra_hooks: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        if not self.config.hooks.enabled:
            return []
        async with self.db.session() as db:
            rows = (
                (await db.execute(select(Hook).where(Hook.event == event, Hook.enabled.is_(True))))
                .scalars()
                .all()
            )
        entries: list[Any] = list(rows)
        for item in extra_hooks or []:
            if item.get("event") == event and item.get("command"):
                entries.append(
                    SimpleNamespace(
                        id=item.get("id"),
                        name=item.get("name") or item.get("id"),
                        event=event,
                        command=item.get("command"),
                        timeout_seconds=int(item.get("timeout") or 30),
                    )
                )
        results = []
        environment = _safe_env()
        for row in entries:
            started = time.monotonic()
            data = {
                "event": event,
                "hook": row.name,
                "sessionId": session.id if session else None,
                "workspaceRoot": str(workspace_root) if workspace_root else None,
                "payload": payload,
            }
            process = None
            hook_env = {
                **environment,
                "MINI_AGENT_EVENT": event,
                "MINI_AGENT_HOOK": str(row.name),
                "MINI_AGENT_SESSION_ID": str(session.id) if session else "",
                "MINI_AGENT_WORKSPACE": str(workspace_root) if workspace_root else "",
            }
            try:
                process = await asyncio.create_subprocess_shell(
                    row.command,
                    cwd=str(workspace_root) if workspace_root else None,
                    env=hook_env,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=True,
                )
                try:
                    stdout, stderr = await asyncio.wait_for(
                        process.communicate(json.dumps(data, ensure_ascii=False).encode("utf-8")),
                        timeout=max(1, row.timeout_seconds),
                    )
                except TimeoutError:
                    await _kill(process)
                    stdout, stderr = b"", b"hook timed out"
                output = redact_text(
                    (stdout + stderr).decode("utf-8", errors="replace")[:20_000],
                    self.config.secret_values(),
                )
                results.append(
                    {
                        "hookId": row.id,
                        "name": row.name,
                        "event": event,
                        "ok": process.returncode == 0,
                        "exitCode": process.returncode,
                        "durationMs": int((time.monotonic() - started) * 1000),
                        "output": output,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("hook failed", extra={"hook_id": row.id})
                results.append(
                    {
                        "hookId": row.id,
                        "name": row.name,
                        "event": event,
                        "ok": False,
                        "exitCode": None,
                        "durationMs": int((time.monotonic() - started) * 1000),
                        "output": redact_text(str(exc), self.config.secret_values()),
                    }
                )
        return results


def _safe_env() -> dict[str, str]:
    allow = {"PATH", "HOME", "LANG", "LC_ALL", "TERM", "TMPDIR", "USER", "SHELL", "PWD", "TZ"}
    env = {key: value for key, value in os.environ.items() if key in allow}
    return env


async def _kill(process: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        try:
            process.terminate()
        except ProcessLookupError:
            return
    try:
        await asyncio.wait_for(process.wait(), timeout=2)
    except TimeoutError:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
