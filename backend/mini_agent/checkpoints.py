"""File change checkpoints, unified diffs and turn-level undo/redo."""

from __future__ import annotations

import difflib
import hashlib
from pathlib import Path
from typing import Any

from sqlalchemy import select

from .config import AppConfig
from .db import Database
from .events import EventStore
from .models import Checkpoint, Message, Session
from .security import resolve_workspace_path


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def unified_diff(before: str, after: str, label: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{label}",
            tofile=f"b/{label}",
            n=3,
        )
    )[:200_000]


class CheckpointService:
    def __init__(self, db: Database, config: AppConfig, events: EventStore | None = None) -> None:
        self.db = db
        self.config = config
        self.events = events

    async def list_for_turn(self, session_id: str, turn_id: str) -> list[Checkpoint]:
        async with self.db.session() as db:
            return list(
                (
                    await db.execute(
                        select(Checkpoint)
                        .where(Checkpoint.session_id == session_id, Checkpoint.turn_id == turn_id)
                        .order_by(Checkpoint.sequence.asc())
                    )
                )
                .scalars()
                .all()
            )

    async def list_for_session(self, session_id: str, limit: int = 100) -> list[Checkpoint]:
        async with self.db.session() as db:
            return list(
                (
                    await db.execute(
                        select(Checkpoint)
                        .where(Checkpoint.session_id == session_id)
                        .order_by(Checkpoint.created_at.desc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )

    async def undo_turn(self, session_id: str, turn_id: str) -> dict[str, Any]:
        rows = await self.list_for_turn(session_id, turn_id)
        applied = [row for row in rows if row.applied]
        if not applied:
            raise ValueError("该 Turn 没有可撤销的文件修改")
        workspace = await self._workspace_root(session_id)
        restored = []
        for row in reversed(applied):
            target = resolve_workspace_path(workspace, row.path)
            if target.exists() and row.after_sha256:
                current = sha256_bytes(target.read_bytes())
                if current != row.after_sha256:
                    raise ValueError(
                        f"文件 {row.path} 在 Turn 结束后被外部修改，已拒绝撤销以避免覆盖用户改动"
                    )
            and_before = await self._read_backup(row.backup_path)
            if row.existed:
                if and_before is None:
                    raise ValueError(f"检查点缺少原始内容：{row.path}")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(and_before)
            else:
                target.unlink(missing_ok=True)
            row.applied = False
            restored.append(row.path)
        async with self.db.session() as db:
            for row in applied:
                db_row = await db.get(Checkpoint, row.id)
                if db_row:
                    db_row.applied = False
            await db.commit()
        await self._notice(session_id, f"已撤销 {len(restored)} 个文件修改：{', '.join(restored)}")
        return {"restored": restored, "direction": "undo"}

    async def redo_turn(self, session_id: str, turn_id: str) -> dict[str, Any]:
        rows = await self.list_for_turn(session_id, turn_id)
        reverted = [row for row in rows if not row.applied and row.after_path]
        if not reverted:
            raise ValueError("该 Turn 没有可重做的文件修改")
        workspace = await self._workspace_root(session_id)
        restored = []
        for row in reverted:
            target = resolve_workspace_path(workspace, row.path)
            if target.exists() and row.before_sha256:
                current = sha256_bytes(target.read_bytes())
                if current != row.before_sha256:
                    raise ValueError(f"文件 {row.path} 在撤销后被外部修改，已拒绝重做以避免覆盖用户改动")
            after = await self._read_backup(row.after_path)
            if after is None:
                raise ValueError(f"检查点缺少修改后内容：{row.path}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(after)
            restored.append(row.path)
        async with self.db.session() as db:
            for row in reverted:
                db_row = await db.get(Checkpoint, row.id)
                if db_row:
                    db_row.applied = True
            await db.commit()
        await self._notice(session_id, f"已重做 {len(restored)} 个文件修改：{', '.join(restored)}")
        return {"restored": restored, "direction": "redo"}

    async def diff(self, checkpoint_id: str) -> dict[str, Any]:
        async with self.db.session() as db:
            row = await db.get(Checkpoint, checkpoint_id)
            if row is None:
                raise LookupError("检查点不存在")
        return {
            "id": row.id,
            "path": row.path,
            "turnId": row.turn_id,
            "operation": row.operation,
            "applied": row.applied,
            "diff": row.diff,
            "beforeSha256": row.before_sha256,
            "afterSha256": row.after_sha256,
        }

    async def _workspace_root(self, session_id: str) -> Path:
        from .models import Workspace

        async with self.db.session() as db:
            session = await db.get(Session, session_id)
            if session is None:
                raise LookupError("会话不存在")
            workspace = await db.get(Workspace, session.workspace_id)
            if workspace is None:
                raise LookupError("工作区不存在")
            return Path(workspace.root_path)

    async def _read_backup(self, relative: str | None) -> bytes | None:
        if not relative:
            return None
        target = (self.config.data_dir / relative).resolve()
        if not target.is_relative_to(self.config.data_dir.resolve()) or not target.exists():
            return None
        return target.read_bytes()

    async def _notice(self, session_id: str, text: str) -> None:
        if self.events is None:
            return
        async with self.db.session() as db:
            last = (
                (
                    await db.execute(
                        select(Message)
                        .where(Message.session_id == session_id)
                        .order_by(Message.seq.desc())
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            from .models import Message as MessageModel

            row = MessageModel(
                session_id=session_id,
                turn_id=None,
                seq=(last.seq if last else 0) + 1,
                role="system",
                content_json='{"text":' + __import__("json").dumps(text, ensure_ascii=False) + "}",
                raw_json=__import__("json").dumps({"role": "system", "content": text}, ensure_ascii=False),
            )
            db.add(row)
            await db.flush()
            await self.events.emit(
                db,
                session_id,
                "message.created",
                {
                    "message": {
                        "id": row.id,
                        "sessionId": session_id,
                        "turnId": None,
                        "seq": row.seq,
                        "role": "system",
                        "content": {"text": text, "reasoning": "", "toolCalls": []},
                        "status": "complete",
                        "clientMessageId": None,
                        "createdAt": row.created_at,
                    }
                },
            )
            await db.commit()

    async def record(
        self,
        *,
        session_id: str,
        turn_id: str,
        path: Path,
        workspace_root: Path,
        operation: str,
        before: bytes | None,
        after: bytes,
    ) -> Checkpoint:
        relative_path = str(path.resolve().relative_to(workspace_root.resolve()))
        existing = await self._existing(session_id, turn_id, relative_path)
        if existing is not None:
            # Preserve the first before-state; update the after-state and diff.
            if existing.after_path:
                (self.config.data_dir / existing.after_path).unlink(missing_ok=True)
            after_path = self._write_backup(session_id, turn_id, path, after, suffix="after")
            existing.after_path = after_path
            existing.after_sha256 = sha256_bytes(after)
            existing.diff = unified_diff(
                (await self._read_backup(existing.backup_path) or b"").decode("utf-8", errors="replace"),
                after.decode("utf-8", errors="replace"),
                relative_path,
            )
            async with self.db.session() as db:
                db_row = await db.get(Checkpoint, existing.id)
                if db_row is not None:
                    db_row.after_path = after_path
                    db_row.after_sha256 = existing.after_sha256
                    db_row.diff = existing.diff
                    db_row.applied = True
                    await db.commit()
                    return db_row
            return existing
        before_path = None
        existed = before is not None
        if before is not None:
            before_path = self._write_backup(session_id, turn_id, path, before, suffix="before")
        after_path = self._write_backup(session_id, turn_id, path, after, suffix="after")
        row = Checkpoint(
            session_id=session_id,
            turn_id=turn_id,
            path=relative_path,
            existed=existed,
            backup_path=before_path,
            after_path=after_path,
            before_sha256=sha256_bytes(before) if before is not None else None,
            after_sha256=sha256_bytes(after),
            operation=operation,
            diff=unified_diff(
                before.decode("utf-8", errors="replace") if before is not None else "",
                after.decode("utf-8", errors="replace"),
                relative_path,
            ),
            applied=True,
            sequence=await self._next_sequence(session_id, turn_id),
        )
        async with self.db.session() as db:
            db.add(row)
            await db.commit()
            return row

    async def _existing(self, session_id: str, turn_id: str, path: str) -> Checkpoint | None:
        async with self.db.session() as db:
            return (
                (
                    await db.execute(
                        select(Checkpoint)
                        .where(
                            Checkpoint.session_id == session_id,
                            Checkpoint.turn_id == turn_id,
                            Checkpoint.path == path,
                        )
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )

    async def _next_sequence(self, session_id: str, turn_id: str) -> int:
        async with self.db.session() as db:
            rows = (
                (
                    await db.execute(
                        select(Checkpoint).where(
                            Checkpoint.session_id == session_id, Checkpoint.turn_id == turn_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            return len(rows)

    def _write_backup(self, session_id: str, turn_id: str, path: Path, data: bytes, *, suffix: str) -> str:
        directory = self.config.data_dir / "checkpoints" / session_id / turn_id
        directory.mkdir(parents=True, exist_ok=True)
        safe_name = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in path.name)[:80]
        target = directory / f"{suffix}-{sha256_bytes(data)[:12]}-{safe_name}"
        target.write_bytes(data)
        return str(target.relative_to(self.config.data_dir))
