"""Provider/model registry backed by SQLite with secrets stored separately."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx
from sqlalchemy import delete, select

from .config import (
    AppConfig,
    ModelConfig,
    ModelPricing,
    OAuthConfig,
    ProviderConfig,
    ResolutionError,
)
from .db import Database
from .models import Agent, Provider, ProviderModel, Session, utcnow
from .secrets_store import SecretStore, mask_secret

logger = logging.getLogger(__name__)

PROTOCOLS = {"openai-chat", "openai-responses", "anthropic", "mock"}


class ProviderService:
    def __init__(self, db: Database, config: AppConfig) -> None:
        self.db = db
        self.config = config
        self.secrets = SecretStore(config.data_dir / "secrets.yaml")
        self._oauth_tokens: dict[str, tuple[str, float]] = {}

    def _store_oauth(self, provider: ProviderConfig) -> dict[str, Any]:
        if not provider.oauth:
            return {}
        data = provider.oauth.model_dump()
        secret = data.pop("client_secret", "")
        if secret:
            self.secrets.set(f"oauth:{provider.id}", secret)
        return data

    async def seed_and_load(self) -> None:
        async with self.db.session() as db:
            existing = (await db.execute(select(Provider).limit(1))).scalars().first()
            if existing is None:
                for position, provider in enumerate(self.config.providers):
                    await self._insert_provider(db, provider, position)
                await db.commit()
        await self.reload()

    async def _insert_provider(self, db, provider: ProviderConfig, position: int) -> None:
        secret = provider.api_key or self.secrets.get(provider.id)
        if provider.api_key:
            self.secrets.set(provider.id, provider.api_key)
        db.add(
            Provider(
                id=provider.id,
                name=provider.display_name,
                protocol=provider.protocol,
                base_url=provider.base_url,
                auth_type=provider.auth_type,
                api_key_env=provider.api_key_env,
                headers_json=json.dumps(provider.headers, ensure_ascii=False),
                oauth_json=json.dumps(self._store_oauth(provider), ensure_ascii=False),
                enabled=provider.enabled,
                supports_balance=provider.supports_balance,
                balance_path=provider.balance_path,
                position=position,
            )
        )
        await db.flush()
        for index, model in enumerate(provider.models):
            self._insert_model(db, provider.id, model, index)
        _ = secret

    @staticmethod
    def _insert_model(db, provider_id: str, model: ModelConfig, position: int) -> None:
        db.add(
            ProviderModel(
                provider_id=provider_id,
                model_id=model.id,
                name=model.name,
                context_window=model.context_window,
                max_output=model.max_output,
                capabilities_json=json.dumps(model.capabilities, ensure_ascii=False),
                reasoning_levels_json=json.dumps(model.reasoning_levels, ensure_ascii=False),
                pricing_json=json.dumps(model.pricing.model_dump(), ensure_ascii=False),
                supports_structured_output=model.structured_output,
                position=position,
            )
        )

    async def reload(self) -> None:
        async with self.db.session() as db:
            providers = (
                (await db.execute(select(Provider).order_by(Provider.position.asc(), Provider.id.asc())))
                .scalars()
                .all()
            )
            models = (
                (await db.execute(select(ProviderModel).order_by(ProviderModel.position.asc())))
                .scalars()
                .all()
            )
        by_provider: dict[str, list[ProviderModel]] = {}
        for model in models:
            by_provider.setdefault(model.provider_id, []).append(model)
        loaded: list[ProviderConfig] = []
        for row in providers:
            secret = self.secrets.get(row.id) or self._env_secret(row.api_key_env)
            oauth_data = self._json(row.oauth_json, {})
            if oauth_data:
                oauth_data["client_secret"] = self.secrets.get(f"oauth:{row.id}") or ""
            provider = ProviderConfig(
                id=row.id,
                name=row.name,
                protocol=row.protocol,  # validated by pydantic; DB is trusted local state
                base_url=row.base_url,
                auth_type=row.auth_type,
                api_key=secret,
                api_key_env=row.api_key_env,
                headers=self._json(row.headers_json, {}),
                oauth=OAuthConfig(**oauth_data) if oauth_data else None,
                enabled=row.enabled,
                supports_balance=row.supports_balance,
                balance_path=row.balance_path,
                models=[self._model_config(item) for item in by_provider.get(row.id, [])],
            )
            loaded.append(provider)
        self.config.providers = loaded
        refs = {item["ref"] for item in self.config.model_refs()}
        if refs and self.config.defaults.model not in refs:
            self.config.defaults.model = sorted(refs)[0]
        self.config.defaults.fallback_models = [
            ref for ref in self.config.defaults.fallback_models if ref in refs
        ]

    @staticmethod
    def _env_secret(env_name: str | None) -> str | None:
        import os

        return os.environ.get(env_name) if env_name else None

    @staticmethod
    def _model_config(row: ProviderModel) -> ModelConfig:
        return ModelConfig(
            id=row.model_id,
            name=row.name,
            context_window=row.context_window,
            max_output=row.max_output,
            capabilities=ProviderService._json(row.capabilities_json, ["text", "tools"]),
            reasoning_levels=ProviderService._json(row.reasoning_levels_json, ["off"]),
            pricing=ModelPricing(**ProviderService._json(row.pricing_json, {})),
            structured_output=row.supports_structured_output,
        )

    @staticmethod
    def _json(raw: str | None, default: Any) -> Any:
        if not raw:
            return default
        try:
            value = json.loads(raw)
            return value if isinstance(value, type(default)) else default
        except (json.JSONDecodeError, TypeError):
            return default

    def public_list(self) -> list[dict[str, Any]]:
        return [
            {
                "id": provider.id,
                "name": provider.display_name,
                "protocol": provider.protocol,
                "base_url": provider.base_url,
                "auth_type": provider.auth_type,
                "enabled": provider.enabled,
                "configured": self.config.provider_ready(provider),
                "key_hint": mask_secret(provider.api_key),
                "api_key_env": provider.api_key_env,
                "headers": {
                    key: (
                        "••••"
                        if key.lower() in {"authorization", "x-api-key", "api-key", "cookie"}
                        else value
                    )
                    for key, value in provider.headers.items()
                },
                "supports_balance": provider.supports_balance,
                "balance_path": provider.balance_path,
                "models": [self.model_public(provider.id, model) for model in provider.models],
            }
            for provider in self.config.providers
        ]

    @staticmethod
    def model_public(provider_id: str, model: ModelConfig) -> dict[str, Any]:
        return {
            "id": model.id,
            "ref": f"{provider_id}/{model.id}",
            "name": model.display_name,
            "context_window": model.context_window,
            "max_output": model.max_output,
            "capabilities": model.capabilities,
            "reasoning_levels": model.reasoning_levels,
            "pricing": model.pricing.model_dump(),
            "structured_output": model.structured_output,
            "vision": model.supports_vision,
        }

    def require(self, provider_id: str) -> ProviderConfig:
        for provider in self.config.providers:
            if provider.id == provider_id:
                return provider
        raise LookupError("服务商不存在")

    async def create(self, payload: dict[str, Any]) -> ProviderConfig:
        provider = ProviderConfig(**payload)
        if provider.protocol not in PROTOCOLS:
            raise ValueError("不支持的协议")
        if provider.api_key:
            self.secrets.set(provider.id, provider.api_key)
        async with self.db.session() as db:
            if await db.get(Provider, provider.id):
                raise ValueError("服务商 ID 已存在")
            await self._insert_provider(db, provider, len(self.config.providers))
            await db.commit()
        await self.reload()
        return self.require(provider.id)

    async def update(self, provider_id: str, payload: dict[str, Any]) -> ProviderConfig:
        current = self.require(provider_id)
        data = current.model_dump()
        for key, value in payload.items():
            if key == "api_key":
                self.secrets.set(provider_id, value)
                data["api_key"] = value or None
            elif key != "id":
                data[key] = value
        candidate = ProviderConfig(**{**data, "id": provider_id})
        async with self.db.session() as db:
            row = await db.get(Provider, provider_id)
            if row is None:
                raise LookupError("服务商不存在")
            row.name = candidate.display_name
            row.protocol = candidate.protocol
            row.base_url = candidate.base_url
            row.auth_type = candidate.auth_type
            row.api_key_env = candidate.api_key_env
            row.headers_json = json.dumps(candidate.headers, ensure_ascii=False)
            row.oauth_json = json.dumps(self._store_oauth(candidate), ensure_ascii=False)
            row.enabled = candidate.enabled
            row.supports_balance = candidate.supports_balance
            row.balance_path = candidate.balance_path
            row.updated_at = utcnow()
            await db.commit()
        await self.reload()
        return self.require(provider_id)

    async def delete(self, provider_id: str) -> None:
        async with self.db.session() as db:
            used = (
                (await db.execute(select(Session).where(Session.model.like(f"{provider_id}/%")).limit(1)))
                .scalars()
                .first()
            )
            if used is not None:
                raise ValueError("仍有会话使用该服务商的模型，请先切换或归档这些会话")
            agent_used = (
                (await db.execute(select(Agent).where(Agent.default_model.like(f"{provider_id}/%")).limit(1)))
                .scalars()
                .first()
            )
            if agent_used is not None:
                raise ValueError("仍有 Agent 使用该服务商的模型，请先更新 Agent 默认模型")
            row = await db.get(Provider, provider_id)
            if row is None:
                raise LookupError("服务商不存在")
            await db.execute(delete(ProviderModel).where(ProviderModel.provider_id == provider_id))
            await db.flush()
            await db.delete(row)
            await db.commit()
        self.secrets.delete(provider_id)
        self.secrets.delete(f"oauth:{provider_id}")
        await self.reload()

    async def upsert_model(self, provider_id: str, payload: dict[str, Any]) -> ModelConfig:
        provider = self.require(provider_id)
        existing = next((model for model in provider.models if model.id == payload.get("id")), None)
        base = existing.model_dump() if existing else {}
        # The HTTP DTO fills defaults such as pricing={}; an update that only
        # changes reasoning/capabilities must not erase existing pricing.
        update_payload = dict(payload)
        if existing is not None and not update_payload.get("pricing"):
            update_payload.pop("pricing", None)
        candidate = ModelConfig(**{**base, **update_payload})
        async with self.db.session() as db:
            row = (
                (
                    await db.execute(
                        select(ProviderModel).where(
                            ProviderModel.provider_id == provider_id,
                            ProviderModel.model_id == candidate.id,
                        )
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                self._insert_model(db, provider_id, candidate, len(provider.models))
            else:
                row.name = candidate.name
                row.context_window = candidate.context_window
                row.max_output = candidate.max_output
                row.capabilities_json = json.dumps(candidate.capabilities, ensure_ascii=False)
                row.reasoning_levels_json = json.dumps(candidate.reasoning_levels, ensure_ascii=False)
                row.pricing_json = json.dumps(candidate.pricing.model_dump(), ensure_ascii=False)
                row.supports_structured_output = candidate.structured_output
            await db.commit()
        await self.reload()
        return next(model for model in self.require(provider_id).models if model.id == candidate.id)

    async def delete_model(self, provider_id: str, model_id: str) -> None:
        async with self.db.session() as db:
            row = (
                (
                    await db.execute(
                        select(ProviderModel).where(
                            ProviderModel.provider_id == provider_id, ProviderModel.model_id == model_id
                        )
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                raise LookupError("模型不存在")
            used = (
                (
                    await db.execute(
                        select(Session).where(Session.model == f"{provider_id}/{model_id}").limit(1)
                    )
                )
                .scalars()
                .first()
            )
            if used is not None:
                raise ValueError("仍有会话使用该模型")
            agent_used = (
                (
                    await db.execute(
                        select(Agent).where(Agent.default_model == f"{provider_id}/{model_id}").limit(1)
                    )
                )
                .scalars()
                .first()
            )
            if agent_used is not None:
                raise ValueError("仍有 Agent 使用该模型")
            await db.delete(row)
            await db.commit()
        await self.reload()

    async def auth_headers(self, provider: ProviderConfig) -> dict[str, str]:
        headers = dict(provider.headers)
        if provider.auth_type == "oauth_client_credentials":
            token = await self._oauth_token(provider)
            headers["Authorization"] = f"Bearer {token}"
        elif provider.api_key:
            if provider.protocol == "anthropic":
                headers["x-api-key"] = provider.api_key
                headers.setdefault("anthropic-version", "2023-06-01")
            else:
                headers["Authorization"] = f"Bearer {provider.api_key}"
        return headers

    async def _oauth_token(self, provider: ProviderConfig) -> str:
        cached = self._oauth_tokens.get(provider.id)
        if cached and cached[1] > time.time() + 30:
            return cached[0]
        oauth = provider.oauth
        if not oauth or not oauth.token_url:
            raise ResolutionError("OAuth 配置不完整")
        async with httpx.AsyncClient(
            timeout=20,
            proxy=self.config.network.proxy or None,
            verify=self.config.network.ca_bundle or True,
        ) as client:
            response = await client.post(
                oauth.token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": oauth.client_id,
                    "client_secret": oauth.client_secret,
                    "scope": oauth.scope,
                    "audience": oauth.audience,
                },
            )
        if response.status_code >= 400:
            raise ResolutionError(f"OAuth token 请求失败：HTTP {response.status_code}")
        data = response.json()
        token = str(data.get("access_token") or "")
        if not token:
            raise ResolutionError("OAuth 响应缺少 access_token")
        self._oauth_tokens[provider.id] = (token, time.time() + int(data.get("expires_in", 3600)))
        return token

    async def test_connection(self, provider_id: str) -> dict[str, Any]:
        provider = self.require(provider_id)
        started = time.monotonic()
        try:
            headers = await self.auth_headers(provider)
            if provider.protocol == "mock":
                return {"ok": True, "latencyMs": 0, "models": len(provider.models)}
            async with httpx.AsyncClient(
                timeout=20,
                proxy=self.config.network.proxy or None,
                verify=self.config.network.ca_bundle or True,
            ) as client:
                if provider.protocol == "anthropic":
                    url = f"{provider.base_url.rstrip('/')}/v1/models"
                else:
                    base = provider.base_url.rstrip("/")
                    url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
                response = await client.get(url, headers=headers)
            return {
                "ok": response.status_code < 400,
                "status": response.status_code,
                "latencyMs": int((time.monotonic() - started) * 1000),
                "models": len(provider.models),
                "error": None if response.status_code < 400 else response.text[:300],
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "status": 0,
                "latencyMs": int((time.monotonic() - started) * 1000),
                "error": type(exc).__name__,
            }

    async def fetch_models(self, provider_id: str) -> list[dict[str, Any]]:
        provider = self.require(provider_id)
        if provider.protocol == "mock":
            return [self.model_public(provider.id, model) for model in provider.models]
        headers = await self.auth_headers(provider)
        async with httpx.AsyncClient(
            timeout=30,
            proxy=self.config.network.proxy or None,
            verify=self.config.network.ca_bundle or True,
        ) as client:
            base = provider.base_url.rstrip("/")
            url = (
                f"{base}/v1/models"
                if provider.protocol == "anthropic"
                else (f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models")
            )
            response = await client.get(url, headers=headers)
        if response.status_code >= 400:
            raise ResolutionError(f"拉取模型失败：HTTP {response.status_code}")
        payload = response.json()
        rows = payload.get("data") or payload.get("models") or []
        return [
            {
                "id": str(item.get("id") or item.get("name") or ""),
                "name": str(item.get("display_name") or item.get("name") or item.get("id") or ""),
                "context_window": 128_000,
                "capabilities": self._infer_capabilities(str(item.get("id") or "")),
                "reasoning_levels": self._infer_reasoning_levels(str(item.get("id") or "")),
                "vision": "vision" in self._infer_capabilities(str(item.get("id") or "")),
            }
            for item in rows
            if item.get("id") or item.get("name")
        ]

    @staticmethod
    def _infer_capabilities(model_id: str) -> list[str]:
        lowered = model_id.lower()
        capabilities = ["text"]
        if any(token in lowered for token in ("gpt", "claude", "deepseek", "qwen", "llama")):
            capabilities.append("tools")
        if any(token in lowered for token in ("vision", "vl", "4o", "claude-3", "gemini")):
            capabilities.extend(["image", "vision"])
        if any(token in lowered for token in ("reason", "o1", "o3", "r1", "thinking", "flash", "v4")):
            capabilities.append("reasoning")
        return capabilities

    @staticmethod
    def _infer_reasoning_levels(model_id: str) -> list[str]:
        lowered = model_id.lower()
        if any(token in lowered for token in ("reason", "o1", "o3", "r1", "thinking", "flash", "v4")):
            return ["off", "low", "medium", "high", "max"]
        return ["off"]

    async def balance(self, provider_id: str) -> dict[str, Any]:
        provider = self.require(provider_id)
        if not provider.supports_balance:
            raise ResolutionError("该服务商未启用余额查询")
        headers = await self.auth_headers(provider)
        url = f"{provider.base_url.rstrip('/')}{provider.balance_path}"
        async with httpx.AsyncClient(
            timeout=15,
            proxy=self.config.network.proxy or None,
            verify=self.config.network.ca_bundle or True,
        ) as client:
            response = await client.get(url, headers=headers)
        if response.status_code >= 400:
            raise ResolutionError(f"余额查询失败：HTTP {response.status_code}")
        payload = response.json()
        infos = payload.get("balance_infos") or []
        if infos:
            total = sum(float(item.get("total_balance") or 0) for item in infos)
            granted = sum(float(item.get("granted_balance") or 0) for item in infos)
            topped = sum(float(item.get("topped_up_balance") or 0) for item in infos)
            currency = str(infos[0].get("currency") or "CNY")
            return {
                "currency": currency,
                "total": total,
                "granted": granted,
                "toppedUp": topped,
                "available": bool(payload.get("is_available", True)),
                "raw": payload,
            }
        return {
            "currency": str(payload.get("currency") or "USD"),
            "total": float(payload.get("total") or payload.get("balance") or 0),
            "granted": float(payload.get("granted") or 0),
            "toppedUp": float(payload.get("topped_up") or 0),
            "available": bool(payload.get("available", True)),
            "raw": payload,
        }
