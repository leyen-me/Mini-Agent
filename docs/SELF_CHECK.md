# 交付自检报告（Final Self-Check）

- 日期：2026-10-29（Asia/Shanghai）
- 版本：0.2.0
- 环境：macOS / Python 3.13.11（uv 管理）/ Node.js 24.22.0 / pnpm 10.13.1 / Chromium（Playwright）
- 自检范围：`create-common-agent-guide.md` 附录 D + `AGENTS.md` 交付要求 + 需求追加项（除第 6、11 项）
- 最终复跑：2026-10-29（`RUN_E2E=1 ./scripts/test.sh`，退出码 0）

## 1. 实际执行结果

| 检查 | 命令 | 结果 |
|---|---|---|
| 后端 lint | `cd backend && uv run ruff check mini_agent tests` | ✅ All checks passed |
| 后端格式 | `cd backend && uv run ruff format --check mini_agent tests` | ✅ |
| 后端测试 | `cd backend && uv run pytest -q` | ✅ **78 passed** |
| 前端类型检查 | `cd frontend && pnpm typecheck` | ✅ 无错误 |
| 前端 lint | `cd frontend && pnpm lint` | ✅ 无错误/警告 |
| 前端单元测试 | `cd frontend && pnpm test` | ✅ **23 passed / 12 files** |
| 前端生产构建 | `cd frontend && pnpm build` | ✅ 成功（主包约 1.07 MB，gzip 318 KB；Mermaid/KaTeX/管理中心按需拆包） |
| E2E | `cd frontend && pnpm exec playwright test` | ✅ **13 passed** |
| 配置 Schema | `cd backend && uv run python ../scripts/export_config_schema.py` | ✅ 已重新生成并随仓库更新 |

一键门禁：

```bash
RUN_E2E=1 ./scripts/test.sh
# 后端结果：78 passed；前端：23 passed；Playwright：13 passed；退出码 0
```

## 2. 附录 D 逐项结论

| # | 验收项 | 证据 | 结论 |
|---|---|---|---|
| 1 | 刷新恢复流式输出 / ask_question / 权限弹窗 / 排队消息 | `test_a1_*`；E2E `refresh during streaming…`、`ask_question round trip`、`permission confirmation…`、`queue while streaming…` | ✅ |
| 2 | 重启后未完成任务明确失败并通知 | `test_a2_restart_marks_work_failed_and_notifies` | ✅ |
| 3 | 暂停中止工具且每个 tool_call 有结果 | `test_a3_pause_aborts_tools_and_fills_results` | ✅ |
| 4 | 连续 5 轮前缀逐字节一致，缓存命中增长 | `test_a4_five_turn_prefix_is_byte_stable_and_cache_grows` | ✅ |
| 5 | 指数退避重试 3 次，失败可读原因 + 原始错误码 | `test_retry_*`、`test_auth_error_is_not_retried` | ✅ |
| 6 | 日志/导出/分享不含完整 API Key | `test_a6_secrets_absent_from_export_share_and_logs` | ✅ |
| 7 | 权限拒绝后不用相同参数重试 | `test_a7_permission_denial_prevents_identical_retry` | ✅ |
| 8 | 长任务完成后模型收到 system 通知并自检 | `test_a8_long_task_notifies_model_for_self_check` | ✅ |
| 9 | 中文输入法选词回车不误发送 | `src/tests/Composer.test.tsx` | ✅ |
| 10 | 压缩后目标/约束/待回答问题/taskId 不丢 | `test_a10_compaction_preserves_goal_questions_and_task_ids`、`test_manual_compaction_uses_model_summary` | ✅ |
| 11 | 浅色/深色对比度 + 状态不只靠颜色 | `contrast.test.ts`、`StatusBadge.test.tsx`、截图 | ✅ |

## 3. 追加需求完成情况

