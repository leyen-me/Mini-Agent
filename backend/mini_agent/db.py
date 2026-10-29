"""Database bootstrap, session factory and migration execution."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from .config import AppConfig
from .models import Base

logger = logging.getLogger(__name__)
BACKEND_ROOT = Path(__file__).resolve().parents[1]


class Database:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.path = config.data_dir / "mini-agent.sqlite3"
        self.url = f"sqlite+aiosqlite:///{self.path}"
        self.engine: AsyncEngine = create_async_engine(
            self.url,
            connect_args={"timeout": 30},
            pool_pre_ping=True,
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)
        self._configure_pragmas()

    def _configure_pragmas(self) -> None:
        @event.listens_for(self.engine.sync_engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

    async def init(self) -> None:
        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(run_migrations, self.config.data_dir)
        async with self.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    async def dispose(self) -> None:
        await self.engine.dispose()

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise


def run_migrations(data_dir: Path) -> None:
    """Run Alembic migrations against the sync SQLite URL.

    Alembic is synchronous; callers run this in a worker thread. ``MINI_AGENT_DB_URL``
    is consumed by ``migrations/env.py``.
    """

    import os

    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "mini-agent.sqlite3"
    previous = os.environ.get("MINI_AGENT_DB_URL")
    os.environ["MINI_AGENT_DB_URL"] = f"sqlite:///{db_path}"
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    try:
        command.upgrade(cfg, "head")
    finally:
        if previous is None:
            os.environ.pop("MINI_AGENT_DB_URL", None)
        else:
            os.environ["MINI_AGENT_DB_URL"] = previous


async def create_schema_fallback(engine: AsyncEngine) -> None:
    """Test helper used only when migrations cannot be loaded."""

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
