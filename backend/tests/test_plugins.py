from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from conftest import run_harness


@pytest.mark.asyncio
async def test_plugin_install_trust_enable_commands_and_agents(tmp_path: Path):
    manifest = {
        "id": "demo",
        "name": "Demo Plugin",
        "version": "1.0.0",
        "description": "demo",
        "commands": [
            {"name": "demo", "description": "demo command", "promptTemplate": "运行 demo $ARGUMENTS"}
        ],
        "agents": [
            {
                "id": "helper",
                "name": "Demo Helper",
                "systemPrompt": "help",
                "tools": ["read_file"],
            }
        ],
        "tools": [],
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("plugin.json", json.dumps(manifest))
        archive.writestr("README.md", "demo")
    async with run_harness(tmp_path) as h:
        installed = await h.client.post(
            "/api/v1/plugins/install",
            files={"file": ("demo.zip", buffer.getvalue(), "application/zip")},
            data={"trusted": "false"},
        )
        assert installed.status_code == 200, installed.text
        assert installed.json()["data"]["plugin"]["trusted"] is False
        enabled = await h.client.post("/api/v1/plugins/demo/enable", json={"enabled": True})
        assert enabled.status_code == 422  # untrusted
        trusted = await h.client.post("/api/v1/plugins/demo/trust", json={"trusted": True})
        assert trusted.status_code == 200
        enabled = await h.client.post("/api/v1/plugins/demo/enable", json={"enabled": True})
        assert enabled.status_code == 200, enabled.text
        agents = (await h.client.get("/api/v1/agents")).json()["data"]
        assert any(agent["id"] == "plugin_demo_helper" for agent in agents)
        commands = (await h.client.get("/api/v1/commands")).json()["data"]
        assert any(item["pluginId"] == "demo" and item["name"] == "demo" for item in commands)
        resolved = await h.client.post("/api/v1/commands/resolve", json={"text": "/demo 参数"})
        assert resolved.status_code == 200
        assert "参数" in resolved.json()["data"]["prompt"]
