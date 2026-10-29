"""Secret storage separate from ordinary configuration.

Keys live in ``<data>/secrets.yaml`` with mode 0600 (or come from environment
variables). API responses only ever expose a mask.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml


class SecretStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._cache: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            loaded = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return
        providers = loaded.get("providers", {}) if isinstance(loaded, dict) else {}
        if isinstance(providers, dict):
            self._cache = {str(key): str(value) for key, value in providers.items() if value}

    def get(self, key: str) -> str | None:
        return self._cache.get(key)

    def set(self, key: str, value: str | None) -> None:
        if value:
            self._cache[key] = value
        else:
            self._cache.pop(key, None)
        self._persist()

    def delete(self, key: str) -> None:
        self.set(key, None)

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"providers": dict(sorted(self._cache.items()))}
        temp = self.path.with_suffix(".yaml.tmp")
        temp.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
        os.chmod(temp, 0o600)
        os.replace(temp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    @property
    def values(self) -> list[str]:
        return [value for value in self._cache.values() if value]


def mask_secret(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "已配置"
    return f"已配置 ····{value[-4:]}"
