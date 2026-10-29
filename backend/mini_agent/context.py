"""Application service container shared by HTTP handlers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .config import AppConfig
from .db import Database
from .events import EventBroker, EventStore
from .model_gateway import BaseGateway
from .runtime import RuntimeManager
from .tasks import TaskManager
from .tools import ToolRegistry


@dataclass
class Services:
    config: AppConfig
    db: Database
    broker: EventBroker
    events: EventStore
    gateway: BaseGateway
    registry: ToolRegistry
    tasks: TaskManager
    runtime: RuntimeManager
    started_at: datetime
    recovery: dict[str, Any]
