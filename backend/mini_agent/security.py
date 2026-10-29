"""Security primitives: path guards, permission rules, SSRF protection and redaction."""

from __future__ import annotations

import asyncio
import fnmatch
import ipaddress
import re
import socket
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .config import CommandPermission, PermissionConfig

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret|password)\s*[\"']?\s*[:=]\s*[\"']?)([^\s\"',}]{6,})"),
]


class PathSecurityError(ValueError):
    """Raised when a requested path escapes the workspace."""


class UrlSecurityError(ValueError):
    """Raised when a URL is unsafe to fetch."""


@dataclass(frozen=True)
class PermissionDecision:
    action: str  # allow | ask | deny
    risk: str
    category: str
    resource: str
    reason: str


def resolve_workspace_path(workspace_root: Path, requested: str | Path) -> Path:
    """Resolve a user/model supplied path and reject traversal or symlink escapes."""

    root = workspace_root.expanduser().resolve()
    raw = str(requested or ".")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise PathSecurityError(f"路径 {raw!r} 超出工作区范围") from exc
    return resolved


def is_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve(strict=False).relative_to(root.resolve())
        return True
    except ValueError:
        return False


def split_shell_segments(command: str) -> list[str]:
    """Split on shell separators while respecting quotes.

    Every segment is permission-checked independently, so ``git status && rm -rf /``
    cannot slip through a rule that only allows ``git status``.
    """

    segments: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(command):
        char = command[index]
        if escaped:
            current.append(char)
            escaped = False
            index += 1
            continue
        if char == "\\" and quote != "'":
            current.append(char)
            escaped = True
            index += 1
            continue
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            index += 1
            continue
        if char in ("'", '"'):
            quote = char
            current.append(char)
            index += 1
            continue
        two = command[index : index + 2]
        if two in ("&&", "||"):
            segments.append("".join(current).strip())
            current = []
            index += 2
            continue
        if char in (";", "|", "\n"):
            segments.append("".join(current).strip())
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    segments.append("".join(current).strip())
    return [segment for segment in segments if segment]


def _matches(value: str, patterns: list[str]) -> bool:
    normalized = value.strip()
    for pattern in patterns:
        candidate = pattern.strip()
        if not candidate:
            continue
        if fnmatch.fnmatchcase(normalized, candidate):
            return True
        if candidate.endswith(" *") and normalized == candidate[:-2]:
            return True
        if candidate.endswith("*") and fnmatch.fnmatchcase(normalized, candidate[:-1] + "*"):
            return True
    return False


def _evaluate_rule(rule: CommandPermission, resource: str, *, multi: list[str] | None = None) -> str:
    values = multi or [resource]
    if any(_matches(value, rule.deny) for value in values):
        return "deny"
    if multi:
        if values and all(_matches(value, rule.allow) for value in values):
            return "allow"
    elif _matches(resource, rule.allow):
        return "allow"
    return rule.default


TOOL_CATEGORY = {
    "read_file": "read",
    "list_dir": "read",
    "glob": "read",
    "grep": "read",
    "read_image": "read",
    "write_file": "write",
    "edit_file": "write",
    "multi_edit": "write",
    "shell": "shell",
    "shell_background": "shell",
    "web_search": "network",
    "web_fetch": "network",
    "ask_question": "interaction",
    "todo_write": "interaction",
    "wait": "task",
    "task_status": "task",
    "task_cancel": "task",
    "load_skill": "read",
    "spawn_agent": "subagent",
}

RISK_BY_CATEGORY = {
    "read": "low",
    "write": "medium",
    "shell": "high",
    "network": "medium",
    "interaction": "low",
    "task": "low",
    "subagent": "high",
}

DANGEROUS_SHELL = [
    re.compile(r"(^|\s)rm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+(/|\*|~|\.\.)"),
    re.compile(r"(^|\s)(sudo|su|shutdown|reboot|mkfs|diskutil\s+erase|dd\s+if=)"),
    re.compile(r":\(\)\s*\{.*\};\s*:"),
]


class PermissionEvaluator:
    def __init__(self, config: PermissionConfig) -> None:
        self.config = config

    def category_rule(self, category: str) -> CommandPermission | None:
        return getattr(self.config, category, None)

    def evaluate(
        self,
        *,
        tool: str,
        resource: str,
        plan_mode: bool,
        grants: list[tuple[str, str]] | None = None,
    ) -> PermissionDecision:
        category = TOOL_CATEGORY.get(tool, "shell")
        risk = RISK_BY_CATEGORY.get(category, "medium")
        rule = self.category_rule(category)
        if rule is None:
            command_rule = self.config.shell
            segments = split_shell_segments(resource) or [resource]
            action = _evaluate_rule(command_rule, resource, multi=segments)
            return PermissionDecision(action, risk, category, resource, self._reason(action, category))

        if category == "shell":
            segments = split_shell_segments(resource)
            if any(pattern.search(segment) for segment in segments for pattern in DANGEROUS_SHELL):
                return PermissionDecision(
                    "deny", "high", category, resource, "该命令命中危险命令黑名单，已阻止执行"
                )
            action = _evaluate_rule(rule, resource, multi=segments)
        else:
            action = _evaluate_rule(rule, resource)

        if plan_mode and category in {"write", "shell", "subagent"}:
            return PermissionDecision("deny", risk, category, resource, "Plan 模式只读，禁止修改或执行命令")
        if category in {"write", "shell", "network", "subagent"} and action == "ask":
            for granted_tool, pattern in grants or []:
                if granted_tool == tool and _matches(resource, [pattern]):
                    return PermissionDecision("allow", risk, category, resource, "已命中「始终允许」规则")
        return PermissionDecision(action, risk, category, resource, self._reason(action, category))

    @staticmethod
    def _reason(action: str, category: str) -> str:
        if action == "allow":
            return "权限规则允许该操作"
        if action == "deny":
            return "权限规则禁止该操作"
        return f"{category} 类操作需要用户确认"


def _is_public_ip(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


async def assert_safe_url(url: str, *, block_private: bool = True) -> None:
    if not block_private:
        return
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise UrlSecurityError("仅允许 http/https 协议")
    if not parsed.hostname:
        raise UrlSecurityError("URL 缺少主机名")
    host = parsed.hostname
    if host.lower() in {"localhost", "metadata.google.internal"} or host.endswith(".localhost"):
        raise UrlSecurityError("禁止访问本机或云元数据地址")
    try:
        infos = await asyncio.to_thread(
            socket.getaddrinfo, host, parsed.port or (443 if parsed.scheme == "https" else 80)
        )
    except OSError as exc:
        raise UrlSecurityError(f"域名解析失败：{exc}") from exc
    for info in infos:
        if not _is_public_ip(info[4][0]):
            raise UrlSecurityError("禁止访问内网、回环或保留地址")


def redact_text(text: str, secrets: list[str] | None = None) -> str:
    redacted = text
    for secret in sorted(
        {item for item in (secrets or []) if item and len(item) >= 6}, key=len, reverse=True
    ):
        redacted = redacted.replace(secret, "[REDACTED]")
    for pattern in SECRET_PATTERNS:
        if pattern.groups >= 2:
            redacted = pattern.sub(lambda m: f"{m.group(1)}[REDACTED]", redacted)
        else:
            redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def redact_object(value, secrets: list[str] | None = None):
    if isinstance(value, str):
        return redact_text(value, secrets)
    if isinstance(value, list):
        return [redact_object(item, secrets) for item in value]
    if isinstance(value, dict):
        return {key: redact_object(item, secrets) for key, item in value.items()}
    return value
