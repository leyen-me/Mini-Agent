from __future__ import annotations

from pathlib import Path

import pytest
from conftest import FakeResponse, run_harness


@pytest.mark.asyncio
async def test_metrics_and_log_export(tmp_path: Path):
    async with run_harness(tmp_path, script=[FakeResponse(content="ok")]) as h:
        session_id = await h.create_session()
        await h.send(session_id, "hello")
        await h.wait_idle(session_id)
        metrics = await h.client.get("/metrics")
        assert metrics.status_code == 200
        assert "mini_agent_sessions_total" in metrics.text
        assert "mini_agent_input_tokens_total" in metrics.text
        logs = await h.client.get("/api/v1/system/logs/export")
        assert logs.status_code == 200
        assert logs.content[:2] == b"PK"
        cleanup = await h.client.post("/api/v1/system/retention/cleanup")
        assert cleanup.status_code == 200
        assert "events" in cleanup.json()["data"]
        policy = await h.client.get("/api/v1/system/policy")
        assert policy.status_code == 200


@pytest.mark.asyncio
async def test_policy_cannot_be_relaxed_by_runtime_config_patch(tmp_path: Path):
    from conftest import FakeResponse, make_config, run_harness

    config = make_config(tmp_path)
    policy = tmp_path / "policy.yaml"
    policy.write_text("sandbox:\n  mode: none\n", encoding="utf-8")
    config.policy_path = policy
    async with run_harness(tmp_path, script=[FakeResponse(content="ok")], config=config) as h:
        response = await h.client.patch(
            "/api/v1/config", json={"sandbox": {"mode": "auto", "allow_network": True}}
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["sandbox"]["mode"] == "none"


@pytest.mark.asyncio
async def test_workspace_alias_and_delete(tmp_path: Path):
    from conftest import FakeResponse, run_harness

    async with run_harness(tmp_path, script=[FakeResponse(content="ok")]) as h:
        created = await h.client.post("/api/v1/workspaces", json={"name": "Another"})
        assert created.status_code == 200, created.text
        workspace = created.json()["data"]
        updated = await h.client.patch(
            f"/api/v1/workspaces/{workspace['id']}",
            json={"alias": "secondary", "description": "ops workspace"},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["data"]["alias"] == "secondary"
        deleted = await h.client.delete(f"/api/v1/workspaces/{workspace['id']}?confirm=true")
        assert deleted.status_code == 200, deleted.text
        listing = await h.client.get("/api/v1/workspaces")
        assert all(item["id"] != workspace["id"] for item in listing.json()["data"])


def test_model_capability_inference():
    from mini_agent.providers import ProviderService

    assert "vision" in ProviderService._infer_capabilities("gpt-4o")
    assert "image" in ProviderService._infer_capabilities("claude-3-vision")
    assert "reasoning" in ProviderService._infer_capabilities("deepseek-r1")
    assert ProviderService._infer_capabilities("plain-model") == ["text"]
    assert ProviderService._infer_reasoning_levels("deepseek-flash") == [
        "off",
        "low",
        "medium",
        "high",
        "max",
    ]
    assert ProviderService._infer_reasoning_levels("plain-model") == ["off"]


def test_pending_restore_is_applied_before_startup(tmp_path: Path):
    import json
    import shutil

    from conftest import make_config

    from mini_agent.ops import apply_pending_restore

    config = make_config(tmp_path)
    data = config.data_dir
    data.mkdir(parents=True, exist_ok=True)
    import sqlite3

    old = data / "mini-agent.sqlite3"
    old_connection = sqlite3.connect(old)
    old_connection.execute("create table old_data (value text)")
    old_connection.execute("insert into old_data values ('old')")
    old_connection.commit()
    old_connection.close()
    staging = data / ".restore-staging" / "bak_test"
    staging.mkdir(parents=True)
    restored = staging / "mini-agent.sqlite3"
    restored_connection = sqlite3.connect(restored)
    restored_connection.execute("create table restored_data (value text)")
    restored_connection.execute("insert into restored_data values ('new')")
    restored_connection.commit()
    restored_connection.close()
    (staging / "manifest.json").write_text(
        json.dumps({"formatVersion": 2, "createdAt": "2026-01-01"}), encoding="utf-8"
    )
    (data / "restore-pending.json").write_text(
        json.dumps({"staging": str(staging), "backupId": "bak_test"}), encoding="utf-8"
    )
    assert apply_pending_restore(config) is True
    check = sqlite3.connect(data / "mini-agent.sqlite3")
    try:
        assert check.execute("select value from restored_data").fetchone() == ("new",)
    finally:
        check.close()
    assert not (data / "restore-pending.json").exists()
    assert list((data / "backups").glob("pre-migration-*.zip"))
    shutil.rmtree(staging, ignore_errors=True)


def test_invalid_restore_rolls_back_old_database(tmp_path: Path):
    import json
    import sqlite3

    from conftest import make_config

    from mini_agent.ops import apply_pending_restore

    config = make_config(tmp_path)
    data = config.data_dir
    data.mkdir(parents=True, exist_ok=True)
    old = data / "mini-agent.sqlite3"
    connection = sqlite3.connect(old)
    connection.execute("create table keep_me (value text)")
    connection.execute("insert into keep_me values ('still-here')")
    connection.commit()
    connection.close()
    staging = data / ".restore-staging" / "bad"
    staging.mkdir(parents=True)
    (staging / "mini-agent.sqlite3").write_bytes(b"not a sqlite database")
    (data / "restore-pending.json").write_text(json.dumps({"staging": str(staging)}), encoding="utf-8")
    assert apply_pending_restore(config) is False
    check = sqlite3.connect(data / "mini-agent.sqlite3")
    try:
        assert check.execute("select value from keep_me").fetchone() == ("still-here",)
    finally:
        check.close()


@pytest.mark.asyncio
async def test_metrics_can_be_disabled(tmp_path: Path):
    from conftest import FakeResponse, make_config, run_harness

    config = make_config(tmp_path)
    config.metrics.enabled = False
    async with run_harness(tmp_path, script=[FakeResponse(content="ok")], config=config) as h:
        response = await h.client.get("/metrics")
        assert response.status_code == 404
