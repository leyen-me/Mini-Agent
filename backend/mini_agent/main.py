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
from .agents import AgentService
from .checkpoints import CheckpointService
from .commands import CommandService
from .config import AppConfig, load_config, repo_root
from .context import Services
from .db import Database
from .events import EventBroker, EventStore
from .hooks import HookService
from .logging_utils import configure_logging
from .mcp import McpService
from .model_gateway import BaseGateway, create_gateway
from .ops import (
    BackupService,
    LogService,
    RetentionService,
    apply_pending_restore,
    metrics_text,
    pre_migration_backup,
)
from .plugins import PluginService
from .providers import ProviderService
from .recovery import recover_interrupted_work
from .runtime import RuntimeManager
from .sandbox import select_sandbox
from .seed import seed
from .skills import SkillService
from .tasks import TaskManager
from .tools import ToolRegistry
from .uploads import UploadService
from .webhooks import WebhookService

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
    configure_logging(
        resolved_config.server.log_level,
        resolved_config.secret_values(),
        resolved_config.data_dir / "logs",
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        started_at = datetime.now(UTC)
        apply_pending_restore(resolved_config)
        pre_migration_backup(resolved_config)
        db = Database(resolved_config)
        await db.init()
        await seed(db, resolved_config)
        provider_service = ProviderService(db, resolved_config)
        await provider_service.seed_and_load()
        broker = EventBroker()
        events = EventStore(broker)
        recovery = await recover_interrupted_work(db, events)
        gateway = gateway_override or create_gateway(
            resolved_config, header_resolver=provider_service.auth_headers
        )
        agent_service = AgentService(db)
        skill_service = SkillService(db, resolved_config)
        mcp_service = McpService(db, resolved_config)
        plugin_service = PluginService(db, resolved_config)
        await plugin_service.sync()
        command_service = CommandService(db)
        hook_service = HookService(db, resolved_config)
        upload_service = UploadService(db, resolved_config)
        checkpoint_service = CheckpointService(db, resolved_config, events)
        backup_service = BackupService(db, resolved_config)
        retention_service = RetentionService(db, resolved_config)
        log_service = LogService(resolved_config)
        webhook_service = WebhookService(db, resolved_config)
        events.attach_webhooks(webhook_service)
        registry = ToolRegistry()
        tasks = TaskManager(db, resolved_config, events)
        runtime = RuntimeManager(
            db=db,
            config=resolved_config,
            events=events,
            gateway=gateway,
            registry=registry,
            tasks=tasks,
            agents=agent_service,
            skills=skill_service,
            mcp=mcp_service,
            plugins=plugin_service,
            hooks=hook_service,
            uploads=upload_service,
            checkpoints=checkpoint_service,
            commands=command_service,
        )
        tasks.on_complete = runtime.on_task_complete
        app.state.services = Services(
            config=resolved_config,
            db=db,
            broker=broker,
            events=events,
            gateway=gateway,
            providers=provider_service,
            agents=agent_service,
            skills=skill_service,
            mcp=mcp_service,
            plugins=plugin_service,
            commands=command_service,
            hooks=hook_service,
            uploads=upload_service,
            checkpoints=checkpoint_service,
            backups=backup_service,
            retention=retention_service,
            logs=log_service,
            webhooks=webhook_service,
            registry=registry,
            tasks=tasks,
            runtime=runtime,
            started_at=started_at,
            recovery=recovery,
        )
        try:
            await retention_service.cleanup()
        except Exception:  # noqa: BLE001
            logger.exception("retention cleanup failed")
        sandbox_mode = select_sandbox(resolved_config)
        if resolved_config.sandbox.mode != "none" and sandbox_mode == "none":
            logger.warning("no shell sandbox runtime available; shell commands are not OS-isolated")
        logger.info(
            "mini-agent started",
            extra={
                "version": api.APP_VERSION,
                "data_dir": str(resolved_config.data_dir),
                "model_configured": resolved_config.model_configured(),
                "recovered_sessions": recovery.get("count", 0),
                "sandbox": sandbox_mode,
            },
        )
        try:
            yield
        finally:
            logger.info("mini-agent shutting down")
            await runtime.shutdown()
            await mcp_service.shutdown()
            await webhook_service.shutdown()
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

    @app.get("/metrics", include_in_schema=False)
    async def metrics():
        from fastapi.responses import PlainTextResponse

        services = app.state.services
        if not services.config.metrics.enabled:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="metrics disabled")
        return PlainTextResponse(
            await metrics_text(
                services.db,
                services.config,
                active_sessions=len(services.runtime.active_sessions()),
                subscribers=services.broker.subscriber_count,
                uptime=int((datetime.now(UTC) - services.started_at).total_seconds()),
            )
        )

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
