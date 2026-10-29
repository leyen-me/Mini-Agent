"""Workspace ignore rules from .gitignore/.mini-agentignore plus builtin defaults."""

from __future__ import annotations

from pathlib import Path

import pathspec

DEFAULT_IGNORES = [
    ".git/",
    ".hg/",
    ".svn/",
    "node_modules/",
    ".venv/",
    "venv/",
    "__pycache__/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".mypy_cache/",
    "dist/",
    "build/",
    ".DS_Store",
    ".mini-agent/",
]


class WorkspaceIgnore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        lines: list[str] = list(DEFAULT_IGNORES)
        for name in (".gitignore", ".mini-agentignore"):
            path = self.root / name
            if path.exists():
                try:
                    lines.extend(path.read_text(encoding="utf-8", errors="replace").splitlines())
                except OSError:
                    pass
        self.spec = pathspec.PathSpec.from_lines("gitwildmatch", lines)
        self._cache: dict[str, bool] = {}

    def ignored(self, path: Path | str, *, is_dir: bool | None = None) -> bool:
        if isinstance(path, Path):
            try:
                relative = path.resolve().relative_to(self.root).as_posix()
            except ValueError:
                return True
        else:
            relative = str(path).replace("\\", "/")
        if relative in {"", "."}:
            return False
        key = relative + ("/" if is_dir else "")
        if key in self._cache:
            return self._cache[key]
        value = self.spec.match_file(key)
        self._cache[key] = value
        return value

    def filter(self, root: Path, names: list[str]) -> list[str]:
        result = []
        for name in names:
            candidate = root / name
            if not self.ignored(candidate, is_dir=candidate.is_dir()):
                result.append(name)
        return result
