from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from conftest import FakeResponse, make_config, run_harness

from mini_agent.config import CommandPermission, PermissionConfig

SERVER_SCRIPT = """\
import json
import sys

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    request = json.loads(line)
    method = request.get("method")
    if "id" not in request:
        continue
    if method == "initialize":
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake", "version": "1.0"},
        }
    elif method == "tools/list":
        result = {
            "tools": [
                {
                    "name": "echo",
                    "description": "Echo text",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    },
                }
            ]
        }
    elif method == "tools/call":
        result = {
            "content": [{"type": "text", "text": "mcp-ok:" + request["params"]["arguments"].get("text", "")}],
            "isError": False,
        }
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
"""


@pytest.mark.asyncio
async def test_mcp_stdio_server_and_runtime_tool(tmp_path: Path):
    script = tmp_path / "mcp_server.py"
    script.write_text(SERVER_SCRIPT, encoding="utf-8")
    config = make_config(tmp_path)
    config.permissions = PermissionConfig(mcp=CommandPermission(default="allow"))
    responses = []
    async with run_harness(tmp_path, script=responses, config=config) as h:
        created = await h.client.post(
            "/api/v1/mcp/servers",
            json={
                "name": "Fake",
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(script)],
            },
        )
        assert created.status_code == 200, created.text
        server = created.json()["data"]["server"]
        tool_name = f"mcp__{server['id']}__echo"
        tested = await h.client.post(f"/api/v1/mcp/servers/{server['id']}/test")
        assert tested.status_code == 200, tested.text
        assert tested.json()["data"]["tools"][0]["name"] == "echo"
        session_id = await h.create_session()
        enabled = await h.client.post(
            f"/api/v1/sessions/{session_id}/mcp/{server['id']}/toggle",
            json={"enabled": True},
        )
        assert enabled.status_code == 200, enabled.text
        h.gateway.script = [
            FakeResponse(tool_calls=[(tool_name, {"text": "hello"})]),
            FakeResponse(content="done"),
        ]
        h.gateway._calls[session_id] = 0
        await h.send(session_id, "调用 MCP")
        snapshot = await h.wait_idle(session_id)
        tool_calls = [call for call in snapshot["toolCalls"] if call["name"] == tool_name]
        assert tool_calls
        output = tool_calls[0]["output"]
        assert output["code"] == 0
        assert "mcp-ok:hello" in json.dumps(output, ensure_ascii=False)
