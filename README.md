# Mini Agent

一个**可运行、可持久化、可验收**的最小 Agent 工作台：后端 Python / FastAPI，前端 React + Vite，SQLite 使用 WAL，REST 操作 + SSE 事件流。

本项目依据 [`create-common-agent-guide.md`](create-common-agent-guide.md) 的 P0 核心能力实现，并以该文件 **附录 D 验收清单** 作为硬性交付标准。验收清单的自动化证据见 [`docs/ACCEPTANCE.md`](docs/ACCEPTANCE.md)。

![Mini Agent 首页](docs/screenshots/home-light.png)

## 快速开始

环境要求：Python ≥ 3.11、[uv](https://docs.astral.sh/uv/)、Node.js ≥ 20、pnpm ≥ 10。

```bash
# 一键启动（会自动构建前端，后端同时托管 UI 和 API）
./scripts/start.sh
# 打开 http://127.0.0.1:8787
```

默认使用离线 **Mock 模型**，不需要 API Key，可以直接体验：流式输出、工具调用、权限确认、`ask_question`、长任务、队列、目标、压缩、分享。

开发模式（Vite HMR + 后端热重载）：

```bash
./scripts/dev.sh
# UI:  http://127.0.0.1:5173
# API: http://127.0.0.1:8787
```

Docker：

```bash
docker compose up --build
# 打开 http://127.0.0.1:8787，数据持久化在 ./data
```

## 接入真实模型（DeepSeek）

```bash
cp .env.example .env
# 编辑 .env：DEEPSEEK_API_KEY=sk-...
./scripts/start.sh
```

然后在「设置 → 模型」把默认模型切换为 `deepseek/deepseek-chat`，或在输入区为当前会话切换模型。API Key 只通过环境变量或 `data/secrets.yaml` 注入：

```yaml
# data/secrets.yaml  (chmod 600，禁止提交)
providers:
  deepseek: sk-...
```

配置优先级（低 → 高）：内置默认 → `config/mini-agent.yaml` → `data/workspace.yaml` → 环境变量。完整示例与 JSON Schema：

- [`config/mini-agent.example.yaml`](config/mini-agent.example.yaml)
- [`config/config.schema.json`](config/config.schema.json)（`cd backend && uv run python ../scripts/export_config_schema.py` 可重新生成）

## 已实现能力（最小版本）

| 领域 | 能力 |
|---|---|
| 会话 | 多轮对话、持久化、标题自动生成/重命名、置顶、归档、搜索、Fork、从用户消息编辑并创建分支 |
| 运行时 | 单会话单写者、Turn/Step 状态机、预算（步数/时长/费用）、死循环检测、暂停/恢复/取消、优雅停机 |
| 模型 | OpenAI Chat Completions 兼容流式协议、离线 Mock、指数退避重试（429/5xx/网络）、`Retry-After`、可读错误 + 原始错误码 + request ID |
| 缓存 | 消息 `raw_json` 原样回放、工具定义固定排序、只追加前缀、`prefix_hash`/缓存命中记录、调试面板 |
| 工具 | `read_file` / `list_dir` / `glob` / `grep` / `write_file` / `edit_file` / `multi_edit` / `shell` / `shell_background` / `ask_question` / `todo_write` / `wait` / `task_status` / `task_cancel` / `web_fetch` |
| 安全 | JSON Schema 严格校验、权限 allow/ask/deny、通配符与管道/`&&` 分段匹配、路径穿越与符号链接逃逸防护、SSRF 防护、危险命令黑名单、子进程环境变量脱敏 |
| 交互 | `ask_question` 四种题型、权限「仅允许一次 / 始终允许 / 拒绝」、待回答状态持久化、刷新恢复 |
| 任务 | `taskId`、后台命令、`wait` 兜底、完成 system 通知并推动模型自检、服务重启后明确失败并通知 |
| 上下文 | 手动 `/compact`、自动压缩、压缩摘要保留目标/约束/待办/待回答问题/taskId、原始历史不删除 |
| 事件 | 全局递增事件 ID、SSE `Last-Event-ID` 断线续传、快照 + 增量重建、流式内容与事件原子落库 |
| 用量 | Provider usage、缓存命中/未命中、费用估算、上下文占用与明细 |
| 前端 | 两栏/可折叠可拖拽侧栏、抽屉式移动端、Markdown + GFM + 高亮 + XSS 过滤、工具调试面板、队列编辑、目标面板、命令面板、快捷键、中文/英文、浅色/深色/跟随系统、草稿持久化、IME 防误发 |
| 数据 | Alembic 迁移、WAL、导出 Markdown/JSON、只读分享（可撤销、可过期、脱敏）、调试请求前缀 |
| 质量 | 30 个后端测试、13 个前端单元测试、8 个 Playwright E2E、Ruff/ESLint/tsc 门禁、对比度测试 |

> 最小版本的边界与未实现项见 [当前范围与限制](#当前范围与限制)。不要把本服务直接暴露到公网：它没有账号体系与鉴权。

## 架构

```text
React + Vite UI
  │  REST 操作                 SSE / Last-Event-ID
  ▼                              ▼
FastAPI API ─────────────── Event Store (SQLite, 全局递增 id)
  │                              ▲
  ▼                              │
Session Runtime (单会话单写者) ──┘
  ├─ Model Gateway (OpenAI-compatible / Mock, 重试, 用量)
  ├─ Tool Registry + Permission Evaluator + Path/SSRF Guard
  ├─ Interaction Manager (ask_question / permission, 持久化 + Future)
  ├─ Task Manager (taskId, 后台 shell, 完成通知)
  └─ Compaction (稳定摘要 + 最近 Turn 原文)
        │
        ▼
SQLite (WAL) + Alembic migrations + artifacts/checkpoints
```

关键设计：

- **事件驱动**：所有用户可见状态变化都写入 `event` 表并广播；前端先拉快照，再用 `Last-Event-ID` 续传。
- **单会话单写者**：每个会话一个 `asyncio.Lock`，后台任务与用户消息通过持久化队列解耦。
- **前缀缓存**：消息 `raw_json` 原样回放；工具定义按名称排序；流式内容与对应 delta 事件在同一事务提交，避免刷新后“缺一段/重复一段”。
- **权限默认安全**：读工具默认允许，写/命令/网络默认询问；Plan 模式强制只读；拒绝后用签名阻断相同参数的重复调用。
- **可恢复**：启动时把运行中的 Turn、工具、任务、交互标记为失败/取消，补齐 tool result，并追加可读的恢复通知；不会自动重放危险操作。

更完整的设计说明与权衡记录见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

## 目录结构

```text
backend/
  mini_agent/
    api.py            REST + SSE，统一错误/请求 ID
    runtime.py        Turn 状态机、工具编排、暂停/恢复、交互
    model_gateway.py  OpenAI-compatible / Mock、重试、用量、前缀序列化
    tools.py          工具 Schema 与受限执行
    security.py       权限、路径、SSRF、脱敏
    tasks.py          taskId 后台任务
    compaction.py     压缩摘要与模型消息重建
    recovery.py       重启恢复
    serialization.py  快照 DTO
    db.py / models.py / migrations/
  tests/              验收、网关、安全、配置测试
frontend/
  src/                React UI、SSE 状态折叠、单元测试
  e2e/                Playwright 端到端
config/               示例配置与 JSON Schema
docs/                 架构、验收证据、自检、截图
scripts/              start / dev / test / schema / e2e server
```

## 测试与质量门禁

```bash
./scripts/test.sh            # 后端 ruff+pytest，前端 tsc+eslint+vitest+build
RUN_E2E=1 ./scripts/test.sh  # 额外运行 Playwright
```

单独运行：

```bash
cd backend && uv run pytest -q
cd frontend && pnpm test && pnpm typecheck && pnpm lint && pnpm build
cd frontend && pnpm exec playwright test
```

验收清单逐项证据（含测试名、场景和结果）见 [`docs/ACCEPTANCE.md`](docs/ACCEPTANCE.md)。

## 配置说明

- `config/mini-agent.example.yaml` 可直接复制为 `config/mini-agent.yaml`；支持 camelCase 与 snake_case。
- 用户设置页修改的常用配置写入 `data/workspace.yaml`，重启后仍生效。
- 配置解析失败会快速失败并给出文件路径，不会静默使用半份配置。
- API Key 支持 `${env:VAR}`、环境变量和 `data/secrets.yaml`；接口只返回“已配置/末尾 4 位”。
- 数据目录默认 `./data`，可通过 `MINI_AGENT_DATA_DIR` 修改；工作区默认 `<data>/workspaces/demo`。

## API 与事件

- OpenAPI：`/docs`、`/openapi.json`
- 健康检查：`/api/v1/healthz`、`/api/v1/readyz`
- SSE：`GET /api/v1/sessions/{id}/events`，请求头 `Last-Event-ID`
- 事件类型：`turn.*`、`content.delta`、`reasoning.delta`、`tool.*`、`task.updated`、`interaction.*`、`queue.updated`、`usage.updated`、`session.status`、`compaction.created`、`error`、`model.retry` 等
- 错误响应统一为 `{ code, message, data, timestamp, requestId }`

## 数据与备份

- 数据库：`data/mini-agent.sqlite3`（WAL 模式）
- 迁移：`backend/migrations/`，启动时自动 `alembic upgrade head`
- 大输出：`data/artifacts/`
- 写文件检查点：`data/checkpoints/`（当前版本已记录，撤销 UI 未实现）
- 备份：直接备份整个 `data/` 目录（建议停服或使用 SQLite `.backup`）

## 安全说明

- 子进程使用受限环境变量，不继承 `DEEPSEEK_API_KEY` 等密钥。
- 工具输出、网页、文件内容都视为不可信数据；系统提示明确禁止绕过权限。
- `web_fetch` 禁止 localhost、内网、链路本地和云元数据地址，并对重定向逐跳校验。
- 文件工具拒绝 `..`、绝对路径逃逸和符号链接逃逸。
- 日志、导出、分享、错误响应统一脱敏。
- **无鉴权**：默认绑定 `127.0.0.1`；如需多人使用，请自行增加反向代理鉴权、TLS、限流与租户隔离。

## 当前范围与限制

最小版本优先保证附录 D 的 11 条验收项和核心 P0 流程，以下能力明确未完成（不是隐藏缺陷）：

- Skill / MCP 管理仅有入口与说明，未实现运行时加载；Agent 仅内置 Build/Plan。
- 服务商/模型管理为只读展示，新增服务商需改配置文件；未实现 OAuth、连接测试、备用模型切换。
- 无账号、多租户、审计、配额、Prometheus 指标；分享只有只读链接，无导入。
- 只支持 OpenAI Chat Completions 协议；Anthropic Messages / OpenAI Responses 适配器未实现。
- 工具调用按顺序执行（只读工具未做并发批处理）；背景任务未设最大时长。
- 附件仅支持前端追加文本内容，不支持图片/PDF 上传与视觉输入。
- 文件检查点未提供撤销/重做 API；消息列表使用 `content-visibility` 优化，未实现真正虚拟列表。
- 上下文压缩使用确定性本地摘要，未调用独立便宜模型。
- 未实现多模型 fallback、企业策略层、自定义命令/Hook/插件、MCP、Skill、子 Agent。

## 截图

| 聊天（浅色） | 聊天（深色） |
|---|---|
| ![light](docs/screenshots/chat-light.png) | ![dark](docs/screenshots/chat-dark.png) |

| 权限确认 | 设置 |
|---|---|
| ![permission](docs/screenshots/permission-light.png) | ![settings](docs/screenshots/settings-light.png) |

移动端与分享页：`docs/screenshots/mobile.png`、`docs/screenshots/share.png`。

## 常见问题

**没有 API Key 能用吗？** 能。默认 `mock/echo` 离线模型会返回演示回复，并支持工具/权限/任务/问题流程。

**为什么收到的回复是“准备调用工具”？** Mock 模型在演示工具调用；批准权限后会继续执行并给出最终回复。

**刷新后流式内容会丢吗？** 不会。快照包含已落库的部分内容，SSE 从 `Last-Event-ID` 续传；流式内容与 delta 事件同事务提交。

**为什么上下文用量和 provider usage 不一样？** Header 的上下文占用是本地估算，用于预算和压缩；费用与 token 统计以 provider `usage` 为准。

**为什么设置里改不了 API Key？** 出于密钥不进入浏览器/普通配置的约束，密钥通过环境变量或 `data/secrets.yaml` 注入。

---

实现与自检记录见 [`docs/SELF_CHECK.md`](docs/SELF_CHECK.md)。本项目未创建 Git 提交（仓库已有用户历史，提交需用户授权）。
