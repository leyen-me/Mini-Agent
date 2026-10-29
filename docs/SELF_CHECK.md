# 交付自检报告（Final Self-Check）

- 日期：2026-10-28（Asia/Shanghai）
- 版本：0.1.0
- 环境：macOS / Python 3.13.11（uv 管理）/ Node.js 24.22.0 / pnpm 10.13.1 / Chromium（Playwright）
- 自检范围：`create-common-agent-guide.md` 附录 D + 本仓库 `AGENTS.md` 的交付要求
- 最终复跑：2026-10-28 20:49（`RUN_E2E=1 ./scripts/test.sh`，退出码 0，结果与下表一致）

## 1. 实际执行结果

命令在仓库根目录执行。

| 检查 | 命令 | 结果 |
|---|---|---|
| 后端 lint | `cd backend && uv run ruff check mini_agent tests` | ✅ All checks passed |
| 后端格式 | `cd backend && uv run ruff format --check mini_agent tests` | ✅ 25 files already formatted |
| 后端测试 | `cd backend && uv run pytest -q` | ✅ **30 passed** |
| 前端类型检查 | `cd frontend && pnpm typecheck` | ✅ 无错误 |
| 前端 lint | `cd frontend && pnpm lint` | ✅ 无错误/警告 |
| 前端单元测试 | `cd frontend && pnpm test` | ✅ **13 passed / 7 files** |
| 前端生产构建 | `cd frontend && pnpm build` | ✅ 成功（JS 732.82 KB，gzip 218.34 KB；CSS 50.35 KB） |
| E2E | `cd frontend && pnpm exec playwright test` | ✅ **8 passed** |
| 一键启动 | `./scripts/start.sh`（端口 8791） | ✅ `/api/v1/healthz` 200，`/` 200 |
| 配置 Schema | `cd backend && uv run python ../scripts/export_config_schema.py` | ✅ 生成并与示例一致（测试 `test_example_config_matches_schema_and_loads`） |

完整质量门禁命令：

```bash
RUN_E2E=1 ./scripts/test.sh
# 结果：后端 30 passed，前端 13 passed，Playwright 8 passed，退出码 0
```

## 2. 附录 D 逐项结论

| # | 验收项 | 证据 | 结论 |
|---|---|---|---|
| 1 | 刷新恢复流式输出 / ask_question / 权限弹窗 / 排队消息 | `test_a1_*`、E2E `refresh during streaming…`、`ask_question round trip`、`permission confirmation…`、`queue while streaming…` | ✅ |
| 2 | 重启后未完成任务明确失败并通知 | `test_a2_restart_marks_work_failed_and_notifies` | ✅ |
| 3 | 暂停中止工具且每个 tool_call 有结果 | `test_a3_pause_aborts_tools_and_fills_results` | ✅ |
| 4 | 连续 5 轮前缀逐字节一致，缓存命中增长 | `test_a4_five_turn_prefix_is_byte_stable_and_cache_grows` | ✅ |
| 5 | 指数退避重试 3 次，失败可读原因 + 原始错误码 | `test_retry_*`、`test_auth_error_is_not_retried` | ✅ |
| 6 | 日志/导出/分享不含完整 API Key | `test_a6_secrets_absent_from_export_share_and_logs` | ✅ |
| 7 | 权限拒绝后不用相同参数重试 | `test_a7_permission_denial_prevents_identical_retry` | ✅ |
| 8 | 长任务完成后模型收到 system 通知并自检 | `test_a8_long_task_notifies_model_for_self_check` | ✅ |
| 9 | 中文输入法选词回车不误发送 | `src/tests/Composer.test.tsx` | ✅ |
| 10 | 压缩后目标/约束/待回答问题/taskId 不丢 | `test_a10_compaction_preserves_goal_questions_and_task_ids` | ✅ |
| 11 | 浅色/深色对比度 + 状态不只靠颜色 | `contrast.test.ts`、`StatusBadge.test.tsx`、截图 | ✅ |

