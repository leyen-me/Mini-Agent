"""Configuration layering, schema validation and secret hygiene."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from mini_agent.config import (
    AppConfig,
    DefaultsConfig,
    ModelConfig,
    ProviderConfig,
    ResolutionError,
    deep_merge,
    load_config,
    sanitize_config,
)


def test_deep_merge_is_recursive_and_does_not_mutate_inputs():
    base = {"a": {"b": 1, "c": 2}, "list": [1, 2]}
    override = {"a": {"b": 9}, "list": [3]}
    merged = deep_merge(base, override)
    assert merged == {"a": {"b": 9, "c": 2}, "list": [3]}
    assert base == {"a": {"b": 1, "c": 2}, "list": [1, 2]}


def test_file_then_workspace_local_precedence(tmp_path: Path, monkeypatch):
    project = tmp_path / "mini-agent.yaml"
    project.write_text(
        """
general:
  language: en-US
  theme: dark
defaults:
  model: mock/echo
  agent: build
providers:
  - id: mock
    name: Mock
    protocol: mock
    models:
      - id: echo
        contextWindow: 8000
""",
        encoding="utf-8",
    )
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "workspace.yaml").write_text(
        """
general:
  language: zh-CN
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("MINI_AGENT_CONFIG", str(project))
    monkeypatch.setenv("MINI_AGENT_DATA_DIR", str(data_dir))
    monkeypatch.delenv("MINI_AGENT_WORKSPACE_ROOT", raising=False)
    config = load_config()
    assert config.general.language == "zh-CN"
    assert config.general.theme == "dark"
    assert config.workspace_root == (data_dir / "workspaces" / "demo").resolve()
    assert config.source.endswith("workspace.yaml")


def test_env_interpolation_and_secret_file_never_serialized(tmp_path: Path, monkeypatch):
    secret = "sk-test-secret-value-99887766"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "secrets.yaml").write_text(f"providers:\n  deepseek: {secret}\n", encoding="utf-8")
    project = tmp_path / "config.yaml"
    project.write_text(
        """
general:
  language: zh-CN
providers:
  - id: deepseek
    name: DeepSeek
    protocol: openai-chat
    baseUrl: https://api.deepseek.com
    apiKey: ${env:MINI_AGENT_TEST_KEY}
    models:
      - id: deepseek-chat
        contextWindow: 128000
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("MINI_AGENT_CONFIG", str(project))
    monkeypatch.setenv("MINI_AGENT_DATA_DIR", str(data_dir))
    monkeypatch.setenv("MINI_AGENT_TEST_KEY", "env-key-should-not-win")
    config = load_config()
    assert config.providers[0].api_key == secret
    public = sanitize_config(config)
    serialized = str(public)
    assert secret not in serialized
    assert "env-key-should-not-win" not in serialized
    assert public["providers"][0]["key_hint"].endswith("7766")


def test_invalid_config_file_fails_fast(tmp_path: Path, monkeypatch):
    path = tmp_path / "bad.yaml"
    path.write_text("general: [not-an-object]\n", encoding="utf-8")
    monkeypatch.setenv("MINI_AGENT_CONFIG", str(path))
    monkeypatch.setenv("MINI_AGENT_DATA_DIR", str(tmp_path / "data"))
    with pytest.raises((ResolutionError, ValueError)):
        load_config()


def test_example_config_matches_schema_and_loads():
    root = Path(__file__).resolve().parents[2]
    schema = json.loads((root / "config" / "config.schema.json").read_text(encoding="utf-8"))
    example = yaml.safe_load((root / "config" / "mini-agent.example.yaml").read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(example), key=lambda error: list(error.path))
    assert errors == []
    config = load_config(root / "config" / "mini-agent.example.yaml")
    assert config.defaults.model == "mock/echo"
    assert config.providers[0].protocol == "mock"


def test_duplicate_default_model_falls_back_at_app_level():
    config = AppConfig(
        defaults=DefaultsConfig(model="missing/model"),
        providers=[
            ProviderConfig(
                id="mock",
                protocol="mock",
                models=[ModelConfig(id="echo")],
            )
        ],
    )
    assert config.defaults.model == "mock/echo"


def test_provider_custom_header_names_are_preserved(tmp_path):
    from mini_agent.config import normalize_config_keys

    raw = {
        "providers": [
            {
                "id": "custom",
                "headers": {"X-Api-Key": "secret", "X-Custom-Trace": "on"},
            }
        ]
    }
    normalized = normalize_config_keys(raw)
    assert normalized["providers"][0]["headers"] == {
        "X-Api-Key": "secret",
        "X-Custom-Trace": "on",
    }


def test_example_config_has_no_duplicate_top_level_keys():
    import re

    root = Path(__file__).resolve().parents[2]
    text = (root / "config" / "mini-agent.example.yaml").read_text(encoding="utf-8")
    keys = [match.group(1) for match in re.finditer(r"^([A-Za-z_][\w-]*):", text, re.M)]
    assert len(keys) == len(set(keys)), f"duplicate top-level keys: {keys}"


def test_invalid_policy_fails_closed(tmp_path):
    from conftest import make_config

    from mini_agent.config import apply_policy

    config = make_config(tmp_path)
    policy = tmp_path / "policy.yaml"
    policy.write_text("budget:\n  maxStepsPerTurn: not-a-number\n", encoding="utf-8")
    config.policy_path = policy
    with pytest.raises(ValueError):
        apply_policy(config)


def test_migrations_upgrade_existing_rows_to_head(tmp_path, monkeypatch):
    import sqlite3

    from alembic import command
    from alembic.config import Config as AlembicConfig

    from mini_agent.db import BACKEND_ROOT

    db_path = tmp_path / "existing.sqlite3"
    monkeypatch.setenv("MINI_AGENT_DB_URL", f"sqlite:///{db_path}")
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "934e51425f68")
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "insert into workspace (id,name,root_path,owner_id,created_at) values (?,?,?,?,?)",
            ("ws_old", "Old", str(tmp_path / "workspace"), "owner", "2026-01-01"),
        )
        connection.commit()
    finally:
        connection.close()
    command.upgrade(cfg, "head")
    connection = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(workspace)")}
        assert "alias" in columns
        assert connection.execute("select name from workspace where id='ws_old'").fetchone() == ("Old",)
        version = connection.execute("select version_num from alembic_version").fetchone()[0]
        assert version != "934e51425f68"
    finally:
        connection.close()
