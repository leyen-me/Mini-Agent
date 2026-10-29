"""Minimal MCP client: stdio and streamable HTTP JSON-RPC transports."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from typing import Any

import httpx
from sqlalchemy import select

from .config import AppConfig
from .db import Database
from .models import McpServer, SessionMcp, utcnow

logger = logging.getLogger(__name__)
PROTOCOL_VERSION = "2024-11-05"
SAFE_HEADER_MASK = {"authorization", "x-api-key", "api-key", "cookie", "proxy-authorization"}


class McpError(RuntimeError):
    pass


def mcp_dto(row: McpServer, *, mask_headers: bool = True) -> dict[str, Any]:
    headers = _json(row.headers_json, {})
    if mask_headers:
        headers = {
            key: ("••••" if key.lower() in SAFE_HEADER_MASK else str(value)) for key, value in headers.items()
        }
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "transport": row.transport,
        "command": row.command,
        "args": _json(row.args_json, []),
        "url": row.url,
        "headers": headers,
        "env": {key: "••••" for key in _json(row.env_json, {})},
        "tools": _json(row.tools_json, []),
        "enabled": row.enabled,
        "timeoutSeconds": row.timeout_seconds,
        "scope": row.scope,
        "lastStatus": row.last_status,
        "lastError": row.last_error,
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
    }


def _json(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        value = json.loads(raw)
        return value if isinstance(value, type(default)) else default
    except (json.JSONDecodeError, TypeError):
        return default


class McpClient:
    def __init__(self, server: McpServer, config: AppConfig) -> None:
        self.server = server
        self.config = config
        self._next_id = 0
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._http_session_id: str | None = None
        self._http = httpx.AsyncClient(
            timeout=server.timeout_seconds,
            proxy=config.network.proxy or None,
            verify=config.network.ca_bundle or True,
        )
        self._initialized = False
        self._tools: list[dict[str, Any]] = []

    async def close(self) -> None:
        await self._http.aclose()
        if self._process and self._process.returncode is None:
            try:
                os.killpg(os.getpgid(self._process.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                await asyncio.wait_for(self._process.wait(), timeout=3)
            except TimeoutError:
                try:
                    os.killpg(os.getpgid(self._process.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        self._process = None

    async def initialize(self) -> None:
        async with self._lock:
            if self._initialized:
                return
            await self._ensure_transport()
            await self._request(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "mini-agent", "version": "0.2.0"},
                },
            )
            await self._notify("notifications/initialized", {})
            self._initialized = True

    async def list_tools(self) -> list[dict[str, Any]]:
        await self.initialize()
        result = await self._request("tools/list", {})
        tools = result.get("tools", []) if isinstance(result, dict) else []
        normalized = []
        for item in tools:
            if not isinstance(item, dict):
                continue
            normalized.append(
                {
                    "name": str(item.get("name") or ""),
                    "description": str(item.get("description") or ""),
                    "inputSchema": item.get("inputSchema")
                    or {"type": "object", "properties": {}, "additionalProperties": False},
                }
            )
        self._tools = normalized
        return normalized

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        await self.initialize()
        return await self._request("tools/call", {"name": name, "arguments": arguments})

    async def _ensure_transport(self) -> None:
        if self.server.transport != "stdio":
            return
        if self._process and self._process.returncode is None:
            return
        command = shlex_split(self.server.command)
        if not command:
            raise McpError("MCP stdio command 为空")
        env = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "HOME", "LANG", "LC_ALL", "TERM", "TMPDIR", "USER", "SHELL", "TZ"}
        }
        env.update({str(key): str(value) for key, value in _json(self.server.env_json, {}).items()})
        self._process = await asyncio.create_subprocess_exec(
            *command,
            *[str(item) for item in _json(self.server.args_json, [])],
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            start_new_session=True,
        )

    async def _request(self, method: str, params: dict[str, Any]) -> Any:
        self._next_id += 1
        request_id = self._next_id
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        if self.server.transport == "stdio":
            return await self._stdio_request(payload, request_id)
        if self.server.transport == "sse":
            return await self._sse_request(payload, request_id)
        return await self._http_request(payload, request_id)

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        if self.server.transport == "stdio":
            await self._stdio_write(payload)
        elif self.server.transport == "sse":
            await self._sse_notify(payload)
        else:
            await self._http_request(payload, None)

    async def _stdio_write(self, payload: dict[str, Any]) -> None:
        await self._ensure_transport()
        assert self._process and self._process.stdin
        self._process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        await self._process.stdin.drain()

    async def _stdio_request(self, payload: dict[str, Any], request_id: int) -> Any:
        await self._stdio_write(payload)
        assert self._process and self._process.stdout
        while True:
            try:
                line = await asyncio.wait_for(
                    self._process.stdout.readline(), timeout=self.server.timeout_seconds
                )
            except TimeoutError as exc:
                raise McpError("MCP stdio 响应超时") from exc
            if not line:
                stderr = b""
                if self._process.stderr:
                    try:
                        stderr = await asyncio.wait_for(self._process.stderr.read(4_000), timeout=1)
                    except TimeoutError:
                        pass
                raise McpError(f"MCP 进程退出：{stderr.decode('utf-8', errors='replace')[:300]}")
            try:
                message = json.loads(line.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            if message.get("id") != request_id:
                continue
            if "error" in message:
                error = message["error"] or {}
                raise McpError(f"MCP 错误：{error.get('message') or error}")
            return message.get("result", {})

    def _sse_headers(self) -> dict[str, str]:
        return {
            "Accept": "text/event-stream",
            **{str(k): str(v) for k, v in _json(self.server.headers_json, {}).items()},
        }

    async def _sse_notify(self, payload: dict[str, Any]) -> None:
        endpoint = await self._sse_endpoint()
        try:
            await self._http.post(
                endpoint,
                json=payload,
                headers={"Content-Type": "application/json", **self._sse_headers()},
            )
        except httpx.HTTPError as exc:
            raise McpError(f"MCP SSE 通知失败：{type(exc).__name__}") from exc

    async def _sse_endpoint(self) -> str:
        try:
            async with self._http.stream("GET", self.server.url, headers=self._sse_headers()) as response:
                if response.status_code >= 400:
                    raise McpError(f"MCP SSE HTTP {response.status_code}")
                async for line in response.aiter_lines():
                    if line.startswith("event:") and "endpoint" not in line:
                        continue
                    if line.startswith("data:"):
                        data = line[5:].strip()
                        if data.startswith("/"):
                            from urllib.parse import urljoin

                            return urljoin(self.server.url, data)
        except httpx.HTTPError as exc:
            raise McpError(f"MCP SSE 连接失败：{type(exc).__name__}") from exc
        raise McpError("MCP SSE 未返回 endpoint")

    async def _sse_request(self, payload: dict[str, Any], request_id: int) -> Any:
        from urllib.parse import urljoin

        headers = self._sse_headers()
        try:
            async with self._http.stream("GET", self.server.url, headers=headers) as response:
                if response.status_code >= 400:
                    raise McpError(f"MCP SSE HTTP {response.status_code}")
                endpoint = None
                posted = False
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        data = line[5:].strip()
                        if not data:
                            continue
                        if not posted and data.startswith("/"):
                            endpoint = urljoin(self.server.url, data)
                            await self._http.post(
                                endpoint,
                                json=payload,
                                headers={"Content-Type": "application/json", **headers},
                            )
                            posted = True
                            continue
                        if not posted:
                            continue
                        try:
                            message = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if message.get("id") != request_id:
                            continue
                        if "error" in message:
                            raise McpError(f"MCP 错误：{(message['error'] or {}).get('message')}")
                        return message.get("result", {})
                raise McpError("MCP SSE 响应结束但没有结果")
        except httpx.HTTPError as exc:
            raise McpError(f"MCP SSE 请求失败：{type(exc).__name__}") from exc

    async def _http_request(self, payload: dict[str, Any], request_id: int | None) -> Any:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **{str(k): str(v) for k, v in _json(self.server.headers_json, {}).items()},
        }
        if self._http_session_id:
            headers["Mcp-Session-Id"] = self._http_session_id
        url = self.server.url
        if not url:
            raise McpError("MCP HTTP URL 为空")
        try:
            response = await self._http.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise McpError(f"MCP HTTP 请求失败：{type(exc).__name__}") from exc
        session_id = response.headers.get("mcp-session-id")
        if session_id:
            self._http_session_id = session_id
        if response.status_code >= 400:
            raise McpError(f"MCP HTTP {response.status_code}: {response.text[:300]}")
        if request_id is None:
            return None
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            for line in response.text.splitlines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data:
                    continue
                try:
                    message = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if message.get("id") != request_id:
                    continue
                if "error" in message:
                    raise McpError(f"MCP 错误：{(message['error'] or {}).get('message')}")
                return message.get("result", {})
            raise McpError("MCP SSE 响应缺少结果")
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise McpError("MCP 返回了非 JSON 响应") from exc
        if isinstance(data, list):
            for message in data:
                if message.get("id") == request_id:
                    if "error" in message:
                        raise McpError(f"MCP 错误：{(message['error'] or {}).get('message')}")
                    return message.get("result", {})
            raise McpError("MCP 响应缺少结果")
        if data.get("id") != request_id:
            raise McpError("MCP 响应 ID 不匹配")
        if "error" in data:
            raise McpError(f"MCP 错误：{(data['error'] or {}).get('message')}")
        return data.get("result", {})


def shlex_split(command: str) -> list[str]:
    import shlex

    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


class McpService:
    def __init__(self, db: Database, config: AppConfig) -> None:
        self.db = db
        self.config = config
        self._clients: dict[str, McpClient] = {}

    async def list(self) -> list[McpServer]:
        async with self.db.session() as db:
            return list(
                (await db.execute(select(McpServer).order_by(McpServer.created_at.asc()))).scalars().all()
            )

    async def get(self, server_id: str) -> McpServer:
        async with self.db.session() as db:
            row = await db.get(McpServer, server_id)
            if row is None:
                raise LookupError("MCP 服务不存在")
            return row

    async def create(self, payload: dict[str, Any]) -> McpServer:
        transport = str(payload.get("transport") or "stdio")
        if transport not in {"stdio", "http", "sse"}:
            raise ValueError("transport 必须是 stdio/http/sse")
        async with self.db.session() as db:
            row = McpServer(
                name=str(payload.get("name") or "MCP"),
                description=str(payload.get("description") or ""),
                transport=transport,
                command=str(payload.get("command") or ""),
                args_json=json.dumps(payload.get("args") or []),
                url=str(payload.get("url") or ""),
                headers_json=json.dumps(payload.get("headers") or {}),
                env_json=json.dumps(payload.get("env") or {}),
                enabled=bool(payload.get("enabled", True)),
                timeout_seconds=int(payload.get("timeoutSeconds") or payload.get("timeout_seconds") or 30),
                scope=str(payload.get("scope") or "system"),
            )
            db.add(row)
            await db.commit()
            return row

    async def update(self, server_id: str, payload: dict[str, Any]) -> McpServer:
        async with self.db.session() as db:
            row = await db.get(McpServer, server_id)
            if row is None:
                raise LookupError("MCP 服务不存在")
            mapping = {
                "name": "name",
                "description": "description",
                "transport": "transport",
                "command": "command",
                "url": "url",
                "enabled": "enabled",
                "timeoutSeconds": "timeout_seconds",
                "timeout_seconds": "timeout_seconds",
                "scope": "scope",
            }
            for key, attribute in mapping.items():
                if key in payload:
                    value = payload[key]
                    setattr(row, attribute, value)
            if "args" in payload:
                row.args_json = json.dumps(payload["args"])
            if "headers" in payload:
                row.headers_json = json.dumps(payload["headers"])
            if "env" in payload:
                row.env_json = json.dumps(payload["env"])
            row.updated_at = utcnow()
            await db.commit()
            await self._close(server_id)
            return row

    async def delete(self, server_id: str) -> None:
        await self._close(server_id)
        async with self.db.session() as db:
            row = await db.get(McpServer, server_id)
            if row is None:
                raise LookupError("MCP 服务不存在")
            links = (
                (await db.execute(select(SessionMcp).where(SessionMcp.server_id == server_id)))
                .scalars()
                .all()
            )
            for link in links:
                await db.delete(link)
            await db.delete(row)
            await db.commit()

    async def _client(self, server: McpServer) -> McpClient:
        client = self._clients.get(server.id)
        if client is None:
            client = McpClient(server, self.config)
            self._clients[server.id] = client
        return client

    async def _close(self, server_id: str) -> None:
        client = self._clients.pop(server_id, None)
        if client:
            await client.close()

    async def test(self, server_id: str) -> dict[str, Any]:
        row = await self.get(server_id)
        client = await self._client(row)
        try:
            await self._close(server_id)
            client = await self._client(await self.get(server_id))
            tools = await client.list_tools()
            await self._save_tools(row.id, tools, status="connected", error=None)
            return {"ok": True, "tools": tools, "count": len(tools)}
        except Exception as exc:  # noqa: BLE001
            await self._save_tools(row.id, [], status="error", error=str(exc))
            return {"ok": False, "error": str(exc), "tools": []}

    async def refresh_tools(self, server_id: str) -> list[dict[str, Any]]:
        row = await self.get(server_id)
        try:
            client = await self._client(row)
            tools = await client.list_tools()
            await self._save_tools(row.id, tools, status="connected", error=None)
            return tools
        except Exception as exc:  # noqa: BLE001
            await self._save_tools(row.id, [], status="error", error=str(exc))
            raise McpError(str(exc)) from exc

    async def _save_tools(
        self, server_id: str, tools: list[dict[str, Any]], *, status: str, error: str | None
    ) -> None:
        async with self.db.session() as db:
            row = await db.get(McpServer, server_id)
            if row:
                row.tools_json = json.dumps(tools, ensure_ascii=False)
                row.last_status = status
                row.last_error = error
                row.updated_at = utcnow()
                await db.commit()

    async def enabled_for_session(self, session_id: str) -> list[McpServer]:
        async with self.db.session() as db:
            links = (
                (
                    await db.execute(
                        select(SessionMcp).where(
                            SessionMcp.session_id == session_id, SessionMcp.enabled.is_(True)
                        )
                    )
                )
                .scalars()
                .all()
            )
            if not links:
                return []
            ids = [link.server_id for link in links]
            return list(
                (
                    await db.execute(
                        select(McpServer).where(McpServer.id.in_(ids), McpServer.enabled.is_(True))
                    )
                )
                .scalars()
                .all()
            )

    async def list_for_session(self, session_id: str) -> list[dict[str, Any]]:
        servers = await self.list()
        enabled_ids = {server.id for server in await self.enabled_for_session(session_id)}
        return [mcp_dto(server) | {"sessionEnabled": server.id in enabled_ids} for server in servers]

    async def set_session_enabled(self, session_id: str, server_id: str, enabled: bool) -> None:
        await self.get(server_id)
        async with self.db.session() as db:
            link = (
                (
                    await db.execute(
                        select(SessionMcp).where(
                            SessionMcp.session_id == session_id, SessionMcp.server_id == server_id
                        )
                    )
                )
                .scalars()
                .first()
            )
            if link is None:
                db.add(SessionMcp(session_id=session_id, server_id=server_id, enabled=enabled))
            else:
                link.enabled = enabled
            await db.commit()

    async def tools_for_session(self, session_id: str) -> list[dict[str, Any]]:
        result = []
        for server in await self.enabled_for_session(session_id):
            tools = _json(server.tools_json, [])
            if not tools:
                try:
                    tools = await self.refresh_tools(server.id)
                except McpError:
                    continue
            for tool in tools:
                name = f"mcp__{server.id}__{tool.get('name')}"
                result.append(
                    {
                        "type": "function",
                        "function": {
                            "name": name,
                            "description": str(tool.get("description") or f"MCP {server.name}"),
                            "parameters": tool.get("inputSchema")
                            or {"type": "object", "properties": {}, "additionalProperties": False},
                        },
                        "_mcp": server.id,
                        "_tool": tool.get("name"),
                    }
                )
        return sorted(result, key=lambda item: item["function"]["name"])

    async def metadata_for_session(self, session_id: str) -> list[dict[str, Any]]:
        return [
            {"id": server.id, "name": server.name, "description": server.description}
            for server in await self.enabled_for_session(session_id)
        ]

    async def call(self, session_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        parts = name.split("__")
        if len(parts) != 3 or parts[0] != "mcp":
            raise McpError("MCP 工具名非法")
        server_id, tool_name = parts[1], parts[2]
        allowed = {server.id for server in await self.enabled_for_session(session_id)}
        if server_id not in allowed:
            raise PermissionError("MCP 服务未在当前会话启用")
        row = await self.get(server_id)
        client = await self._client(row)
        try:
            result = await client.call_tool(tool_name, arguments)
            return {"result": result, "untrusted": True, "server": server_id}
        except Exception as exc:  # noqa: BLE001
            await self._close(server_id)
            raise McpError(f"MCP 调用失败：{exc}") from exc

    async def shutdown(self) -> None:
        for server_id in list(self._clients):
            await self._close(server_id)
