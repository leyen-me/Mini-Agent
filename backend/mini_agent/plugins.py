"""Declarative plugin system with isolated HTTP tools, agents and hooks."""

from __future__ import annotations

import io
import json
import logging
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select

from .config import AppConfig
from .db import Database
from .models import Agent, Plugin, Session, utcnow
from .security import UrlSecurityError, assert_safe_url
from .tools import ToolResult

logger = logging.getLogger(__name__)
PLUGIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
TOOL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def plugin_dto(row: Plugin, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.loads(row.manifest_json or "{}") if manifest is None else manifest
    return {
        "id": row.id,
        "name": row.name,
        "version": row.version,
        "description": row.description,
        "path": row.path,
        "enabled": row.enabled,
        "trusted": row.trusted,
        "lastError": row.last_error,
        "manifest": data,
        "tools": data.get("tools", []),
        "commands": data.get("commands", []),
        "agents": data.get("agents", []),
        "hooks": data.get("hooks", []),
        "ui": data.get("ui", []),
        "permissions": data.get("permissions", []),
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
    }


class PluginService:
    def __init__(self, db: Database, config: AppConfig) -> None:
        self.db = db
        self.config = config
        self.root = config.data_dir / "plugins"

    async def sync(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for directory in sorted(self.root.iterdir()):
            if directory.is_dir() and (directory / "plugin.json").exists():
                try:
                    manifest = self._read_manifest(directory)
                    row = await self._upsert(directory, manifest)
                    if row.enabled and row.trusted:
                        await self._sync_agents(row.id, manifest, enabled=True)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "invalid plugin skipped", extra={"path": str(directory), "error": str(exc)}
                    )

    def _read_manifest(self, directory: Path) -> dict[str, Any]:
        path = directory / "plugin.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"plugin.json 无效：{exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("plugin.json 顶层必须是对象")
        plugin_id = str(data.get("id") or directory.name)
        if not PLUGIN_ID_RE.match(plugin_id):
            raise ValueError("插件 ID 非法")
        for tool in data.get("tools", []):
            if not TOOL_NAME_RE.match(str(tool.get("name") or "")):
                raise ValueError("插件工具名非法")
        return data

    async def _upsert(self, directory: Path, manifest: dict[str, Any], *, trusted: bool = False) -> Plugin:
        plugin_id = str(manifest["id"])
        async with self.db.session() as db:
            row = await db.get(Plugin, plugin_id)
            if row is None:
                row = Plugin(
                    id=plugin_id,
                    path=str(directory),
                    trusted=trusted,
                    enabled=False,
                )
                db.add(row)
            row.name = str(manifest.get("name") or plugin_id)
            row.version = str(manifest.get("version") or "0.0.0")
            row.description = str(manifest.get("description") or "")
            row.path = str(directory)
            row.manifest_json = json.dumps(manifest, ensure_ascii=False)
            row.updated_at = utcnow()
            await db.commit()
            return row

    async def list(self) -> list[Plugin]:
        async with self.db.session() as db:
            return list((await db.execute(select(Plugin).order_by(Plugin.id.asc()))).scalars().all())

    async def get(self, plugin_id: str) -> Plugin:
        async with self.db.session() as db:
            row = await db.get(Plugin, plugin_id)
            if row is None:
                raise LookupError("插件不存在")
            return row

    async def install_zip(self, data: bytes, *, trusted: bool = False) -> Plugin:
        with tempfile.TemporaryDirectory(dir=self.root) as temp:
            temp_path = Path(temp)
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    for member in archive.infolist():
                        target = (temp_path / member.filename).resolve()
                        if not target.is_relative_to(temp_path.resolve()):
                            raise ValueError("压缩包包含路径穿越")
                    archive.extractall(temp_path)
            except zipfile.BadZipFile as exc:
                raise ValueError("不是有效的 zip 文件") from exc
            manifests = list(temp_path.rglob("plugin.json"))
            if len(manifests) != 1:
                raise ValueError("插件压缩包必须包含且仅包含一个 plugin.json")
            source = manifests[0].parent
            manifest = self._read_manifest(source)
            destination = self.root / str(manifest["id"])
            if destination.exists():
                raise ValueError("插件已安装")
            shutil.move(str(source), str(destination))
            row = await self._upsert(destination, manifest, trusted=trusted)
            return row

    async def set_enabled(self, plugin_id: str, enabled: bool) -> Plugin:
        row = await self.get(plugin_id)
        manifest = json.loads(row.manifest_json or "{}")
        if enabled and not row.trusted:
            raise ValueError("插件尚未信任，不能启用")
        async with self.db.session() as db:
            db_row = await db.get(Plugin, plugin_id)
            db_row.enabled = enabled
            db_row.updated_at = utcnow()
            await db.commit()
        await self._sync_agents(plugin_id, manifest if enabled else {}, enabled=enabled)
        return await self.get(plugin_id)

    async def set_trusted(self, plugin_id: str, trusted: bool) -> Plugin:
        async with self.db.session() as db:
            row = await db.get(Plugin, plugin_id)
            if row is None:
                raise LookupError("插件不存在")
            row.trusted = trusted
            if not trusted:
                row.enabled = False
            row.updated_at = utcnow()
            await db.commit()
        if not trusted:
            await self._sync_agents(plugin_id, {}, enabled=False)
        return await self.get(plugin_id)

    async def uninstall(self, plugin_id: str) -> None:
        row = await self.get(plugin_id)
        directory = Path(row.path)
        if directory.exists() and directory.resolve().is_relative_to(self.root.resolve()):
            shutil.rmtree(directory)
        await self._sync_agents(plugin_id, {}, enabled=False)
        async with self.db.session() as db:
            db_row = await db.get(Plugin, plugin_id)
            if db_row:
                await db.delete(db_row)
                await db.commit()

    async def _sync_agents(self, plugin_id: str, manifest: dict[str, Any], *, enabled: bool) -> None:
        async with self.db.session() as db:
            desired = {}
            if enabled:
                for item in manifest.get("agents", []):
                    suffix = re.sub(
                        r"[^a-z0-9_-]", "-", str(item.get("id") or item.get("name") or "agent").lower()
                    )
                    agent_id = f"plugin_{plugin_id}_{suffix}"[:64]
                    desired[agent_id] = item
            existing = (
                (await db.execute(select(Agent).where(Agent.id.like(f"plugin_{plugin_id}_%"))))
                .scalars()
                .all()
            )
            for agent in existing:
                if agent.id not in desired:
                    used = (
                        (await db.execute(select(Session).where(Session.agent_id == agent.id).limit(1)))
                        .scalars()
                        .first()
                    )
                    if used is None:
                        await db.delete(agent)
                    else:
                        agent.enabled = False
            for agent_id, item in desired.items():
                agent = await db.get(Agent, agent_id)
                if agent is None:
                    agent = Agent(id=agent_id, enabled=True)
                    db.add(agent)
                agent.name = str(item.get("name") or agent_id)[:120]
                agent.description = str(item.get("description") or "")
                agent.system_prompt = str(item.get("systemPrompt") or item.get("system_prompt") or "")
                agent.read_only = bool(item.get("readOnly", False))
                agent.builtin = False
                agent.enabled = True
                agent.default_model = item.get("defaultModel")
                agent.default_reasoning_level = str(item.get("defaultReasoningLevel") or "off")
                agent.tools_json = json.dumps(item["tools"]) if item.get("tools") is not None else None
                agent.permissions_json = json.dumps(item.get("permissions") or {})
                agent.skills_json = json.dumps(item.get("skills") or [])
                agent.mcp_json = json.dumps(item.get("mcp") or [])
                agent.updated_at = utcnow()
            await db.commit()

    async def enabled_manifests(self) -> list[tuple[Plugin, dict[str, Any]]]:
        rows = await self.list()
        return [(row, json.loads(row.manifest_json or "{}")) for row in rows if row.enabled and row.trusted]

    async def tools(self) -> list[dict[str, Any]]:
        result = []
        for row, manifest in await self.enabled_manifests():
            for tool in manifest.get("tools", []):
                name = f"plugin__{row.id}__{tool.get('name')}"
                result.append(
                    {
                        "type": "function",
                        "function": {
                            "name": name,
                            "description": str(
                                tool.get("description") or manifest.get("description") or name
                            ),
                            "parameters": tool.get("inputSchema")
                            or {"type": "object", "properties": {}, "additionalProperties": False},
                        },
                        "_plugin": row.id,
                        "_tool": tool.get("name"),
                    }
                )
        return sorted(result, key=lambda item: item["function"]["name"])

    async def hooks(self) -> list[dict[str, Any]]:
        result = []
        for row, manifest in await self.enabled_manifests():
            for hook in manifest.get("hooks", []):
                result.append(
                    {
                        "id": f"plugin:{row.id}:{hook.get('name', 'hook')}",
                        "pluginId": row.id,
                        "event": hook.get("event"),
                        "command": hook.get("command"),
                        "timeout": int(hook.get("timeoutSeconds") or 30),
                    }
                )
        return result

    async def execute_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        workspace_root: Path,
    ) -> ToolResult:
        parts = name.split("__")
        if len(parts) != 3 or parts[0] != "plugin":
            return ToolResult.failure("INVALID_INPUT", "插件工具名非法")
        plugin_id, tool_name = parts[1], parts[2]
        row = await self.get(plugin_id)
        if not row.enabled or not row.trusted:
            return ToolResult.failure("PERMISSION_DENIED", "插件未启用或未被信任")
        manifest = json.loads(row.manifest_json or "{}")
        tool = next((item for item in manifest.get("tools", []) if item.get("name") == tool_name), None)
        if tool is None:
            return ToolResult.failure("INVALID_INPUT", "插件工具不存在")
        url = str(tool.get("url") or "")
        if not url:
            return ToolResult.failure("INVALID_INPUT", "插件工具缺少 URL")
        try:
            await assert_safe_url(url, block_private=True)
        except UrlSecurityError as exc:
            return ToolResult.failure("PATH_DENIED", f"插件 URL 被安全策略阻止：{exc}")
        method = str(tool.get("method") or "POST").upper()
        headers = {
            "Content-Type": "application/json",
            **{str(k): str(v) for k, v in (tool.get("headers") or {}).items()},
        }
        timeout = min(
            60,
            max(
                1,
                int(
                    tool.get("timeoutSeconds") or row.timeout_seconds
                    if hasattr(row, "timeout_seconds")
                    else 30
                ),
            ),
        )
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                proxy=self.config.network.proxy or None,
                verify=self.config.network.ca_bundle or True,
            ) as client:
                if method == "GET":
                    response = await client.get(url, params=arguments, headers=headers)
                else:
                    response = await client.request(
                        method, url, json={"arguments": arguments}, headers=headers
                    )
            text = response.text[:2_000_000]
            data: Any
            try:
                data = response.json()
            except json.JSONDecodeError:
                data = {"text": text}
            if response.status_code >= 400:
                return ToolResult.failure(
                    "TOOL_ERROR",
                    f"插件返回 HTTP {response.status_code}",
                    {"response": data, "untrusted": True},
                )
            return ToolResult.success({"response": data, "untrusted": True, "plugin": plugin_id})
        except httpx.HTTPError as exc:
            return ToolResult.failure("NETWORK", f"插件请求失败：{type(exc).__name__}")
