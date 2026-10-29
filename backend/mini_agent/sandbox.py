"""Optional OS-level sandbox for shell tools (sandbox-exec / bwrap / docker)."""

from __future__ import annotations

import os
import shlex
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import AppConfig


@dataclass(slots=True)
class SandboxCommand:
    command: str
    mode: str
    active: bool
    warning: str = ""


def select_sandbox(config: AppConfig) -> str:
    mode = config.sandbox.mode
    if mode == "none":
        return "none"
    if mode == "sandbox-exec":
        return "sandbox-exec" if shutil.which("sandbox-exec") else "none"
    if mode == "bwrap":
        return "bwrap" if shutil.which("bwrap") else "none"
    if mode == "docker":
        return "docker" if shutil.which("docker") else "none"
    if sys.platform == "darwin" and shutil.which("sandbox-exec"):
        return "sandbox-exec"
    if shutil.which("bwrap"):
        return "bwrap"
    if shutil.which("docker"):
        return "docker"
    return "none"


def wrap_shell(config: AppConfig, command: str, workspace_root: Path) -> SandboxCommand:
    mode = select_sandbox(config)
    root = str(workspace_root.resolve())
    if mode == "none":
        warning = (
            "" if config.sandbox.mode == "none" else "未找到可用沙箱运行时，命令将在普通工作区进程中执行"
        )
        return SandboxCommand(command=command, mode="none", active=False, warning=warning)
    if mode == "sandbox-exec":
        temp = str(Path(tempfile.gettempdir()).resolve())
        network = "(allow network*)" if config.sandbox.allow_network else "(deny network*)"
        profile = "\n".join(
            [
                "(version 1)",
                "(allow default)",
                "(deny file-write*)",
                f'(allow file-write* (subpath "{root}"))',
                f'(allow file-write* (subpath "{temp}"))',
                '(allow file-write* (literal "/dev/null"))',
                '(allow file-write* (literal "/dev/stdout"))',
                '(allow file-write* (literal "/dev/stderr"))',
                network,
            ]
        )
        wrapped = f"/usr/bin/sandbox-exec -p {shlex.quote(profile)} /bin/sh -c {shlex.quote(command)}"
        return SandboxCommand(command=wrapped, mode=mode, active=True)
    if mode == "bwrap":
        network = "" if config.sandbox.allow_network else "--unshare-net"
        wrapped = (
            "bwrap --ro-bind / / "
            f"--bind {shlex.quote(root)} {shlex.quote(root)} "
            "--tmpfs /tmp --proc /proc --dev /dev "
            f"{network} -- /bin/sh -c {shlex.quote(command)}"
        )
        return SandboxCommand(command=wrapped, mode=mode, active=True)
    if mode == "docker":
        network = "bridge" if config.sandbox.allow_network else "none"
        uid = os.getuid() if hasattr(os, "getuid") else 1000
        gid = os.getgid() if hasattr(os, "getgid") else 1000
        wrapped = (
            f"docker run --rm --network {network} --user {uid}:{gid} "
            f"-v {shlex.quote(root)}:/workspace -w /workspace "
            f"{shlex.quote(config.sandbox.docker_image)} /bin/sh -c {shlex.quote(command)}"
        )
        return SandboxCommand(command=wrapped, mode=mode, active=True)
    return SandboxCommand(command=command, mode="none", active=False, warning="沙箱不可用")
