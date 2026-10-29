"""Structured JSON logging with request correlation and secret redaction."""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from .security import redact_text

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
session_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("session_id", default="")
_secrets: list[str] = []


def configure_logging(
    level: str = "INFO", secrets: list[str] | None = None, log_dir: Path | None = None
) -> None:
    global _secrets
    _secrets = [value for value in (secrets or []) if value]
    formatter = JsonFormatter()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handlers: list[logging.Handler] = [handler]
    if log_dir is not None:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                log_dir / "app.log",
                maxBytes=10 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            handlers.append(file_handler)
        except OSError:
            pass
    root = logging.getLogger()
    root.handlers = handlers
    root.setLevel(level.upper())
    for noisy in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel("WARNING")


class JsonFormatter(logging.Formatter):
    _standard = {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "asctime",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = request_id_var.get()
        session_id = session_id_var.get()
        if request_id:
            payload["requestId"] = request_id
        if session_id:
            payload["sessionId"] = session_id
        for key, value in record.__dict__.items():
            if key not in self._standard and not key.startswith("_"):
                try:
                    json.dumps(value)
                    payload[key] = value
                except (TypeError, ValueError):
                    payload[key] = str(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        text = json.dumps(payload, ensure_ascii=False, default=str)
        return redact_text(text, _secrets)
