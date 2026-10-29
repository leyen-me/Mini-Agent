"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import api
from .config import AppConfig, load_config, repo_root
from .context import Services
from .db import Database
from .events import EventBroker, EventStore
from .logging_utils import configure_logging
from .model_gateway import BaseGateway, create_gateway
from .recovery import recover_interrupted_work
from .runtime import RuntimeManager
from .seed import seed
from .tasks import TaskManager
from .tools import ToolRegistry

logger = logging.getLogger(__name__)
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def create_app(
    config: AppConfig | None = None,
    *,
    gateway_override: BaseGateway | None = None,
) -> FastAPI:
    if config is None:
        from dotenv import load_dotenv

        load_dotenv(repo_root() / ".env", override=False)
    resolved_config = config or load_config()
    configure_logging(resolved_config.server.log_level, resolved_config.secret_values())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        started_at = datetime.now(UTC)
        db = Database(resolved_config)
        await db.init()
        await seed(db, resolved_config)
        broker = EventBroker()
        events = EventStore(broker)
        recovery = await recover_interrupted_work(db, events)
        gateway = gateway_override or create_gateway(resolved_config)
        registry = ToolRegistry()
        tasks = TaskManager(db, resolved_config, events)
        runtime = RuntimeManager(
            db=db,
            config=resolved_config,
            events=events,
            gateway=gateway,
            registry=registry,
            tasks=tasks,
        )
        tasks.on_complete = runtime.on_task_complete
        app.state.services = Services(
            config=resolved_config,
            db=db,
            broker=broker,
            events=events,
            gateway=gateway,
            registry=registry,
            tasks=tasks,
            runtime=runtime,
            started_at=started_at,
            recovery=recovery,
        )
        logger.info(
            "mini-agent started",
            extra={
                "version": api.APP_VERSION,
                "data_dir": str(resolved_config.data_dir),
                "model_configured": resolved_config.model_configured(),
                "recovered_sessions": recovery.get("count", 0),
            },
        )
        try:
            yield
        finally:
            logger.info("mini-agent shutting down")
            await runtime.shutdown()
            await gateway.aclose()
            await db.dispose()

    app = FastAPI(
        title="Mini Agent",
        version=api.APP_VERSION,
        description="A minimal but production-minded local agent runtime.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_config.server.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    api.install_middleware(app)
    api.install_exception_handlers(app)
    app.include_router(api.router)

    if FRONTEND_DIST.exists():
        assets = FRONTEND_DIST / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str):
            if full_path.startswith(("api/", "docs", "openapi.json", "redoc")):
                return JSONResponse(status_code=404, content={"code": "NOT_FOUND", "message": "Not found"})
            target = (FRONTEND_DIST / full_path).resolve()
            if target.is_file() and target.is_relative_to(FRONTEND_DIST.resolve()):
                return FileResponse(target)
            index = FRONTEND_DIST / "index.html"
            if index.exists():
                return FileResponse(index)
            return JSONResponse(status_code=404, content={"code": "NOT_FOUND", "message": "UI not built"})

    return app
