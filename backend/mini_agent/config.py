"""Typed, layered application configuration.

Priority (lowest to highest): built-in defaults, config file, workspace-local file,
environment variables. Secrets are read from a separate YAML file (or referenced via
``${env:NAME}``) and are never serialized to API responses.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel


class ResolutionError(ValueError):
    """Raised when a configuration value cannot be resolved safely."""


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        alias_generator=to_camel,
        populate_by_name=True,
    )


class ModelPricing(StrictModel):
    input_per_million: float = 0.0
    output_per_million: float = 0.0
    cache_hit_per_million: float = 0.0


class ModelConfig(StrictModel):
    id: str = Field(min_length=1, max_length=200)
    name: str = ""
    context_window: int = Field(default=128_000, ge=1_000)
    max_output: int = Field(default=8_192, ge=1)
    capabilities: list[str] = Field(default_factory=lambda: ["text", "tools"])
    reasoning_levels: list[str] = Field(default_factory=lambda: ["off"])
    pricing: ModelPricing = Field(default_factory=ModelPricing)
    structured_output: bool = False

    @property
    def supports_vision(self) -> bool:
        return "image" in self.capabilities or "vision" in self.capabilities

    @property
    def display_name(self) -> str:
        return self.name or self.id


class OAuthConfig(StrictModel):
    token_url: str = ""
    client_id: str = ""
    client_secret: str = ""
    scope: str = ""
    audience: str = ""


class ProviderConfig(StrictModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = ""
    protocol: Literal["openai-chat", "openai-responses", "anthropic", "mock"] = "openai-chat"
    base_url: str = "https://api.deepseek.com"
    auth_type: Literal["api_key", "oauth_client_credentials", "none"] = "api_key"
    api_key: str | None = None
    api_key_env: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    oauth: OAuthConfig | None = None
    enabled: bool = True
    supports_balance: bool = False
    balance_path: str = "/user/balance"
    models: list[ModelConfig] = Field(default_factory=list)

    @property
    def display_name(self) -> str:
        return self.name or self.id


class ServerConfig(StrictModel):
    host: str = "127.0.0.1"
    port: int = Field(default=8787, ge=1, le=65_535)
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )
    request_timeout_seconds: int = Field(default=120, ge=1, le=3_600)
    log_level: str = "INFO"


class NetworkConfig(StrictModel):
    proxy: str = ""
    ca_bundle: str = ""
    user_agent: str = "MiniAgent/0.2"


class SearchConfig(StrictModel):
    provider: Literal["duckduckgo", "tavily"] = "duckduckgo"
    api_key: str | None = None
    api_key_env: str = "TAVILY_API_KEY"
    max_results: int = Field(default=5, ge=1, le=20)


class SubagentConfig(StrictModel):
    max_depth: int = Field(default=2, ge=1, le=8)
    max_concurrent: int = Field(default=3, ge=1, le=32)
    timeout_seconds: int = Field(default=600, ge=10, le=86_400)
    background_timeout_seconds: int = Field(default=3_600, ge=10, le=86_400)


class FormatterConfig(StrictModel):
    enabled: bool = True
    commands: dict[str, str] = Field(
        default_factory=lambda: {
            ".py": "ruff format {path}",
            ".ts": "prettier --write {path}",
            ".tsx": "prettier --write {path}",
            ".js": "prettier --write {path}",
            ".jsx": "prettier --write {path}",
            ".json": "prettier --write {path}",
            ".css": "prettier --write {path}",
            ".md": "prettier --write {path}",
        }
    )


class ToolsConfig(StrictModel):
    parallel_read_only: bool = True
    format: FormatterConfig = Field(default_factory=FormatterConfig)


class SandboxConfig(StrictModel):
    mode: Literal["none", "auto", "sandbox-exec", "bwrap", "docker"] = "auto"
    docker_image: str = "python:3.13-slim"
    allow_network: bool = False


class RetentionConfig(StrictModel):
    artifacts_days: int = Field(default=30, ge=1, le=3_650)
    events_days: int = Field(default=90, ge=1, le=3_650)
    logs_days: int = Field(default=30, ge=1, le=3_650)
    backups_days: int = Field(default=30, ge=1, le=3_650)
    uploads_days: int = Field(default=30, ge=1, le=3_650)


class MetricsConfig(StrictModel):
    enabled: bool = True


class ApiConfig(StrictModel):
    token: str = ""
    token_env: str = "MINI_AGENT_API_TOKEN"
    require_token_remote: bool = False
    trust_proxy_headers: bool = True
    rate_limit_per_minute: int = Field(default=600, ge=0, le=100_000)


class WebhookConfig(StrictModel):
    enabled: bool = True
    max_attempts: int = Field(default=3, ge=1, le=10)
    timeout_seconds: int = Field(default=10, ge=1, le=120)


class HookConfig(StrictModel):
    enabled: bool = True
    events: list[str] = Field(
        default_factory=lambda: ["session.start", "turn.end", "tool.before", "tool.after"]
    )


class BackupConfig(StrictModel):
    enabled: bool = True
    auto_before_migration: bool = True
    include_artifacts: bool = True


class GeneralConfig(StrictModel):
    language: Literal["zh-CN", "en-US"] = "zh-CN"
    theme: Literal["light", "dark", "system"] = "system"
    notifications: bool = True


class DefaultsConfig(StrictModel):
    model: str = "mock/echo"
    reasoning_level: str = "off"
    agent: str = "build"
    mode: Literal["build", "plan"] = "build"
    fallback_models: list[str] = Field(default_factory=list)
    vision_model: str | None = None
    compaction_model: str | None = None

    @field_validator("reasoning_level", mode="before")
    @classmethod
    def _coerce_reasoning_level(cls, value: Any) -> Any:
        # YAML 1.1 reads the unquoted token ``off`` as a boolean.
        if value is False:
            return "off"
        if value is True:
            return "on"
        return value


class CommandPermission(StrictModel):
    default: Literal["allow", "ask", "deny"] = "ask"
    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)


class PermissionConfig(StrictModel):
    read: CommandPermission = Field(default_factory=lambda: CommandPermission(default="allow"))
    write: CommandPermission = Field(default_factory=lambda: CommandPermission(default="ask"))
    shell: CommandPermission = Field(default_factory=CommandPermission)
    network: CommandPermission = Field(default_factory=lambda: CommandPermission(default="ask"))
    interaction: CommandPermission = Field(default_factory=lambda: CommandPermission(default="allow"))
    task: CommandPermission = Field(default_factory=lambda: CommandPermission(default="allow"))
    mcp: CommandPermission = Field(default_factory=lambda: CommandPermission(default="ask"))
    plugin: CommandPermission = Field(default_factory=lambda: CommandPermission(default="ask"))
    subagent: CommandPermission = Field(default_factory=lambda: CommandPermission(default="ask"))


class BudgetConfig(StrictModel):
    max_steps_per_turn: int = Field(default=50, ge=1, le=1_000)
    max_duration_seconds: int = Field(default=900, ge=10, le=86_400)
    max_cost_per_turn: float = Field(default=1.0, ge=0)
    max_repeat_failures: int = Field(default=3, ge=1, le=20)
    max_tool_output_chars: int = Field(default=24_000, ge=1_000, le=2_000_000)


class CompactionConfig(StrictModel):
    enabled: bool = True
    keep_recent_tokens: int = Field(default=4_000, ge=500, le=200_000)
    trigger_ratio: float = Field(default=0.85, gt=0, le=1.0)
    use_model: bool = False
    model: str | None = None
    summary_max_tokens: int = Field(default=2_000, ge=256, le=16_000)
    reload_project_rules: bool = True


class TaskConfig(StrictModel):
    wait_timeout_seconds: int = Field(default=120, ge=1, le=3_600)
    auto_continue_after_completion: bool = True
    max_duration_seconds: int = Field(default=3_600, ge=1, le=86_400)


class SecurityConfig(StrictModel):
    allow_outside_workspace: bool = False
    max_upload_mb: int = Field(default=20, ge=1, le=500)
    max_file_read_bytes: int = Field(default=512_000, ge=1_000, le=20_000_000)
    shell_timeout_seconds: int = Field(default=60, ge=1, le=3_600)
    block_private_network: bool = True


class AppConfig(StrictModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    general: GeneralConfig = Field(default_factory=GeneralConfig)
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)
    providers: list[ProviderConfig] = Field(default_factory=list)
    permissions: PermissionConfig = Field(default_factory=PermissionConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    compaction: CompactionConfig = Field(default_factory=CompactionConfig)
    tasks: TaskConfig = Field(default_factory=TaskConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    subagents: SubagentConfig = Field(default_factory=SubagentConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    webhooks: WebhookConfig = Field(default_factory=WebhookConfig)
    hooks: HookConfig = Field(default_factory=HookConfig)
    backup: BackupConfig = Field(default_factory=BackupConfig)
    data_dir: Path = Field(default_factory=lambda: repo_root() / "data")
    workspace_root: Path = Field(default_factory=lambda: repo_root() / "data" / "workspaces" / "demo")
    config_path: Path | None = None
    policy_path: Path | None = None
    source: str = "built-in"

    @field_validator("data_dir", "workspace_root", mode="before")
    @classmethod
    def _expand_path(cls, value: Any) -> Any:
        if isinstance(value, str):
            return Path(os.path.expandvars(os.path.expanduser(value))).resolve()
        return value

    @model_validator(mode="after")
    def _validate_defaults(self) -> AppConfig:
        if not self.providers:
            return self
        refs = {f"{p.id}/{m.id}" for p in self.providers if p.enabled for m in p.models}
        if self.defaults.model and self.defaults.model not in refs:
            # Fall back to the first configured model instead of crashing at startup.
            ordered = [
                ref for p in self.providers if p.enabled for ref in [f"{p.id}/{m.id}" for m in p.models]
            ]
            if ordered:
                self.defaults.model = ordered[0]
        return self

    def provider_for(self, model_ref: str) -> tuple[ProviderConfig, ModelConfig]:
        provider_id, _, model_id = model_ref.partition("/")
        for provider in self.providers:
            if provider.id == provider_id and provider.enabled:
                for model in provider.models:
                    if model.id == model_id:
                        return provider, model
        raise ResolutionError(f"模型 {model_ref!r} 未配置或已停用，请在设置中检查服务商配置")

    def provider_ready(self, provider: ProviderConfig) -> bool:
        if provider.protocol == "mock" or provider.auth_type == "none":
            return True
        if provider.api_key:
            return True
        if provider.auth_type == "oauth_client_credentials" and provider.oauth:
            return bool(provider.oauth.token_url and provider.oauth.client_id)
        return False

    def model_refs(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for provider in self.providers:
            if not provider.enabled or not self.provider_ready(provider):
                continue
            for model in provider.models:
                result.append(
                    {
                        "ref": f"{provider.id}/{model.id}",
                        "provider": provider.id,
                        "provider_name": provider.display_name,
                        "protocol": provider.protocol,
                        "name": model.display_name,
                        "context_window": model.context_window,
                        "max_output": model.max_output,
                        "capabilities": model.capabilities,
                        "reasoning_levels": model.reasoning_levels,
                        "pricing": model.pricing.model_dump(),
                        "structured_output": model.structured_output,
                        "vision": model.supports_vision,
                    }
                )
        return result

    def model_configured(self) -> bool:
        return bool(self.model_refs())

    def fallback_refs(self) -> list[str]:
        refs: list[str] = []
        configured = {f"{p.id}/{m.id}" for p in self.providers if p.enabled for m in p.models}
        for ref in self.defaults.fallback_models:
            if ref in configured and ref not in refs:
                refs.append(ref)
        return refs

    def secret_values(self) -> list[str]:
        values = [p.api_key for p in self.providers if p.api_key]
        for provider in self.providers:
            values.extend(value for value in provider.headers.values() if value)
        values.extend(
            provider.oauth.client_secret
            for provider in self.providers
            if provider.oauth and provider.oauth.client_secret
        )
        if self.search.api_key:
            values.append(self.search.api_key)
        if self.api.token:
            values.append(self.api.token)
        return values


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_config() -> AppConfig:
    root = repo_root()
    mock_model = ModelConfig(
        id="echo",
        name="Mock Echo (离线演示)",
        context_window=32_000,
        max_output=4_000,
        capabilities=["text", "tools", "reasoning"],
        reasoning_levels=["off", "low", "high"],
    )
    deepseek_model = ModelConfig(
        id="deepseek-chat",
        name="DeepSeek Chat",
        context_window=128_000,
        max_output=8_192,
        capabilities=["text", "tools"],
        reasoning_levels=["off"],
        pricing=ModelPricing(input_per_million=0.27, output_per_million=1.10, cache_hit_per_million=0.07),
    )
    reasoner_model = ModelConfig(
        id="deepseek-reasoner",
        name="DeepSeek Reasoner",
        context_window=128_000,
        max_output=8_192,
        capabilities=["text", "reasoning"],
        reasoning_levels=["high"],
        pricing=ModelPricing(input_per_million=0.55, output_per_million=2.19, cache_hit_per_million=0.14),
    )
    flash_model = ModelConfig(
        id="deepseek-flash",
        name="DeepSeek Flash",
        context_window=128_000,
        max_output=8_192,
        capabilities=["text", "tools", "reasoning"],
        reasoning_levels=["off", "low", "medium", "high", "max"],
    )
    v4_pro_model = ModelConfig(
        id="deepseek-v4-pro",
        name="DeepSeek V4 Pro",
        context_window=128_000,
        max_output=8_192,
        capabilities=["text", "tools", "reasoning"],
        reasoning_levels=["off", "low", "medium", "high", "max"],
    )
    return AppConfig(
        providers=[
            ProviderConfig(
                id="mock",
                name="离线 Mock 模型",
                protocol="mock",
                base_url="",
                models=[mock_model],
            ),
            ProviderConfig(
                id="deepseek",
                name="DeepSeek",
                protocol="openai-chat",
                base_url="https://api.deepseek.com",
                api_key_env="DEEPSEEK_API_KEY",
                models=[deepseek_model, reasoner_model, flash_model, v4_pro_model],
            ),
        ],
        data_dir=root / "data",
        workspace_root=root / "data" / "workspaces" / "demo",
    )


ENV_PATTERN = re.compile(r"^\$\{env:([A-Za-z_][A-Za-z0-9_]*)\}$")


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        match = ENV_PATTERN.match(value)
        if match:
            return os.environ.get(match.group(1), "")
        return value
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    return value


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge dicts without mutating inputs; lists are replaced atomically."""

    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ResolutionError(f"配置文件 {path} 无法解析：{exc}") from exc
    if not isinstance(loaded, dict):
        raise ResolutionError(f"配置文件 {path} 顶层必须是对象")
    return loaded


