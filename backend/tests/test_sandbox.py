from __future__ import annotations

from pathlib import Path

from conftest import make_config

from mini_agent.sandbox import select_sandbox, wrap_shell


def test_sandbox_selection_and_none_mode(tmp_path: Path):
    config = make_config(tmp_path)
    config.sandbox.mode = "none"
    wrapped = wrap_shell(config, "echo hello", tmp_path)
    assert wrapped.mode == "none"
    assert wrapped.active is False
    assert wrapped.command == "echo hello"


def test_sandbox_auto_wraps_when_runtime_available(tmp_path: Path):
    config = make_config(tmp_path)
    config.sandbox.mode = "auto"
    selected = select_sandbox(config)
    wrapped = wrap_shell(config, "echo hello", tmp_path)
    assert wrapped.mode == selected
    if selected == "none":
        assert wrapped.active is False
        assert wrapped.warning
    else:
        assert wrapped.active is True
        assert "echo hello" in wrapped.command
