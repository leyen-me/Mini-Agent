"""Tests for provider/agent/skill/MCP/plugin/upload/checkpoint/ops extensions."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from conftest import FakeResponse, make_config, run_harness
from sqlalchemy import select

from mini_agent.config import CommandPermission, PermissionConfig
from mini_agent.models import Message


@pytest.mark.asyncio
async def test_provider_crud_and_secret_masking(tmp_path: Path):
    async with run_harness(tmp_path) as h:
        response = await h.client.get("/api/v1/providers")
        assert response.status_code == 200
        providers = response.json()["data"]
        assert any(item["id"] == "mock" for item in providers)
        created = await h.client.post(
            "/api/v1/providers",
            json={
                "id": "testprov",
                "name": "Test",
                "protocol": "openai-chat",
                "baseUrl": "https://api.example.com/v1",
                "apiKey": "sk-provider-secret-12345",
                "models": [{"id": "chat", "name": "Chat", "contextWindow": 8000}],
            },
        )
        assert created.status_code == 200, created.text
        payload = (await h.client.get("/api/v1/providers")).json()["data"]
        provider = next(item for item in payload if item["id"] == "testprov")
        assert provider["key_hint"].endswith("2345")
        assert "sk-provider-secret" not in json.dumps(payload)
        assert provider["models"][0]["id"] == "chat"
        # API key update and model add.
        updated = await h.client.patch(
            "/api/v1/providers/testprov", json={"enabled": False, "name": "Disabled"}
        )
        assert updated.status_code == 200
        model = await h.client.post(
            "/api/v1/providers/testprov/models",
            json={
                "id": "chat2",
                "name": "Chat 2",
                "capabilities": ["text", "image"],
                "reasoningLevels": ["off", "low", "high"],
            },
        )
        assert model.status_code == 200, model.text
        provider = next(
            item
            for item in (await h.client.get("/api/v1/providers")).json()["data"]
            if item["id"] == "testprov"
        )
        saved = next(item for item in provider["models"] if item["id"] == "chat2")
        assert saved["reasoning_levels"] == ["off", "low", "high"]
        delete = await h.client.delete("/api/v1/providers/testprov")
        assert delete.status_code == 200


@pytest.mark.asyncio
async def test_agent_crud_and_session_defaults(tmp_path: Path):
    async with run_harness(tmp_path, script=[FakeResponse(content="ok")]) as h:
        response = await h.client.get("/api/v1/agents")
        assert any(item["id"] == "build" for item in response.json()["data"])
        created = await h.client.post(
            "/api/v1/agents",
            json={
                "name": "Reviewer",
                "systemPrompt": "只读审查",
                "readOnly": True,
                "tools": ["read_file"],
                "skills": ["code-review"],
                "permissions": {"shell": {"default": "deny"}},
            },
        )
        assert created.status_code == 200, created.text
        agent = created.json()["data"]["agent"]
        assert agent["readOnly"] is True
        assert agent["tools"] == ["read_file"]
        exported = await h.client.get(f"/api/v1/agents/{agent['id']}/export")
        assert exported.status_code == 200
        duplicate = await h.client.post(f"/api/v1/agents/{agent['id']}/duplicate")
        assert duplicate.status_code == 200
        session_id = await h.create_session(agentId=agent["id"])
        snapshot = await h.snapshot(session_id)
        assert snapshot["session"]["agentId"] == agent["id"]
        assert snapshot["session"]["mode"] == "plan"
        assert any(item["name"] == "code-review" and item["enabled"] for item in snapshot["skills"])
        deleted = await h.client.delete(f"/api/v1/agents/{agent['id']}")
        assert deleted.status_code == 422  # in use by session
        from_session = await h.client.post(f"/api/v1/agents/from-session/{session_id}")
        assert from_session.status_code == 200


@pytest.mark.asyncio
async def test_custom_command_crud_and_render(tmp_path: Path):
    async with run_harness(tmp_path) as h:
        created = await h.client.post(
            "/api/v1/commands",
            json={
                "name": "review",
                "description": "review a file",
                "promptTemplate": "请审查 $1，重点：$ARGUMENTS",
            },
        )
        assert created.status_code == 200, created.text
        command = created.json()["data"]["command"]
        rendered = await h.client.post(
            f"/api/v1/commands/{command['id']}/render", json={"arguments": "a.py 安全 性能"}
        )
        assert rendered.status_code == 200
        assert "a.py" in rendered.json()["data"]["prompt"]
        resolved = await h.client.post("/api/v1/commands/resolve", json={"text": "/review b.py 权限"})
        assert resolved.status_code == 200
        assert "b.py" in resolved.json()["data"]["prompt"]


@pytest.mark.asyncio
async def test_upload_text_and_attachment_message(tmp_path: Path):
    async with run_harness(tmp_path, script=[FakeResponse(content="收到附件")]) as h:
        session_id = await h.create_session()
        upload = await h.client.post(
            "/api/v1/uploads",
            files={"file": ("note.txt", b"attachment body", "text/plain")},
            data={"session_id": session_id},
        )
        assert upload.status_code == 200, upload.text
        upload_id = upload.json()["data"]["upload"]["id"]
        await h.send(session_id, "看看附件", attachmentIds=[upload_id])
        await h.wait_idle(session_id)
        async with h.app.state.services.db.session() as db:
            rows = (await db.execute(select(Message).where(Message.session_id == session_id))).scalars().all()
        user = next(row for row in rows if row.role == "user")
        content = json.loads(user.content_json)
        assert content["attachments"][0]["filename"] == "note.txt"
        assert "attachment body" in json.loads(user.raw_json)["content"]


@pytest.mark.asyncio
async def test_file_checkpoint_undo_redo(tmp_path: Path):
    config = make_config(tmp_path)
    config.permissions = PermissionConfig(write=CommandPermission(default="allow"))
    script = [
        FakeResponse(tool_calls=[("write_file", {"path": "note.txt", "content": "version one\n"})]),
        FakeResponse(content="written"),
    ]
    async with run_harness(tmp_path, script=script, config=config) as h:
        session_id = await h.create_session()
        await h.send(session_id, "写文件")
        snapshot = await h.wait_idle(session_id)
        turn = snapshot["turns"][-1]
        note = Path(config.workspace_root) / "note.txt"
        assert note.read_text() == "version one\n"
        changes = await h.client.get(f"/api/v1/sessions/{session_id}/checkpoints?turn_id={turn['id']}")
        assert changes.status_code == 200
        assert changes.json()["data"][0]["operation"] == "write"
        undo = await h.client.post(f"/api/v1/sessions/{session_id}/turns/{turn['id']}/undo")
        assert undo.status_code == 200, undo.text
        assert not note.exists()
        redo = await h.client.post(f"/api/v1/sessions/{session_id}/turns/{turn['id']}/redo")
        assert redo.status_code == 200
        assert note.read_text() == "version one\n"
        diff = await h.client.get(f"/api/v1/checkpoints/{changes.json()['data'][0]['id']}/diff")
        assert diff.status_code == 200
        assert "version one" in diff.json()["data"]["diff"]


@pytest.mark.asyncio
async def test_session_export_import_and_delete(tmp_path: Path):
    async with run_harness(tmp_path, script=[FakeResponse(content="hello export")]) as h:
        session_id = await h.create_session()
        await h.send(session_id, "导出我")
        await h.wait_idle(session_id)
        exported = await h.client.get(f"/api/v1/sessions/{session_id}/export?format=json")
        assert exported.status_code == 200
        data = exported.json()
        assert data["formatVersion"] == 2
        assert data["rawMessages"]
        imported = await h.client.post("/api/v1/sessions/import", json={"data": data})
        assert imported.status_code == 200, imported.text
        new_id = imported.json()["data"]["session"]["id"]
        snapshot = await h.snapshot(new_id)
        assert any("hello export" in message["content"]["text"] for message in snapshot["messages"])
        deleted = await h.client.delete(f"/api/v1/sessions/{new_id}?confirm=true")
        assert deleted.status_code == 200
        missing = await h.client.get(f"/api/v1/sessions/{new_id}")
        assert missing.status_code == 404


class _WebhookHandler(BaseHTTPRequestHandler):
    received: list[dict] = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length", 0))
        body = self.rfile.read(length)
        self.__class__.received.append(
            {"body": body.decode(), "signature": self.headers.get("x-mini-agent-signature")}
        )
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):  # noqa: ANN002
        return


@pytest.mark.asyncio
async def test_webhook_delivery_and_backup(tmp_path: Path):
    _WebhookHandler.received = []
    config = make_config(tmp_path)
    config.security.block_private_network = False
    server = ThreadingHTTPServer(("127.0.0.1", 0), _WebhookHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        async with run_harness(tmp_path, script=[FakeResponse(content="webhook done")], config=config) as h:
            created = await h.client.post(
                "/api/v1/webhooks",
                json={
                    "name": "test",
                    "url": f"http://127.0.0.1:{port}/hook",
                    "events": ["turn.completed"],
                    "secret": "hook-secret",
                },
            )
            assert created.status_code == 200, created.text
            session_id = await h.create_session()
            await h.send(session_id, "触发 webhook")
            await h.wait_idle(session_id)
            for _ in range(50):
                if _WebhookHandler.received:
                    break
                import asyncio

                await asyncio.sleep(0.05)
            assert _WebhookHandler.received
            assert _WebhookHandler.received[0]["signature"].startswith("sha256=")
            backup = await h.client.post("/api/v1/backups", json={})
            assert backup.status_code == 200, backup.text
            backup_id = backup.json()["data"]["id"]
            download = await h.client.get(f"/api/v1/backups/{backup_id}/download")
            assert download.status_code == 200
            assert download.content[:2] == b"PK"
            restored = await h.client.post("/api/v1/backups/restore", json={"backupId": backup_id})
            assert restored.status_code == 200
            assert restored.json()["data"]["requiresRestart"] is True
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.asyncio
async def test_external_file_modification_blocks_overwrite(tmp_path: Path):
    config = make_config(tmp_path)
    config.permissions = PermissionConfig(write=CommandPermission(default="allow"))
    script = [
        FakeResponse(tool_calls=[("write_file", {"path": "note.txt", "content": "one\n"})]),
        FakeResponse(content="written"),
        FakeResponse(
            tool_calls=[("edit_file", {"path": "note.txt", "old_string": "one", "new_string": "two"})]
        ),
        FakeResponse(content="edited"),
    ]
    async with run_harness(tmp_path, script=script, config=config) as h:
        session_id = await h.create_session()
        await h.send(session_id, "write")
        await h.wait_idle(session_id)
        note = Path(config.workspace_root) / "note.txt"
        note.write_text("user changed\n")
        await h.send(session_id, "edit")
        snapshot = await h.wait_idle(session_id)
        calls = [call for call in snapshot["toolCalls"] if call["name"] == "edit_file"]
        assert calls
        assert calls[0]["output"]["code"] != 0
        assert calls[0]["output"]["errorCode"] == "CONFLICT"
        assert note.read_text() == "user changed\n"


@pytest.mark.asyncio
async def test_workspace_ignores_gitignored_files(tmp_path: Path):
    async with run_harness(tmp_path) as h:
        workspace_id = (await h.client.get("/api/v1/workspaces")).json()["data"][0]["id"]
        root = Path(h.config.workspace_root)
        (root / ".gitignore").write_text("secret/\n*.tmp\n")
        (root / "secret").mkdir()
        (root / "secret" / "hidden.txt").write_text("hidden")
        (root / "keep.txt").write_text("visible")
        (root / "drop.tmp").write_text("drop")
        listing = await h.client.get(f"/api/v1/workspaces/{workspace_id}/files?path=.")
        names = {entry["name"] for entry in listing.json()["data"]["entries"]}
        assert "keep.txt" in names
        assert "drop.tmp" not in names
        assert "secret" not in names


def test_ddg_search_parser_extracts_results():
    from mini_agent.tools import _parse_ddg

    page = """
    <a class="result__a" href="https://example.com/a">Example <b>A</b></a>
    <a class="result__snippet" href="https://example.com/a">First snippet</a>
    <a class="result__a" href="https://example.com/b">Example B</a>
    <a class="result__snippet" href="https://example.com/b">Second snippet</a>
    """
    results = _parse_ddg(page, 2)
    assert results[0]["title"] == "Example A"
    assert results[0]["url"] == "https://example.com/a"
    assert results[1]["snippet"] == "Second snippet"


@pytest.mark.asyncio
async def test_parallel_readonly_tools_execute_and_persist_results(tmp_path: Path):
    config = make_config(tmp_path)
    Path(config.workspace_root).mkdir(parents=True, exist_ok=True)
    (Path(config.workspace_root) / "a.txt").write_text("aaa")
    (Path(config.workspace_root) / "b.txt").write_text("bbb")
    script = [
        FakeResponse(tool_calls=[("read_file", {"path": "a.txt"}), ("read_file", {"path": "b.txt"})]),
        FakeResponse(content="read both"),
    ]
    async with run_harness(tmp_path, script=script, config=config) as h:
        session_id = await h.create_session()
        await h.send(session_id, "read both")
        snapshot = await h.wait_idle(session_id)
        calls = [call for call in snapshot["toolCalls"] if call["name"] == "read_file"]
        assert len(calls) == 2
        assert all(call["status"] == "succeeded" for call in calls)
        assert {call["output"]["data"]["content"] for call in calls} == {"aaa", "bbb"}


@pytest.mark.asyncio
async def test_switching_agent_rebuilds_prompt_and_plan_mode(tmp_path: Path):
    async with run_harness(tmp_path, script=[FakeResponse(content="ok")]) as h:
        session_id = await h.create_session()
        before = await h.snapshot(session_id)
        assert "Build 模式" in before["systemPrompt"]
        updated = await h.client.patch(f"/api/v1/sessions/{session_id}", json={"agentId": "plan"})
        assert updated.status_code == 200, updated.text
        after = await h.snapshot(session_id)
        assert after["session"]["agentId"] == "plan"
        assert after["session"]["mode"] == "plan"
        assert "Plan 模式" in after["systemPrompt"]


@pytest.mark.asyncio
async def test_write_file_runs_auto_formatter_and_records_formatted_diff(tmp_path: Path):
    config = make_config(tmp_path)
    config.permissions = PermissionConfig(write=CommandPermission(default="allow"))
    config.tools.format.commands[".txt"] = (
        "python3 -c \"from pathlib import Path; p=Path('{path}'); p.write_text(p.read_text().upper())\""
    )
    script = [
        FakeResponse(tool_calls=[("write_file", {"path": "note.txt", "content": "hello\n"})]),
        FakeResponse(content="done"),
    ]
    async with run_harness(tmp_path, script=script, config=config) as h:
        session_id = await h.create_session()
        await h.send(session_id, "write")
        snapshot = await h.wait_idle(session_id)
        note = Path(config.workspace_root) / "note.txt"
        assert note.read_text() == "HELLO\n"
        call = next(call for call in snapshot["toolCalls"] if call["name"] == "write_file")
        assert call["output"]["code"] == 0
        assert call["output"]["data"]["format"]["ok"] is True
        assert "HELLO" in call["output"]["data"]["diff"]


@pytest.mark.asyncio
async def test_provider_balance_normalizes_and_uses_network_config(tmp_path: Path):
    from types import SimpleNamespace

    import mini_agent.providers as providers_module

    captured: dict[str, object] = {}

    class FakeHTTPResponse:
        status_code = 200

        def json(self):
            return {
                "is_available": True,
                "balance_infos": [
                    {
                        "currency": "CNY",
                        "total_balance": "12.5",
                        "granted_balance": "2",
                        "topped_up_balance": "10.5",
                    }
                ],
            }

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, headers=None):
            captured["url"] = url
            captured["headers"] = headers
            return FakeHTTPResponse()

    config = make_config(tmp_path)
    config.providers[0].supports_balance = True
    config.providers[0].base_url = "https://mock.example.com"
    config.providers[0].balance_path = "/user/balance"
    config.network.proxy = "http://127.0.0.1:7890"
    config.network.ca_bundle = "/tmp/ca.pem"
    original = providers_module.httpx
    providers_module.httpx = SimpleNamespace(AsyncClient=FakeClient)  # type: ignore[assignment]
    try:
        async with run_harness(tmp_path, script=[FakeResponse(content="ok")], config=config) as h:
            response = await h.client.get("/api/v1/providers/mock/balance")
            assert response.status_code == 200, response.text
            data = response.json()["data"]
            assert data["currency"] == "CNY"
            assert data["total"] == 12.5
            assert data["available"] is True
            assert captured["proxy"] == "http://127.0.0.1:7890"
            assert captured["verify"] == "/tmp/ca.pem"
    finally:
        providers_module.httpx = original  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_web_search_tool_uses_configured_provider_and_parses_results(tmp_path: Path):
    from types import SimpleNamespace

    import httpx as real_httpx

    import mini_agent.tools as tools_module

    html = (
        '<a class="result__a" href="https://example.com/result">Result title</a>'
        '<a class="result__snippet" href="https://example.com/result">Result snippet</a>'
    )

    class FakeHTTPResponse:
        status_code = 200
        text = html

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, **kwargs):
            return FakeHTTPResponse()

    config = make_config(tmp_path)
    config.permissions = PermissionConfig(network=CommandPermission(default="allow"))
    script = [
        FakeResponse(tool_calls=[("web_search", {"query": "mini agent"})]),
        FakeResponse(content="done"),
    ]
    original = tools_module.httpx
    tools_module.httpx = SimpleNamespace(
        AsyncClient=FakeClient,
        HTTPError=real_httpx.HTTPError,
        Timeout=real_httpx.Timeout,
    )  # type: ignore[assignment]
    try:
        async with run_harness(tmp_path, script=script, config=config) as h:
            session_id = await h.create_session()
            await h.send(session_id, "search")
            snapshot = await h.wait_idle(session_id)
            call = next(call for call in snapshot["toolCalls"] if call["name"] == "web_search")
            assert call["output"]["code"] == 0
            assert call["output"]["data"]["results"][0]["title"] == "Result title"
            assert call["output"]["data"]["results"][0]["url"] == "https://example.com/result"
    finally:
        tools_module.httpx = original  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_truncated_model_output_is_not_presented_as_complete(tmp_path: Path):
    script = [FakeResponse(content="partial", finish_reason="length")]
    async with run_harness(tmp_path, script=script) as h:
        session_id = await h.create_session()
        await h.send(session_id, "long answer")
        snapshot = await h.wait_idle(session_id)
        assert snapshot["messages"][-1]["role"] == "system"
        assert "最大 token" in snapshot["messages"][-1]["content"]["text"]


@pytest.mark.asyncio
async def test_webhook_rejects_private_target_by_default(tmp_path: Path):
    async with run_harness(tmp_path) as h:
        response = await h.client.post(
            "/api/v1/webhooks",
            json={"name": "private", "url": "http://127.0.0.1:9/hook", "events": ["*"]},
        )
        assert response.status_code == 422
