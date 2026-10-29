"""First-run seed data: built-in agents, default workspace and demo files."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select

from .config import AppConfig
from .db import Database
from .models import Agent, Workspace

logger = logging.getLogger(__name__)

BUILD_AGENT_PROMPT = "你负责在用户授权范围内完成真实的代码与文件修改，并在每一步后验证结果。"
PLAN_AGENT_PROMPT = "你负责只读分析、拆解任务和产出计划。不要修改文件或执行有副作用的命令。"

DEMO_AGENTS = [
    {
        "id": "build",
        "name": "Build Agent",
        "description": "可读写工作区的默认 Agent",
        "system_prompt": BUILD_AGENT_PROMPT,
        "read_only": False,
        "builtin": True,
    },
    {
        "id": "plan",
        "name": "Plan Agent",
        "description": "只读分析与计划模式",
        "system_prompt": PLAN_AGENT_PROMPT,
        "read_only": True,
        "builtin": True,
    },
]

DEMO_README = """# Mini Agent 演示工作区

这个目录是 Agent 的隔离工作区。你可以让 Agent 在这里读取、创建和修改文件。

试试在聊天框输入：

- `文件`：列出当前目录
- `权限`：触发 shell 权限确认
- `长任务`：启动后台任务并观察完成通知
- `提问`：触发 ask_question 表单

所有文件操作都被限制在此目录内；`..` 和符号链接逃逸会被拒绝。
"""

DEMO_AGENTS_MD = """# 演示工作区规则

- 修改文件前先读取并确认影响范围。
- 不执行破坏性命令，不访问工作区之外的路径。
- 每次修改后验证结果，并在最终回复里列出验证命令。
"""

DEMO_HELLO = '''"""Demo module used by the file tools."""


def greet(name: str) -> str:
    return f"Hello, {name}!"


if __name__ == "__main__":
    print(greet("mini-agent"))
'''


async def seed(db: Database, config: AppConfig) -> str:
    workspace_root = config.workspace_root
    workspace_root.mkdir(parents=True, exist_ok=True)
    _seed_file(workspace_root / "README.md", DEMO_README)
    _seed_file(workspace_root / "AGENTS.md", DEMO_AGENTS_MD)
    _seed_file(workspace_root / "hello.py", DEMO_HELLO)
    async with db.session() as session:
        for agent_data in DEMO_AGENTS:
            existing = await session.get(Agent, agent_data["id"])
            if existing is None:
                session.add(Agent(**agent_data))
        workspace = (
            (await session.execute(select(Workspace).order_by(Workspace.created_at.asc()).limit(1)))
            .scalars()
            .first()
        )
        if workspace is None:
            workspace = Workspace(name="Default Workspace", root_path=str(workspace_root))
            session.add(workspace)
            await session.flush()
        return workspace.id


def _seed_file(path: Path, content: str) -> None:
    if path.exists():
        return
    try:
        path.write_text(content, encoding="utf-8")
    except OSError:
        logger.warning("failed to seed demo file", extra={"path": str(path)})
