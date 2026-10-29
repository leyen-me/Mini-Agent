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

    @property
    def display_name(self) -> str:
        return self.name or self.id


class ProviderConfig(StrictModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = ""
    protocol: Literal["openai-chat", "mock"] = "openai-chat"
    base_url: str = "https://api.deepseek.com"
    api_key: str | None = None
    api_key_env: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
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


class GeneralConfig(StrictModel):
    language: Literal["zh-CN", "en-US"] = "zh-CN"
    theme: Literal["light", "dark", "system"] = "system"
    notifications: bool = True


class DefaultsConfig(StrictModel):
    model: str = "mock/echo"
    reasoning_level: str = "off"
    agent: str = "build"
    mode: Literal["build", "plan"] = "build"

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


class TaskConfig(StrictModel):
    wait_timeout_seconds: int = Field(default=120, ge=1, le=3_600)
    auto_continue_after_completion: bool = True


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
    data_dir: Path = Field(default_factory=lambda: repo_root() / "data")
    workspace_root: Path = Field(default_factory=lambda: repo_root() / "data" / "workspaces" / "demo")
    config_path: Path | None = None
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

    def model_refs(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for provider in self.providers:
            if not provider.enabled:
                continue
            for model in provider.models:
                if provider.protocol == "openai-chat" and not provider.api_key:
                    continue
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
                    }
                )
        return result

    def model_configured(self) -> bool:
        return bool(self.model_refs())

    def secret_values(self) -> list[str]:
        return [p.api_key for p in self.providers if p.api_key]


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
                models=[deepseek_model, reasoner_model],
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


def normalize_config_keys(value: Any) -> Any:
    """Normalize camelCase YAML keys to model field names.

    Keeping one spelling before the final validation avoids a subtle pydantic
    ``extra`` error when both ``dataDir`` and ``data_dir`` are present after merging.
    """

    if isinstance(value, dict):
        return {_to_snake_key(str(key)): normalize_config_keys(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_config_keys(item) for item in value]
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
    raw["config_path"] = path if path.exists() else None
    raw["source"] = source

    config = AppConfig(**raw)
    secrets = _load_secrets(config.data_dir)
    for provider in config.providers:
        from_secret_file = secrets.get(provider.id)
        env_key = os.environ.get(provider.api_key_env) if provider.api_key_env else None
        provider.api_key = from_secret_file or env_key or (provider.api_key or None)
    return config


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
                "enabled": provider.enabled,
                "configured": bool(provider.api_key) or provider.protocol == "mock",
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
