---
name: commit-message
description: 根据改动生成 Conventional Commits 提交信息；当用户要求写 commit message 或整理提交时使用。
version: 1
triggers: [commit, 提交信息, conventional commits]
---
# Commit Message Skill

1. 先读取 `git status` 和 `git diff --stat`。
2. 识别主要改动类型：feat / fix / refactor / docs / test / chore。
3. 第一行不超过 72 个字符，使用祈使句。
4. 正文说明动机、影响和验证方式。
5. 不要编造未执行的测试。