## 3. 手工/浏览器验证

- 1440×1000 桌面：首页、聊天、权限确认、设置、命令面板；截图见 `docs/screenshots/`。
- 390×844 移动端：抽屉侧栏、权限面板、Composer 工具栏不溢出、发送/停止按钮可见。
- 深色/浅色切换：刷新后保持；系统主题跟随。
- 分享页：只读、无 system prompt、已脱敏、过期时间显示。
- 发送后 Composer 清空，草稿不会在布局切换时“复活”。
- SSE 断线重连：先刷新快照，再用 `Last-Event-ID` 续传；E2E 刷新测试无重复消息。

## 4. 未执行或未达成的检查

| 项目 | 状态 | 说明 |
|---|---|---|
| Docker 镜像构建 | ⚠️ 未执行 | 本机没有 Docker（`docker --version` 不存在）；Dockerfile/Compose 已提供但未在本机构建验证 |
| 真实 DeepSeek 请求 | ⚠️ 未执行 | 无有效 API Key；所有自动化测试使用 Mock/Scripted Gateway，避免外部依赖与费用 |
| 性能基准 | ⚠️ 未执行 | 首 token 渲染 <100ms、1 万条消息滚动、冷启动 <3s 未做严格测量；大 bundle 警告仍在 |
| 混沌故障注入 | ⚠️ 部分 | 已模拟数据库中断状态 + 重启 + 浏览器刷新；未做真实 kill -9/断网 |
| 账号/多租户/审计 | ❌ 未实现 | 最小版本明确不支持，见 README 限制 |
| Skill/MCP/子 Agent | ❌ 未实现 | 管理入口仅说明；运行时未加载 |
| 文件检查点撤销/重做 | ⚠️ 部分 | 写文件已记录 checkpoint，但没有撤销 API/UI |
| 虚拟列表 | ❌ 未实现 | 使用 `content-visibility` 优化，未做真正虚拟化 |
| Git 提交 | ⚠️ 未执行 | 仓库已有用户历史；按 AGENTS.md「提交需授权」，未擅自 commit/push |

## 5. 对 `AGENTS.md` 的遵循说明

- **工程化/分层**：`api/runtime/model_gateway/tools/security/tasks/compaction/recovery` 边界清晰；API 不直接堆业务 SQL。
- **迁移**：Alembic 初始迁移随仓库提供，启动自动升级；SQLite WAL。
- **配置**：示例 YAML、JSON Schema、`.env.example`、启动校验、密钥分离、日志脱敏。
- **测试**：后端 30、前端 13、E2E 8；覆盖认证/权限边界、数据写入、状态恢复、并发取消、SSE。
- **安全**：输入 DTO/JSON Schema、路径/SSRF/命令注入防护、子进程环境脱敏、日志与导出脱敏。
- **前端**：TypeScript strict、无 `any` 滥用、完整 Loading/Error/Empty/Disabled 状态、IME、a11y、响应式、主题对比度测试。
- **可观测**：结构化 JSON 日志、requestId、healthz/readyz、用量、请求调试面板。
- **破坏性变更/提交**：未删除用户文件、未覆盖 `.env`、未重新初始化 Git、未擅自提交。
- **例外**：未执行 Docker/真实 API/性能基准，原因已列于第 4 节；这些不构成本次附录 D 的阻塞项。

## 6. 复现步骤

```bash
# 1. 安装
cd backend && uv sync --all-groups
cd ../frontend && pnpm install --frozen-lockfile

# 2. 全量检查（含 E2E）
cd ..
RUN_E2E=1 ./scripts/test.sh

# 3. 启动
./scripts/start.sh
```

详细 API、配置、限制见 [`README.md`](../README.md)，验收项映射见 [`ACCEPTANCE.md`](ACCEPTANCE.md)。