| 模块 | 状态 | 证据/位置 |
|---|---|---|
| Skill（三层、渐进加载） | ✅ | `skills.py`、`test_skill_*`、管理中心 Skill |
| MCP（stdio/HTTP/SSE、命名空间） | ✅ | `mcp.py`、`test_mcp_stdio...`、管理中心 MCP |
| 子 Agent（深度/并发/权限上限、前台/后台） | ✅ | `runtime.spawn_subagent`、`test_foreground_subagent...`、`test_background_subagent...` |
| 服务商/模型 CRUD、OAuth、余额、多协议 | ✅ | `providers.py`、`test_provider_crud...`、`test_adapters.py` |
| DeepSeek Flash 思考强度（off/low/medium/high/max） | ✅ | `test_deepseek_flash_sends_thinking_switch_and_effort`；模型元数据可在管理中心编辑 |
| 附件、PDF、图片视觉 | ✅ | `uploads.py`、`test_upload...`、`test_read_image...` |
| Web 搜索 | ✅ | `tools.web_search`、`test_ddg_search_parser...` |
| 检查点、diff、撤销/重做、自动格式化 | ✅ | `checkpoints.py`、`test_file_checkpoint...`、`test_external...`、Diff UI |
| `.gitignore` / 文件内容搜索 | ✅ | `ignore.py`、`test_workspace_ignores...`、Sidebar 搜索、E2E file search |
| 工作区别名/描述/删除 | ✅ | Workspace API + 管理中心数据页 |
| 会话导入/删除 | ✅ | `/sessions/import`、DELETE、`test_session_export_import...` |
| 自定义命令/预览 | ✅ | `commands.py`、`test_custom_command...`、E2E custom command |
| 插件（安装/信任/启用） | ✅ | `plugins.py`、`test_plugin_install...`、管理中心插件 |
| Hook | ✅ | `hooks.py`、`test_session_start_hook...`、管理中心 Hook |
| 备份/恢复、保留策略、日志导出 | ✅ | `ops.py`、`test_webhook_delivery_and_backup`、`test_pending_restore_is_applied_before_startup`、`test_metrics_and_log_export` |
| Prometheus / Webhook | ✅ | `/metrics`、`webhooks.py`、管理中心 Webhook |
| 静态 API Token / 限流 / 企业策略 | ✅ | `api.py` middleware、`config/policy.example.yaml`；`test_api_auth.py`、`test_always_allow_grant_is_exact...` |
| 虚拟列表 / KaTeX / Mermaid | ✅ | `MessageList.tsx`、`MermaidBlock.tsx`、`Markdown.tsx`；`VirtualList.test.tsx`、`Markdown.test.tsx`、`MermaidBlock.test.tsx` |
| 模型推理级别编辑 UI | ✅ | `ProvidersPanel.tsx`、`ProvidersPanel.test.tsx` |
| Python SDK | ✅ | `sdk/python/mini_agent_sdk`、`sdk/README.md`、`tests/test_sdk.py` |
| 能力探测 / Shell 沙箱 / 自动格式化 / 压缩重载规则 | ✅ | `test_model_capability_inference`、`tests/test_sandbox.py`、`test_write_file_runs_auto_formatter...`、`test_compaction_reloads_agents_md...` |
| 工作区别名/删除、静态 API Token/限流/代理防伪造 | ✅ | `test_workspace_alias_and_delete`、`test_api_auth.py` |
| 工作区切换、命令参数预览、Goal in_progress、主题/版本 | ✅ | `App.tsx`、`Composer.test.tsx`、`GoalDialog.test.tsx`、`SettingsDialog` |
| 恢复失败回滚、Webhook 私网拦截、Grant 精确匹配 | ✅ | `test_invalid_restore_rolls_back...`、`test_webhook_rejects_private...`、`test_always_allow_grant...` |
| 旧 revision 有数据升级 | ✅ | `test_migrations_upgrade_existing_rows_to_head` |
| 余额归一化、代理/CA | ✅ | `test_provider_balance_normalizes_and_uses_network_config` |
| 只读工具并行批处理 | ✅ | `test_parallel_readonly_tools_execute_and_persist_results` |
| 明确排除：账号/多租户/RBAC/审计/配额 | ⛔ | 按需求不做 |
| 明确排除：高级分享隐藏/字段级隐藏 | ⛔ | 按需求不做 |

## 4. 手工/浏览器验证

- 1440×960 桌面：聊天、权限确认、设置、管理中心、附件、命令预览、文件搜索、Diff、分享。
- 390×844 移动端：抽屉侧栏、Composer 工具栏、交互面板不溢出；新增管理中心为单列布局。
- 深色/浅色切换、语言切换、刷新后保持；E2E 覆盖。
- SSE 断线重连：先刷新快照，再按 `Last-Event-ID` 续传。
- 附件上传后可在 Composer 预览/删除，发送后消息气泡展示；E2E 覆盖。
- 管理中心懒加载；管理操作后刷新 bootstrap/snapshot；E2E 覆盖服务商与 Skill 页。

## 5. 未执行/已知限制

| 项目 | 状态 | 说明 |
|---|---|---|
| Docker 构建 | ⚠️ 未执行 | 本机无 Docker；Dockerfile/Compose 保留 |
| 真实 DeepSeek/OpenAI/Anthropic 调用 | ⚠️ 未执行 | 无有效 Key；自动化使用 Mock/Scripted Gateway 与本地假 MCP/HTTP 服务 |
| 严格性能基准 | ⚠️ 未执行 | 未测量首 token、1 万条消息、冷启动指标；仅做功能级验证 |
| MCP SSE 长连接兼容 | ⚠️ 部分 | 按请求建立 SSE 通道；少数只支持复用事件流的服务器需适配 |
| 插件 UI 扩展 | ⚠️ 安全边界 | 以声明式元数据/管理面板展示，不执行第三方 JS |
| 账号/多租户/RBAC/审计/配额 | ⛔ | 按需求明确排除（原第 6 项） |
| 高级分享隐藏/字段级隐藏 | ⛔ | 按需求明确排除（原第 11 项） |

## 6. 对 AGENTS.md 的遵循说明

- **工程化/分层**：API、Runtime、Gateway、Tool、Extension、Ops 服务边界清晰；新增功能有独立模块。
- **迁移**：Alembic 多版本迁移、非空列 server_default、已在已有数据上验证升级。
- **配置**：示例 YAML、JSON Schema、`.env.example`、策略层、启动校验、密钥分离、日志脱敏。
- **测试**：后端 78、前端 23、E2E 13；覆盖权限、数据写入、恢复、并发取消、SSE、附件、扩展。
- **安全**：DTO/Schema 校验、路径/SSRF/命令注入防护、Shell 沙箱、子进程密钥隔离、API Token、限流、策略对配置节最高优先级。
- **前端**：TypeScript strict、Loading/Error/Empty/Disabled、IME、a11y、响应式、主题对比度、虚拟列表、数学/Mermaid。
- **可观测**：结构化 JSON 日志 + 轮转、requestId、healthz/readyz、用量、Prometheus、Webhook 投递记录。
- **破坏性变更/提交**：未删除用户文件、未覆盖 `.env`、未重新初始化 Git；提交需用户授权。

## 7. 复现步骤

```bash
cd backend && uv sync --all-groups
cd ../frontend && pnpm install --frozen-lockfile
cd ..
RUN_E2E=1 ./scripts/test.sh
./scripts/start.sh   # http://127.0.0.1:8787
```

详细 API、配置、限制见 [`README.md`](../README.md)，验收映射见 [`ACCEPTANCE.md`](ACCEPTANCE.md)。
