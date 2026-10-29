"""Signed outbound webhooks with persisted delivery attempts."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from typing import Any

import httpx
from sqlalchemy import select

from .config import AppConfig
from .db import Database
from .events import EventDTO
from .models import Webhook, WebhookDelivery, utcnow
from .security import assert_safe_url

logger = logging.getLogger(__name__)


def webhook_dto(row: Webhook) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "url": row.url,
        "events": json.loads(row.events_json or '["*"]'),
        "enabled": row.enabled,
        "secretConfigured": bool(row.secret),
        "lastStatus": row.last_status,
        "lastError": row.last_error,
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
    }


class WebhookService:
    def __init__(self, db: Database, config: AppConfig) -> None:
        self.db = db
        self.config = config
        self._tasks: set[asyncio.Task[Any]] = set()

    async def list(self) -> list[Webhook]:
        async with self.db.session() as db:
            return list(
                (await db.execute(select(Webhook).order_by(Webhook.created_at.desc()))).scalars().all()
            )

    async def get(self, webhook_id: str) -> Webhook:
        async with self.db.session() as db:
            row = await db.get(Webhook, webhook_id)
            if row is None:
                raise LookupError("Webhook 不存在")
            return row

    async def create(self, payload: dict[str, Any]) -> Webhook:
        url = str(payload.get("url") or "")
        if not url.startswith(("http://", "https://")):
            raise ValueError("Webhook URL 必须是 http/https")
        await assert_safe_url(url, block_private=self.config.security.block_private_network)
        async with self.db.session() as db:
            row = Webhook(
                name=str(payload.get("name") or "Webhook"),
                url=url,
                events_json=json.dumps(payload.get("events") or ["*"]),
                secret=str(payload.get("secret") or ""),
                enabled=bool(payload.get("enabled", True)),
            )
            db.add(row)
            await db.commit()
            return row

    async def update(self, webhook_id: str, payload: dict[str, Any]) -> Webhook:
        async with self.db.session() as db:
            row = await db.get(Webhook, webhook_id)
            if row is None:
                raise LookupError("Webhook 不存在")
            if "name" in payload:
                row.name = str(payload["name"])
            if "url" in payload:
                url = str(payload["url"])
                if not url.startswith(("http://", "https://")):
                    raise ValueError("Webhook URL 必须是 http/https")
                await assert_safe_url(url, block_private=self.config.security.block_private_network)
                row.url = url
            if "events" in payload:
                row.events_json = json.dumps(payload["events"])
            if "secret" in payload:
                row.secret = str(payload["secret"] or "")
            if "enabled" in payload:
                row.enabled = bool(payload["enabled"])
            row.updated_at = utcnow()
            await db.commit()
            return row

    async def delete(self, webhook_id: str) -> None:
        async with self.db.session() as db:
            row = await db.get(Webhook, webhook_id)
            if row is None:
                raise LookupError("Webhook 不存在")
            deliveries = (
                (await db.execute(select(WebhookDelivery).where(WebhookDelivery.webhook_id == webhook_id)))
                .scalars()
                .all()
            )
            for item in deliveries:
                await db.delete(item)
            await db.delete(row)
            await db.commit()

    async def test(self, webhook_id: str) -> dict[str, Any]:
        row = await self.get(webhook_id)
        event = EventDTO(
            id=0,
            session_id="test",
            type="webhook.test",
            payload={"message": "Mini Agent webhook test"},
            created_at=utcnow(),
        )
        return await self._deliver(row, event, "test")

    def schedule(self, event: EventDTO) -> None:
        if not self.config.webhooks.enabled:
            return
        # Delta events can arrive hundreds of times per turn; webhooks are for
        # semantic state changes, not token-level streaming.
        if event.type.endswith(".delta"):
            return
        task = asyncio.create_task(self.dispatch(event), name=f"webhook:{event.id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def dispatch(self, event: EventDTO) -> None:
        rows = await self.list()
        if not rows:
            return
        for row in rows:
            if not row.enabled:
                continue
            events = json.loads(row.events_json or '["*"]')
            if "*" not in events and event.type not in events:
                continue
            await self._deliver(row, event, f"{event.id}")

    async def _deliver(self, webhook: Webhook, event: EventDTO, delivery_key: str) -> dict[str, Any]:
        body = json.dumps(
            {
                "id": event.id,
                "type": event.type,
                "sessionId": event.session_id,
                "payload": event.payload,
                "createdAt": event.created_at,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        signature = ""
        if webhook.secret:
            signature = hmac.new(webhook.secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "X-Mini-Agent-Event": event.type,
            "X-Mini-Agent-Delivery": delivery_key,
        }
        if signature:
            headers["X-Mini-Agent-Signature"] = f"sha256={signature}"
        last_error = ""
        response_code = None
        status = "failed"
        async with self.db.session() as db:
            delivery = WebhookDelivery(
                webhook_id=webhook.id,
                event_id=event.id,
                status="pending",
            )
            db.add(delivery)
            await db.flush()
            delivery_id = delivery.id
            await db.commit()
        for attempt in range(1, self.config.webhooks.max_attempts + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=self.config.webhooks.timeout_seconds,
                    proxy=self.config.network.proxy or None,
                    verify=self.config.network.ca_bundle or True,
                ) as client:
                    response = await client.post(webhook.url, content=body, headers=headers)
                response_code = response.status_code
                if response.status_code < 400:
                    status = "succeeded"
                    last_error = ""
                    break
                last_error = f"HTTP {response.status_code}"
            except httpx.HTTPError as exc:
                last_error = type(exc).__name__
            if attempt < self.config.webhooks.max_attempts:
                await asyncio.sleep(min(4, 2 ** (attempt - 1)))
        async with self.db.session() as db:
            delivery = await db.get(WebhookDelivery, delivery_id)
            if delivery:
                delivery.status = status
                delivery.attempts = attempt
                delivery.response_code = response_code
                delivery.error = last_error[:500]
                delivery.updated_at = utcnow()
            hook = await db.get(Webhook, webhook.id)
            if hook:
                hook.last_status = status
                hook.last_error = last_error[:500] or None
                hook.updated_at = utcnow()
            await db.commit()
        return {
            "ok": status == "succeeded",
            "status": status,
            "attempts": attempt,
            "responseCode": response_code,
            "error": last_error,
        }

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