def _to_snake_key(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def normalize_config_keys(value: Any, *, preserve_keys: bool = False) -> Any:
    """Normalize camelCase YAML keys to model field names.

    Header/env maps are user-owned dictionaries: their keys are protocol names
    such as ``X-Api-Key`` and must not be rewritten. Keeping one spelling before
    final validation also avoids a pydantic ``extra`` error when both ``dataDir``
    and ``data_dir`` are present after merging.
    """

    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = str(key) if preserve_keys else _to_snake_key(str(key))
            # Only the map's own keys are preserved; nested config below it is
            # still normalized unless it is another user-keyed map.
            normalized[normalized_key] = normalize_config_keys(
                item, preserve_keys=normalized_key in {"headers", "env"}
            )
        return normalized
    if isinstance(value, list):
        return [normalize_config_keys(item, preserve_keys=preserve_keys) for item in value]
    return value


def _apply_env_overrides(raw: dict[str, Any]) -> dict[str, Any]:
    server = dict(raw.get("server") or {})
    if os.environ.get("MINI_AGENT_HOST"):
        server["host"] = os.environ["MINI_AGENT_HOST"]
    if os.environ.get("MINI_AGENT_PORT"):
        try:
            server["port"] = int(os.environ["MINI_AGENT_PORT"])
        except ValueError as exc:
            raise ResolutionError("MINI_AGENT_PORT 必须是整数") from exc
    raw["server"] = server
    if os.environ.get("MINI_AGENT_DATA_DIR"):
        raw["data_dir"] = os.environ["MINI_AGENT_DATA_DIR"]
    if os.environ.get("MINI_AGENT_WORKSPACE_ROOT"):
        raw["workspace_root"] = os.environ["MINI_AGENT_WORKSPACE_ROOT"]
    return raw


def _load_secrets(data_dir: Path) -> dict[str, str]:
    path = data_dir / "secrets.yaml"
    if not path.exists():
        return {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    providers = loaded.get("providers", {}) if isinstance(loaded, dict) else {}
    if not isinstance(providers, dict):
        return {}
    return {str(key): str(value) for key, value in providers.items() if value}


def load_config(config_path: str | Path | None = None) -> AppConfig:
    """Load and validate configuration, returning a fully resolved ``AppConfig``."""

    raw = _default_config().model_dump(mode="python")
    path = Path(
        config_path or os.environ.get("MINI_AGENT_CONFIG", repo_root() / "config" / "mini-agent.yaml")
    )
    path = path.expanduser().resolve()
    source = "built-in"
    file_config: dict[str, Any] = {}
    if path.exists():
        file_config = normalize_config_keys(_read_yaml(path))
        raw = deep_merge(raw, file_config)
        source = str(path)

    env_data_dir = os.environ.get("MINI_AGENT_DATA_DIR")
    data_dir = Path(env_data_dir or raw.get("data_dir", repo_root() / "data")).expanduser().resolve()
    local_path = data_dir / "workspace.yaml"
    local_config: dict[str, Any] = {}
    if local_path.exists():
        local_config = normalize_config_keys(_read_yaml(local_path))
        raw = deep_merge(raw, local_config)
        source = f"{source}+{local_path.name}"

    raw = _apply_env_overrides(raw)
    raw = _expand_env(raw)
    raw["data_dir"] = Path(raw.get("data_dir", data_dir)).expanduser().resolve()
    workspace_explicit = bool(
        os.environ.get("MINI_AGENT_WORKSPACE_ROOT")
        or "workspace_root" in file_config
        or "workspace_root" in local_config
    )
    if not workspace_explicit:
        raw["workspace_root"] = str(raw["data_dir"] / "workspaces" / "demo")
    workspace = Path(str(raw["workspace_root"])).expanduser()
    if not workspace.is_absolute():
        workspace = Path.cwd() / workspace
    raw["workspace_root"] = str(workspace.resolve())
    policy_env = os.environ.get("MINI_AGENT_POLICY")
    policy_path = (
        Path(policy_env).expanduser().resolve() if policy_env else (repo_root() / "config" / "policy.yaml")
    )
    if policy_path.exists():
        policy = normalize_config_keys(_read_yaml(policy_path))
        raw = deep_merge(raw, policy)
        source = f"{source}+policy"
        raw["policy_path"] = policy_path
    else:
        raw["policy_path"] = None

    raw.setdefault("search", {})
    raw["search"]["api_key"] = (
        raw["search"].get("api_key")
        or os.environ.get(str(raw["search"].get("api_key_env", "TAVILY_API_KEY")))
        or None
    )
    raw.setdefault("api", {})
    raw["api"]["token"] = (
        raw["api"].get("token")
        or os.environ.get(str(raw["api"].get("token_env", "MINI_AGENT_API_TOKEN")))
        or os.environ.get("MINI_AGENT_API_TOKEN", "")
    )
    raw["config_path"] = path if path.exists() else None
    raw["source"] = source

    config = AppConfig(**raw)
    if config.api.require_token_remote and not config.api.token:
        raise ValueError("api.requireTokenRemote=true 时必须配置 MINI_AGENT_API_TOKEN 或 api.token")
    secrets = _load_secrets(config.data_dir)
    for provider in config.providers:
        from_secret_file = secrets.get(provider.id)
        env_key = os.environ.get(provider.api_key_env) if provider.api_key_env else None
        provider.api_key = from_secret_file or env_key or (provider.api_key or None)
    return config


def apply_policy(config: AppConfig) -> None:
    """Re-apply the highest-precedence policy file to an in-memory config.

    Settings are persisted separately, but a running process must not let a
    UI patch relax a policy until the next restart. Invalid policy files fail
    closed instead of silently dropping the restrictions.
    """

    path = config.policy_path
    if path is None or not path.exists():
        return
    raw = normalize_config_keys(_read_yaml(path))
    sections = {
        "general": GeneralConfig,
        "defaults": DefaultsConfig,
        "server": ServerConfig,
        "budget": BudgetConfig,
        "permissions": PermissionConfig,
        "network": NetworkConfig,
        "search": SearchConfig,
        "tools": ToolsConfig,
        "sandbox": SandboxConfig,
        "retention": RetentionConfig,
        "api": ApiConfig,
        "compaction": CompactionConfig,
        "subagents": SubagentConfig,
        "tasks": TaskConfig,
        "metrics": MetricsConfig,
        "backup": BackupConfig,
        "webhooks": WebhookConfig,
        "hooks": HookConfig,
    }
    updates: dict[str, Any] = {}
    for section, model_cls in sections.items():
        value = raw.get(section)
        if not isinstance(value, dict):
            continue
        current = getattr(config, section).model_dump()
        if section == "api":
            value = {key: item for key, item in value.items() if key != "token"}
            current.pop("token", None)
        try:
            updates[section] = model_cls(**deep_merge(current, value))
        except ValueError as exc:
            raise ValueError(f"策略文件 {path} 的 {section} 配置无效：{exc}") from exc
    for section, value in updates.items():
        setattr(config, section, value)


def sanitize_config(config: AppConfig) -> dict[str, Any]:
    """Return a frontend-safe representation with all credentials masked."""

    providers = []
    for provider in config.providers:
        providers.append(
            {
                "id": provider.id,
                "name": provider.display_name,
                "protocol": provider.protocol,
                "base_url": provider.base_url,
                "auth_type": provider.auth_type,
                "enabled": provider.enabled,
                "configured": bool(provider.api_key) or provider.protocol == "mock",
                "supports_balance": provider.supports_balance,
                "balance_path": provider.balance_path,
                "key_hint": _key_hint(provider.api_key),
                "models": [model.model_dump() for model in provider.models],
            }
        )
    return {
        "general": config.general.model_dump(),
        "defaults": config.defaults.model_dump(),
        "providers": providers,
        "models": config.model_refs(),
        "model_configured": config.model_configured(),
        "budget": config.budget.model_dump(),
        "compaction": config.compaction.model_dump(),
        "permissions": config.permissions.model_dump(),
        "network": config.network.model_dump(),
        "search": config.search.model_dump(exclude={"api_key"}),
        "subagents": config.subagents.model_dump(),
        "tools": config.tools.model_dump(),
        "sandbox": config.sandbox.model_dump(),
        "retention": config.retention.model_dump(),
        "metrics": config.metrics.model_dump(),
        "api": config.api.model_dump(exclude={"token"}) | {"token_configured": bool(config.api.token)},
        "webhooks": config.webhooks.model_dump(),
        "hooks": config.hooks.model_dump(),
        "backup": config.backup.model_dump(),
        "policy_path": str(config.policy_path) if config.policy_path else None,
        "source": config.source,
        "config_path": str(config.config_path) if config.config_path else None,
        "workspace_root": str(config.workspace_root),
        "data_dir": str(config.data_dir),
    }


def _key_hint(api_key: str | None) -> str:
    if not api_key:
        return ""
    if len(api_key) <= 4:
        return "已配置"
    return f"已配置 ····{api_key[-4:]}"
