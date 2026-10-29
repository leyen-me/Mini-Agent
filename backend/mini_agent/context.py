"""Application service container shared by HTTP handlers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .agents import AgentService
from .checkpoints import CheckpointService
from .commands import CommandService
from .config import AppConfig
from .db import Database
from .events import EventBroker, EventStore
from .hooks import HookService
from .mcp import McpService
from .model_gateway import BaseGateway
from .ops import BackupService, LogService, RetentionService
from .plugins import PluginService
from .providers import ProviderService
from .runtime import RuntimeManager
from .skills import SkillService
from .tasks import TaskManager
from .tools import ToolRegistry
from .uploads import UploadService
from .webhooks import WebhookService


@dataclass
class Services:
    config: AppConfig
    db: Database
    broker: EventBroker
    events: EventStore
    gateway: BaseGateway
    providers: ProviderService
    agents: AgentService
    skills: SkillService
    mcp: McpService
    plugins: PluginService
    commands: CommandService
    hooks: HookService
    uploads: UploadService
    checkpoints: CheckpointService
    backups: BackupService
    retention: RetentionService
    logs: LogService
    webhooks: WebhookService
    registry: ToolRegistry
    tasks: TaskManager
    runtime: RuntimeManager
    started_at: datetime
    recovery: dict[str, Any]
