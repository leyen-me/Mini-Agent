"""Backups, restore staging, retention cleanup, log export and Prometheus metrics."""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import time
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select

from .config import AppConfig
from .db import Database
from .models import Backup, Event, Session, Task, Upload, UsageRecord, WebhookDelivery, utcnow
from .security import redact_text

logger = logging.getLogger(__name__)
BACKUP_FORMAT = 2


def _safe_extract(archive: zipfile.ZipFile, target: Path) -> None:
    for member in archive.infolist():
        destination = (target / member.filename).resolve()
        if not destination.is_relative_to(target.resolve()):
            raise ValueError("备份包包含路径穿越")
        archive.extract(member, target)


def _zip_manifest() -> dict[str, Any]:
    return {
        "formatVersion": BACKUP_FORMAT,
        "createdAt": utcnow(),
        "app": "mini-agent",
        "appVersion": "0.2.0",
    }


def pre_migration_backup(config: AppConfig) -> Path | None:
    """Cheap file-level backup before Alembic mutates the schema."""

    if not config.backup.enabled or not config.backup.auto_before_migration:
        return None
    db_path = config.data_dir / "mini-agent.sqlite3"
    if not db_path.exists():
        return None
    target_dir = config.data_dir / "backups"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"pre-migration-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.zip"
    try:
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(db_path, "mini-agent.sqlite3")
            archive.writestr("manifest.json", json.dumps(_zip_manifest(), ensure_ascii=False))
        return target
    except OSError:
        logger.exception("pre-migration backup failed")
        return None


def _sqlite_ok(path: Path) -> bool:
    try:
        connection = sqlite3.connect(path)
        try:
            row = connection.execute("PRAGMA integrity_check").fetchone()
        finally:
            connection.close()
        return bool(row and row[0] == "ok")
    except sqlite3.Error:
        return False


def apply_pending_restore(config: AppConfig) -> bool:
    """Apply a staged restore with rollback on failure.

    The previous database and data directories are moved aside first; only a
    validated database is swapped in. If anything fails, the old state is
    restored and the app starts normally instead of losing user data.
    """

    marker = config.data_dir / "restore-pending.json"
    if not marker.exists():
        return False
    staging: Path | None = None
    backup_dir: Path | None = None
    moved: list[tuple[Path, Path]] = []
    new_dirs: list[Path] = []
    db_replaced = False
    db_path = config.data_dir / "mini-agent.sqlite3"
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
        staging = Path(payload["staging"]).resolve()
        if not staging.is_relative_to(config.data_dir.resolve()):
            raise ValueError("恢复目录非法")
        source_db = staging / "mini-agent.sqlite3"
        if not source_db.exists() or not _sqlite_ok(source_db):
            raise ValueError("备份数据库完整性校验失败")
        pre_migration_backup(config)
        backup_dir = config.data_dir / f".pre-restore-{int(time.time())}"
        backup_dir.mkdir(parents=True, exist_ok=False)
        for suffix in ("", "-wal", "-shm"):
            old = Path(str(db_path) + suffix)
            if old.exists():
                destination = backup_dir / (old.name)
                os.replace(old, destination)
                moved.append((old, destination))
        temporary = db_path.with_suffix(".restore.tmp")
        shutil.copy2(source_db, temporary)
        if not _sqlite_ok(temporary):
            temporary.unlink(missing_ok=True)
            raise ValueError("恢复数据库复制后校验失败")
        os.replace(temporary, db_path)
        db_replaced = True
        for directory in ("uploads", "artifacts", "checkpoints"):
            source = staging / directory
            if not source.exists():
                continue
            destination = config.data_dir / directory
            if destination.exists():
                old = backup_dir / directory
                os.replace(destination, old)
                moved.append((destination, old))
            shutil.copytree(source, destination)
            new_dirs.append(destination)
        shutil.rmtree(staging, ignore_errors=True)
        marker.unlink(missing_ok=True)
        logger.warning("pending restore applied")
        return True
    except Exception:  # noqa: BLE001
        logger.exception("pending restore failed; rolling back existing data")
        for directory in new_dirs:
            shutil.rmtree(directory, ignore_errors=True)
        if db_replaced and db_path.exists():
            db_path.unlink(missing_ok=True)
        for original, saved in reversed(moved):
            try:
                if saved.exists():
                    os.replace(saved, original)
            except OSError:
                logger.exception("restore rollback failed for %s", original)
        if backup_dir is not None:
            shutil.rmtree(backup_dir, ignore_errors=True)
        marker.unlink(missing_ok=True)
        return False


