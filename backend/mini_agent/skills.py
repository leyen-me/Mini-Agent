"""Filesystem-backed Skill registry with builtin/system/workspace scopes."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select

from .config import AppConfig
from .db import Database
from .models import Agent, Session, SessionSkill, Workspace

SCOPES = ("builtin", "system", "workspace")
SCOPE_PRIORITY = {"builtin": 0, "system": 1, "workspace": 2}
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")


class SkillError(ValueError):
    pass


class SkillService:
    def __init__(self, db: Database, config: AppConfig) -> None:
        self.db = db
        self.config = config
        self.builtin_root = Path(__file__).resolve().parents[2] / "skills"
        self.system_root = config.data_dir / "skills"

    def roots(self, workspace_root: Path | None = None) -> dict[str, Path]:
        roots = {"builtin": self.builtin_root, "system": self.system_root}
        if workspace_root is not None:
            roots["workspace"] = workspace_root / ".mini-agent" / "skills"
        return roots

    def scan(self, workspace_root: Path | None = None) -> list[dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        for scope, root in self.roots(workspace_root).items():
            if not root.exists():
                continue
            for directory in sorted(root.iterdir()):
                manifest = directory / "SKILL.md"
                if not directory.is_dir() or not manifest.exists():
                    continue
                info = self._parse_manifest(manifest, scope, directory)
                if info is None:
                    continue
                current = found.get(info["name"])
                if current is None or SCOPE_PRIORITY[scope] >= SCOPE_PRIORITY[current["scope"]]:
                    found[info["name"]] = info
        return sorted(found.values(), key=lambda item: item["name"])

    def _parse_manifest(self, path: Path, scope: str, directory: Path) -> dict[str, Any] | None:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return None
        meta: dict[str, Any] = {}
        body = text
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                try:
                    loaded = yaml.safe_load(parts[1]) or {}
                    meta = loaded if isinstance(loaded, dict) else {}
                except yaml.YAMLError:
                    meta = {}
                body = parts[2].lstrip("\n")
        name = str(meta.get("name") or directory.name)
        if not NAME_RE.match(name):
            return None
        files = []
        for item in sorted(directory.rglob("*")):
            if item.is_file() and item != path and len(files) < 50:
                files.append(str(item.relative_to(directory)))
        return {
            "name": name,
            "description": str(
                meta.get("description") or body.strip().splitlines()[0] if body.strip() else ""
            ),
            "version": str(meta.get("version") or "1"),
            "triggers": [str(item) for item in meta.get("triggers") or []],
            "scope": scope,
            "path": str(directory),
            "files": files,
            "content": body,
        }

    async def workspace_root_for_session(self, session: Session) -> Path:
        async with self.db.session() as db:
            workspace = await db.get(Workspace, session.workspace_id)
        if workspace is None:
            raise SkillError("工作区不存在")
        return Path(workspace.root_path)

    async def list(self, session: Session | None = None) -> list[dict[str, Any]]:
        workspace_root = await self.workspace_root_for_session(session) if session else None
        skills = self.scan(workspace_root)
        enabled: dict[str, dict[str, Any]] = {}
        if session is not None:
            async with self.db.session() as db:
                rows = (
                    (
                        await db.execute(
                            select(SessionSkill).where(
                                SessionSkill.session_id == session.id, SessionSkill.enabled.is_(True)
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            enabled = {row.skill_name: row for row in rows}
        public = []
        for skill in skills:
            item = {key: value for key, value in skill.items() if key != "content"}
            item["enabled"] = skill["name"] in enabled
            public.append(item)
        if session is not None:
            # Keep enabled skills that were removed from disk visible so users can disable them.
            known = {item["name"] for item in public}
            for name in enabled:
                if name not in known:
                    public.append(
                        {
                            "name": name,
                            "description": "（Skill 目录已不存在）",
                            "version": "0",
                            "triggers": [],
                            "scope": enabled[name].scope,
                            "path": "",
                            "files": [],
                            "enabled": True,
                        }
                    )
        return public

    async def get(self, name: str, session: Session | None = None) -> dict[str, Any]:
        workspace_root = await self.workspace_root_for_session(session) if session else None
        for skill in self.scan(workspace_root):
            if skill["name"] == name:
                return skill
        raise LookupError("Skill 不存在")

    async def enable(self, session: Session, name: str, enabled: bool) -> dict[str, Any]:
        skill = await self.get(name, session)
        async with self.db.session() as db:
            row = (
                (
                    await db.execute(
                        select(SessionSkill).where(
                            SessionSkill.session_id == session.id,
                            SessionSkill.skill_name == name,
                            SessionSkill.scope == skill["scope"],
                        )
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                row = SessionSkill(
                    session_id=session.id,
                    skill_name=name,
                    scope=skill["scope"],
                    enabled=enabled,
                )
                db.add(row)
            else:
                row.enabled = enabled
            await db.commit()
        return {"name": name, "scope": skill["scope"], "enabled": enabled}

    async def enabled_skills(self, session: Session) -> list[dict[str, Any]]:
        workspace_root = await self.workspace_root_for_session(session)
        by_name = {item["name"]: item for item in self.scan(workspace_root)}
        async with self.db.session() as db:
            rows = (
                (
                    await db.execute(
                        select(SessionSkill).where(
                            SessionSkill.session_id == session.id, SessionSkill.enabled.is_(True)
                        )
                    )
                )
                .scalars()
                .all()
            )
        result = []
        for row in rows:
            skill = by_name.get(row.skill_name)
            if skill:
                result.append(skill)
        return sorted(result, key=lambda item: item["name"])

    async def ensure_defaults(self, session: Session, agent: Agent) -> None:
        try:
            defaults = json.loads(agent.skills_json or "[]")
        except json.JSONDecodeError:
            defaults = []
        for name in defaults:
            try:
                await self.enable(session, str(name), True)
            except LookupError:
                continue

    async def metadata_for_session(self, session: Session) -> list[dict[str, Any]]:
        skills = await self.enabled_skills(session)
        return [
            {
                "name": skill["name"],
                "description": skill["description"],
                "scope": skill["scope"],
                "triggers": skill["triggers"],
            }
            for skill in skills
        ]

    async def load(self, session: Session, name: str) -> dict[str, Any]:
        skills = {skill["name"]: skill for skill in await self.enabled_skills(session)}
        skill = skills.get(name)
        if skill is None:
            raise PermissionError("该 Skill 未在当前会话启用")
        return skill

    def save(
        self,
        scope: str,
        name: str,
        content: str,
        description: str = "",
        workspace_root: Path | None = None,
    ) -> dict[str, Any]:
        if scope not in {"system", "workspace"}:
            raise SkillError("只能编辑 system 或 workspace 作用域的 Skill")
        if not NAME_RE.match(name):
            raise SkillError("Skill 名称只能包含小写字母、数字、- 和 _")
        root = (
            self.system_root
            if scope == "system"
            else (workspace_root or self.config.workspace_root) / ".mini-agent" / "skills"
        )
        directory = root / name
        directory.mkdir(parents=True, exist_ok=True)
        manifest = directory / "SKILL.md"
        current = manifest.read_text(encoding="utf-8") if manifest.exists() else ""
        if current.startswith("---"):
            parts = current.split("---", 2)
            if len(parts) >= 3:
                current_body = parts[2].lstrip("\n")
            else:
                current_body = content
        else:
            current_body = current
        body = content if content else current_body
        header = f"---\nname: {name}\ndescription: {description}\nversion: 1\n---\n"
        manifest.write_text(header + body, encoding="utf-8")
        return {"name": name, "scope": scope, "path": str(directory)}

    def delete(self, scope: str, name: str, workspace_root: Path | None = None) -> None:
        if scope not in {"system", "workspace"}:
            raise SkillError("内置 Skill 不能删除")
        root = (
            self.system_root
            if scope == "system"
            else (workspace_root or self.config.workspace_root) / ".mini-agent" / "skills"
        )
        directory = root / name
        if not directory.exists():
            raise LookupError("Skill 不存在")
        shutil.rmtree(directory)
