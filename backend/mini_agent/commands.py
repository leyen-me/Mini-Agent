"""Custom slash commands with parameter expansion and preview."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select

from .db import Database
from .models import CustomCommand, utcnow


def command_dto(row: CustomCommand) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "promptTemplate": row.prompt_template,
        "agentId": row.agent_id,
        "model": row.model,
        "reasoningLevel": row.reasoning_level,
        "enabled": row.enabled,
        "scope": row.scope,
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
    }


def render_template(template: str, arguments: str) -> str:
    """Expand ``$ARGUMENTS`` and positional ``$1``..``$9`` placeholders."""

    parsed = re.findall(r'"[^"]*"|\'[^\']*\'|\S+', arguments)
    stripped = [item[1:-1] if item[:1] in "\"'" and item[-1:] == item[:1] else item for item in parsed]
    rendered = template.replace("$ARGUMENTS", arguments.strip())
    for index in range(1, 10):
        rendered = rendered.replace(f"${index}", stripped[index - 1] if index <= len(stripped) else "")
    return rendered


class CommandService:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def list(self, enabled_only: bool = False) -> list[CustomCommand]:
        async with self.db.session() as db:
            stmt = select(CustomCommand).order_by(CustomCommand.scope.asc(), CustomCommand.name.asc())
            if enabled_only:
                stmt = stmt.where(CustomCommand.enabled.is_(True))
            return list((await db.execute(stmt)).scalars().all())

    async def get(self, command_id: str) -> CustomCommand:
        async with self.db.session() as db:
            row = await db.get(CustomCommand, command_id)
            if row is None:
                raise LookupError("命令不存在")
            return row

    async def create(self, payload: dict[str, Any]) -> CustomCommand:
        name = str(payload.get("name") or "").strip().lstrip("/")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", name):
            raise ValueError("命令名只能包含小写字母、数字、- 和 _")
        template = str(payload.get("promptTemplate") or payload.get("prompt_template") or "").strip()
        if not template:
            raise ValueError("promptTemplate 不能为空")
        async with self.db.session() as db:
            exists = (
                (await db.execute(select(CustomCommand).where(CustomCommand.name == name))).scalars().first()
            )
            if exists:
                raise ValueError("命令名已存在")
            row = CustomCommand(
                name=name,
                description=str(payload.get("description") or ""),
                prompt_template=template,
                agent_id=payload.get("agentId") or payload.get("agent_id"),
                model=payload.get("model"),
                reasoning_level=payload.get("reasoningLevel") or payload.get("reasoning_level"),
                enabled=bool(payload.get("enabled", True)),
                scope=str(payload.get("scope") or "system"),
            )
            db.add(row)
            await db.commit()
            return row

    async def update(self, command_id: str, payload: dict[str, Any]) -> CustomCommand:
        async with self.db.session() as db:
            row = await db.get(CustomCommand, command_id)
            if row is None:
                raise LookupError("命令不存在")
            mapping = {
                "name": "name",
                "description": "description",
                "promptTemplate": "prompt_template",
                "prompt_template": "prompt_template",
                "agentId": "agent_id",
                "agent_id": "agent_id",
                "model": "model",
                "reasoningLevel": "reasoning_level",
                "reasoning_level": "reasoning_level",
                "enabled": "enabled",
                "scope": "scope",
            }
            for key, attribute in mapping.items():
                if key in payload:
                    value = payload[key]
                    if attribute == "name":
                        value = str(value).lstrip("/")
                    setattr(row, attribute, value)
            row.updated_at = utcnow()
            await db.commit()
            return row

    async def delete(self, command_id: str) -> None:
        async with self.db.session() as db:
            row = await db.get(CustomCommand, command_id)
            if row is None:
                raise LookupError("命令不存在")
            await db.delete(row)
            await db.commit()

    async def render(self, command_id: str, arguments: str) -> dict[str, Any]:
        row = await self.get(command_id)
        return {
            "command": command_dto(row),
            "prompt": render_template(row.prompt_template, arguments),
        }

    async def resolve_slash(self, text: str) -> dict[str, Any] | None:
        """Return an expanded custom command when ``text`` starts with its slash name."""

        if not text.startswith("/"):
            return None
        name, _, rest = text[1:].partition(" ")
        async with self.db.session() as db:
            row = (
                (
                    await db.execute(
                        select(CustomCommand).where(
                            CustomCommand.name == name, CustomCommand.enabled.is_(True)
                        )
                    )
                )
                .scalars()
                .first()
            )
        if row is None:
            return None
        return {"command": command_dto(row), "prompt": render_template(row.prompt_template, rest)}
