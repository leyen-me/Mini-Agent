"""Agent CRUD, import/export and per-agent policy resolution."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select

from .config import PermissionConfig
from .db import Database
from .models import Agent, Message, Session, new_id, utcnow


def json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
        return [str(item) for item in value] if isinstance(value, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def json_dict(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def agent_dto(agent: Agent) -> dict[str, Any]:
    return {
        "id": agent.id,
        "name": agent.name,
        "description": agent.description,
        "systemPrompt": agent.system_prompt,
        "readOnly": agent.read_only,
        "builtin": agent.builtin,
        "enabled": agent.enabled,
        "defaultModel": agent.default_model,
        "defaultReasoningLevel": agent.default_reasoning_level,
        "tools": json_list(agent.tools_json) if agent.tools_json else None,
        "permissions": json_dict(agent.permissions_json),
        "skills": json_list(agent.skills_json),
        "mcp": json_list(agent.mcp_json),
        "structuredOutput": json_dict(agent.structured_output_json) if agent.structured_output_json else None,
        "createdAt": agent.created_at,
        "updatedAt": agent.updated_at,
    }


class AgentService:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def list(self, include_disabled: bool = True) -> list[Agent]:
        async with self.db.session() as db:
            stmt = select(Agent).order_by(Agent.builtin.desc(), Agent.created_at.asc())
            if not include_disabled:
                stmt = stmt.where(Agent.enabled.is_(True))
            return list((await db.execute(stmt)).scalars().all())

    async def get(self, agent_id: str) -> Agent:
        async with self.db.session() as db:
            agent = await db.get(Agent, agent_id)
            if agent is None:
                raise LookupError("Agent 不存在")
            return agent

    async def create(self, payload: dict[str, Any], *, builtin: bool = False) -> Agent:
        agent_id = str(payload.get("id") or new_id("agent"))[:64]
        async with self.db.session() as db:
            if await db.get(Agent, agent_id):
                raise ValueError("Agent ID 已存在")
            agent = Agent(
                id=agent_id,
                name=str(payload.get("name") or "New Agent")[:120],
                description=str(payload.get("description") or ""),
                system_prompt=str(payload.get("systemPrompt") or payload.get("system_prompt") or ""),
                read_only=bool(payload.get("readOnly") or payload.get("read_only") or False),
                builtin=builtin,
                enabled=bool(payload.get("enabled", True)),
                default_model=payload.get("defaultModel") or payload.get("default_model"),
                default_reasoning_level=str(
                    payload.get("defaultReasoningLevel") or payload.get("default_reasoning_level") or "off"
                ),
                tools_json=self._dump(payload.get("tools")) if payload.get("tools") is not None else None,
                permissions_json=self._dump(payload.get("permissions") or {}),
                skills_json=self._dump(payload.get("skills") or []),
                mcp_json=self._dump(payload.get("mcp") or []),
                structured_output_json=(
                    self._dump(payload.get("structuredOutput") or payload.get("structured_output"))
                    if (payload.get("structuredOutput") or payload.get("structured_output"))
                    else None
                ),
            )
            db.add(agent)
            await db.commit()
            return agent

    async def update(self, agent_id: str, payload: dict[str, Any]) -> Agent:
        async with self.db.session() as db:
            agent = await db.get(Agent, agent_id)
            if agent is None:
                raise LookupError("Agent 不存在")
            mapping = {
                "name": "name",
                "description": "description",
                "systemPrompt": "system_prompt",
                "system_prompt": "system_prompt",
                "readOnly": "read_only",
                "read_only": "read_only",
                "enabled": "enabled",
                "defaultModel": "default_model",
                "default_model": "default_model",
                "defaultReasoningLevel": "default_reasoning_level",
                "default_reasoning_level": "default_reasoning_level",
            }
            for key, attribute in mapping.items():
                if key in payload:
                    setattr(agent, attribute, payload[key])
            if "tools" in payload:
                agent.tools_json = self._dump(payload["tools"]) if payload["tools"] is not None else None
            if "permissions" in payload:
                agent.permissions_json = self._dump(payload["permissions"] or {})
            if "skills" in payload:
                agent.skills_json = self._dump(payload["skills"] or [])
            if "mcp" in payload:
                agent.mcp_json = self._dump(payload["mcp"] or [])
            if "structuredOutput" in payload or "structured_output" in payload:
                value = payload.get("structuredOutput", payload.get("structured_output"))
                agent.structured_output_json = self._dump(value) if value else None
            if not agent.builtin and "readOnly" not in payload and "read_only" not in payload:
                agent.read_only = False
            agent.updated_at = utcnow()
            await db.commit()
            return agent

    async def delete(self, agent_id: str) -> None:
        async with self.db.session() as db:
            agent = await db.get(Agent, agent_id)
            if agent is None:
                raise LookupError("Agent 不存在")
            if agent.builtin:
                raise ValueError("内置 Agent 不能删除，只能停用")
            used = (
                (await db.execute(select(Session).where(Session.agent_id == agent_id).limit(1)))
                .scalars()
                .first()
            )
            if used is not None:
                raise ValueError("仍有会话使用该 Agent")
            await db.delete(agent)
            await db.commit()

    async def duplicate(self, agent_id: str, name: str | None = None) -> Agent:
        source = await self.get(agent_id)
        payload = agent_dto(source)
        payload["id"] = new_id("agent")
        payload["name"] = (name or f"{source.name} Copy")[:120]
        payload["builtin"] = False
        return await self.create(payload)

    async def export(self, agent_id: str) -> dict[str, Any]:
        return {"formatVersion": 1, "agent": agent_dto(await self.get(agent_id))}

    async def import_agent(self, payload: dict[str, Any]) -> Agent:
        data = payload.get("agent", payload)
        data = dict(data)
        data["id"] = new_id("agent")
        data["builtin"] = False
        return await self.create(data)

    async def from_session(self, session_id: str) -> Agent:
        async with self.db.session() as db:
            session = await db.get(Session, session_id)
            if session is None:
                raise LookupError("会话不存在")
            messages = (
                (
                    await db.execute(
                        select(Message)
                        .where(Message.session_id == session_id, Message.role.in_(["user", "assistant"]))
                        .order_by(Message.seq.desc())
                        .limit(12)
                    )
                )
                .scalars()
                .all()
            )
        messages = list(reversed(messages))
        transcript = "\n".join(
            f"{message.role}: {json.loads(message.content_json).get('text', '')[:800]}"
            for message in messages
        )
        prompt = (
            "你是从既有会话中提炼出的专用 Agent。请延续以下对话体现的角色、约束和输出风格：\n\n"
            f"会话主题：{session.title}\n\n{transcript[:8_000]}"
        )
        return await self.create(
            {
                "name": f"{session.title[:30]} Agent",
                "description": f"由会话 {session.id} 提炼",
                "systemPrompt": prompt,
                "tools": None,
                "permissions": {},
            }
        )

    @staticmethod
    def _dump(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def effective_permissions(agent: Agent, base: PermissionConfig) -> PermissionConfig:
        overrides = json_dict(agent.permissions_json)
        if not overrides:
            return base
        merged = base.model_dump()
        for key, value in overrides.items():
            if key in merged and isinstance(value, dict):
                merged[key] = {**merged[key], **value}
        try:
            return PermissionConfig(**merged)
        except ValueError:
            return base

    @staticmethod
    def allowed_tools(agent: Agent) -> set[str] | None:
        items = json_list(agent.tools_json) if agent.tools_json else []
        return set(items) if items else None