class BackupService:
    def __init__(self, db: Database, config: AppConfig) -> None:
        self.db = db
        self.config = config
        self.root = config.data_dir / "backups"

    async def create(self, *, include_artifacts: bool | None = None) -> Backup:
        self.root.mkdir(parents=True, exist_ok=True)
        backup_id = f"bak_{int(time.time())}_{os.urandom(3).hex()}"
        db_path = self.config.data_dir / "mini-agent.sqlite3"
        staging = self.config.data_dir / ".backup-staging" / backup_id
        staging.mkdir(parents=True, exist_ok=True)
        try:
            destination = staging / "mini-agent.sqlite3"
            source = sqlite3.connect(db_path)
            target = sqlite3.connect(destination)
            try:
                source.backup(target)
            finally:
                target.close()
                source.close()
            includes = ["database"]
            if include_artifacts if include_artifacts is not None else self.config.backup.include_artifacts:
                for directory in ("uploads", "artifacts", "checkpoints"):
                    source_dir = self.config.data_dir / directory
                    if source_dir.exists():
                        shutil.copytree(source_dir, staging / directory)
                        includes.append(directory)
            manifest = _zip_manifest() | {"includes": includes}
            (staging / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            filename = f"{backup_id}.zip"
            archive_path = self.root / filename
            with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
                for file in staging.rglob("*"):
                    if file.is_file():
                        archive.write(file, file.relative_to(staging).as_posix())
            size = archive_path.stat().st_size
            row = Backup(
                id=backup_id,
                filename=filename,
                size=size,
                includes_json=json.dumps(includes),
            )
            async with self.db.session() as session:
                session.add(row)
                await session.commit()
            return row
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    async def list(self) -> list[Backup]:
        async with self.db.session() as db:
            return list((await db.execute(select(Backup).order_by(Backup.created_at.desc()))).scalars().all())

    def path(self, backup_id: str) -> Path:
        target = (self.root / f"{backup_id}.zip").resolve()
        if not target.is_relative_to(self.root.resolve()) or not target.exists():
            raise LookupError("备份不存在")
        return target

    async def delete(self, backup_id: str) -> None:
        target = self.path(backup_id)
        target.unlink(missing_ok=True)
        async with self.db.session() as db:
            row = await db.get(Backup, backup_id)
            if row:
                await db.delete(row)
                await db.commit()

    async def stage_restore(self, backup_id: str) -> dict[str, Any]:
        archive_path = self.path(backup_id)
        staging_root = self.config.data_dir / ".restore-staging"
        staging = staging_root / backup_id
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(archive_path) as archive:
                _safe_extract(archive, staging)
            manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
            if int(manifest.get("formatVersion", 0)) > BACKUP_FORMAT:
                raise ValueError("备份版本高于当前应用支持版本")
            if not (staging / "mini-agent.sqlite3").exists():
                raise ValueError("备份缺少数据库文件")
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        marker = self.config.data_dir / "restore-pending.json"
        marker.write_text(json.dumps({"staging": str(staging), "backupId": backup_id}), encoding="utf-8")
        return {"backupId": backup_id, "staging": str(staging), "requiresRestart": True}


class RetentionService:
    def __init__(self, db: Database, config: AppConfig) -> None:
        self.db = db
        self.config = config

    async def cleanup(self) -> dict[str, Any]:
        now = datetime.now(UTC)
        removed = {
            "events": 0,
            "artifacts": 0,
            "uploads": 0,
            "backups": 0,
            "logs": 0,
            "webhook_deliveries": 0,
        }
        event_cutoff = (
            (now - timedelta(days=self.config.retention.events_days)).isoformat().replace("+00:00", "Z")
        )
        async with self.db.session() as db:
            result = await db.execute(delete(Event).where(Event.created_at < event_cutoff))
            removed["events"] = result.rowcount or 0
            delivery_cutoff = event_cutoff
            result = await db.execute(
                delete(WebhookDelivery).where(WebhookDelivery.created_at < delivery_cutoff)
            )
            removed["webhook_deliveries"] = result.rowcount or 0
            upload_rows = (
                (
                    await db.execute(
                        select(Upload).where(
                            Upload.created_at
                            < (now - timedelta(days=self.config.retention.uploads_days))
                            .isoformat()
                            .replace("+00:00", "Z")
                        )
                    )
                )
                .scalars()
                .all()
            )
            for upload in upload_rows:
                target = (self.config.data_dir / upload.path).resolve()
                if target.is_relative_to(self.config.data_dir):
                    target.unlink(missing_ok=True)
                await db.delete(upload)
                removed["uploads"] += 1
            await db.commit()
        for directory, key, days in (
            (self.config.data_dir / "artifacts", "artifacts", self.config.retention.artifacts_days),
            (self.config.data_dir / "backups", "backups", self.config.retention.backups_days),
            (self.config.data_dir / "logs", "logs", self.config.retention.logs_days),
        ):
            if not directory.exists():
                continue
            cutoff = time.time() - days * 86_400
            for child in directory.iterdir():
                try:
                    if child.is_file() and child.stat().st_mtime < cutoff:
                        child.unlink()
                        removed[key] += 1
                    elif child.is_dir() and child.stat().st_mtime < cutoff:
                        shutil.rmtree(child)
                        removed[key] += 1
                except OSError:
                    continue
        return removed


class LogService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.log_dir = config.data_dir / "logs"

    def export_zip(self) -> Path:
        target = self.config.data_dir / "exports" / f"logs-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.zip"
        target.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            if self.log_dir.exists():
                for file in self.log_dir.iterdir():
                    if file.is_file():
                        content = redact_text(
                            file.read_text(encoding="utf-8", errors="replace"), self.config.secret_values()
                        )
                        archive.writestr(file.name, content)
        return target


async def metrics_text(
    db: Database, config: AppConfig, *, active_sessions: int, subscribers: int, uptime: int
) -> str:
    async with db.session() as session:
        sessions = (await session.execute(select(func.count(Session.id)))).scalar_one()
        running_tasks = (
            await session.execute(select(func.count(Task.id)).where(Task.status.in_(["queued", "running"])))
        ).scalar_one()
        usage = (
            await session.execute(
                select(
                    func.coalesce(func.sum(UsageRecord.input_tokens), 0),
                    func.coalesce(func.sum(UsageRecord.output_tokens), 0),
                    func.coalesce(func.sum(UsageRecord.cache_hit_tokens), 0),
                    func.coalesce(func.sum(UsageRecord.cost), 0),
                )
            )
        ).one()
    lines = [
        "# HELP mini_agent_info Application info",
        "# TYPE mini_agent_info gauge",
        'mini_agent_info{version="0.2.0"} 1',
        "# HELP mini_agent_uptime_seconds Process uptime",
        "# TYPE mini_agent_uptime_seconds gauge",
        f"mini_agent_uptime_seconds {uptime}",
        "# HELP mini_agent_sessions_total Sessions",
        "# TYPE mini_agent_sessions_total gauge",
        f"mini_agent_sessions_total {sessions}",
        "mini_agent_active_sessions " + str(active_sessions),
        "mini_agent_sse_subscribers " + str(subscribers),
        "mini_agent_running_tasks " + str(running_tasks),
        "mini_agent_input_tokens_total " + str(usage[0]),
        "mini_agent_output_tokens_total " + str(usage[1]),
        "mini_agent_cache_hit_tokens_total " + str(usage[2]),
        "mini_agent_cost_usd_total " + str(round(float(usage[3]), 8)),
    ]
    return "\n".join(lines) + "\n"
