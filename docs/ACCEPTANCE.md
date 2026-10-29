# 附录 D 验收清单：逐项证据

> 目标：不是“口头声称通过”，而是每条都能用自动化测试复现。命令默认在仓库根目录执行。

## 一键执行

```bash
cd backend && uv run pytest -q
cd ../frontend && pnpm test && pnpm typecheck && pnpm lint && pnpm build
cd ../frontend && pnpm exec playwright test
```

最近一次完整结果见 [`SELF_CHECK.md`](SELF_CHECK.md)。

| # | 验收项 | 自动化证据 | 结果 |
|---|---|---|---|
| 1 | 刷新页面后，正在进行的流式输出、ask_question、权限确认弹窗、排队消息都能完整恢复 | 后端：`test_a1_refresh_restores_stream_queue_and_interactions`、`test_a1_ask_question_and_permission_survive_snapshot`、`test_a1_sse_last_event_id_replays_without_duplicates`；E2E：`refresh during streaming resumes without duplicate messages`、`queue while streaming…`、`ask_question round trip`、`permission confirmation…` | 通过 |
| 2 | 服务重启后，未完成的任务被恢复或明确标为失败，并通知了用户 | `test_a2_restart_marks_work_failed_and_notifies`：构造 running Turn/两个 tool_call/任务/交互后重启，断言 Turn failed、两个 tool_call 都有 result、任务 failed、system 通知包含 taskId | 通过 |
| 3 | 暂停时，运行中的工具能被中止，每个 tool_call 都有对应的结果 | `test_a3_pause_aborts_tools_and_fills_results`：`sleep 10` 工具 + 第二个未开始调用；暂停后两者均 cancelled + `USER_PAUSED`，两条 tool message；耗时 < 1s | 通过 |
| 4 | 连续 5 轮请求的前缀逐字节一致，缓存命中数持续增长 | `test_a4_five_turn_prefix_is_byte_stable_and_cache_grows`：断言 `prefix_text[n+1].startswith(prefix_text[n])`，cache_hit 单调且 >0；`test_prefix_serialization_is_append_only_and_deterministic` | 通过 |
| 5 | 断网后，按指数退避重试 3 次，失败时显示可读原因和原始错误码 | `test_retry_backoff_and_retry_after`（1s/2s，±20%）、`test_retry_after_header_is_honoured`、`test_retry_exhaustion_reports_readable_error_and_raw_code`（初始+3 次，rawCode/requestId）、`test_auth_error_is_not_retried`；前端 `error` 事件 toast 显示消息与 rawCode | 通过 |
| 6 | 日志、导出内容和分享内容中都搜不到完整的 API Key | `test_a6_secrets_absent_from_export_share_and_logs`：JSON/Markdown 导出、公开分享、日志 Formatter 均无 sentinel 且有 `[REDACTED]`；`test_redaction_covers_secret_registry_and_patterns` | 通过 |
| 7 | 被权限拒绝后，模型没有用相同参数重复调用 | `test_a7_permission_denial_prevents_identical_retry`：第二次相同 shell 调用返回 `REPEATED_CALL`，未产生第二次权限交互，也从未执行命令 | 通过 |
| 8 | 长任务完成后，模型收到了 system 通知并做了自检 | `test_a8_long_task_notifies_model_for_self_check`：第 3 次模型请求的末尾必须是含 taskId 与“自检”的 system 通知，快照中有完成通知 | 通过 |
| 9 | 中文输入法选词时按回车不会误发送 | `frontend/src/tests/Composer.test.tsx`：compositionStart + keyCode 229 不发送；compositionEnd 后正常发送；Shift+Enter 不发送 | 通过 |
| 10 | 上下文压缩后，目标、约束、待回答问题、任务 ID 都没有丢失 | `test_a10_compaction_preserves_goal_questions_and_task_ids`：摘要包含 objective/criteria/pending question/taskId；原始消息仍保留；压缩后的请求包含 `[Context compaction]` 摘要 | 通过 |
| 11 | 浅色和深色主题下，阅读区和代码区的对比度都合格；所有状态都不只靠颜色区分 | `frontend/src/tests/contrast.test.ts`（阅读/代码对比度 AA+）；`StatusBadge.test.tsx`（图标 + 文字 + aria-label）；截图 `docs/screenshots/chat-light.png` / `chat-dark.png` | 通过 |

## 补充的可靠性测试

| 场景 | 测试 |
|---|---|
| SSE 重放不重复、不遗漏 | `test_a1_sse_last_event_id_replays_without_duplicates` |
| 流中断丢弃不完整输出 | `test_runtime_discards_partial_output_when_model_restarts` |
| 路径穿越 / 符号链接逃逸 | `test_path_traversal_and_symlink_escape_are_rejected` |
| SSRF | `test_ssrf_blocks_private_and_metadata_hosts` |
| 工具 Schema 严格校验 | `test_tool_schema_rejects_unknown_fields_and_bad_types` |
| 子进程不继承密钥 | `test_shell_environment_strips_provider_credentials` |
| 配置层级 / yaml camelCase / 密钥不序列化 | `tests/test_config.py` |
| 配置示例与 JSON Schema 一致 | `test_example_config_matches_schema_and_loads` |
| 浏览器端到端（13 条） | `frontend/e2e/app.spec.ts` |

## 扩展能力测试证据

| 能力 | 自动化测试 |
|---|---|
| 服务商 CRUD、密钥掩码、模型管理 | `test_provider_crud_and_secret_masking` |
| Agent CRUD、会话默认 Skill、只读强制 Plan | `test_agent_crud_and_session_defaults` |
| 自定义命令 CRUD、参数渲染 | `test_custom_command_crud_and_render` |
| 附件上传、消息 raw 多模态与文本提取 | `test_upload_text_and_attachment_message` |
| 图片上传 + 辅助视觉模型 | `test_read_image_uses_auxiliary_vision_model` |
| 文件检查点 diff、撤销、重做 | `test_file_checkpoint_undo_redo` |
| 外部修改冲突阻断覆盖 | `test_external_file_modification_blocks_overwrite` |
| `.gitignore` 过滤文件树 | `test_workspace_ignores_gitignored_files` |
| 并行只读工具 | `test_parallel_readonly_tools_execute_and_persist_results` |
| 会话导出/导入/删除级联 | `test_session_export_import_and_delete` |
| Webhook 签名投递 + 备份 | `test_webhook_delivery_and_backup` |
| 恢复在重启前应用 | `test_pending_restore_is_applied_before_startup` |
| Skill 渐进加载 + 系统 Skill 编辑 | `test_skill_enable_and_load_tool`、`test_system_skill_create_and_delete` |
| MCP stdio 握手、工具发现、运行时调用 | `test_mcp_stdio_server_and_runtime_tool` |
| 插件安装/信任/启用/命令/Agent | `test_plugin_install_trust_enable_commands_and_agents` |
| Hook 环境变量与 stdin | `test_session_start_hook_runs_and_receives_event` |
| 主模型失败切换备用模型 | `test_runtime_falls_back_to_secondary_model` |
| Anthropic / OpenAI Responses 流适配与结构化输出 | `tests/test_adapters.py` |
| DeepSeek Flash 思考开关与强度映射 | `test_deepseek_flash_sends_thinking_switch_and_effort` |
| 工作区切换后主区/会话一致 | 前端逻辑修复；`Composer.test.tsx`（命令输入）、`GoalDialog.test.tsx` |
| 自定义命令直接输入参数进入预览 | `Composer.test.tsx` |
| Goal in_progress 不丢失 | `GoalDialog.test.tsx` |
| 顶栏主题持久化 | `Header` 复用 `changeTheme`；E2E settings theme |
| 版本号来自 bootstrap | `SettingsDialog` version prop |
| LLM 压缩摘要 | `test_manual_compaction_uses_model_summary` |
| 前台子 Agent 结果回传与父子关系 | `test_foreground_subagent_returns_summary` |
| 后台子 Agent 任务追踪 | `test_background_subagent_is_tracked_as_task` |
| 指标、日志导出、保留策略、策略查看 | `test_metrics_and_log_export` |
| 搜索解析与运行时工具 | `test_ddg_search_parser_extracts_results`、`test_web_search_tool_uses_configured_provider_and_parses_results` |
| 自动格式化 | `test_write_file_runs_auto_formatter_and_records_formatted_diff` |
| 压缩后重载 AGENTS.md | `test_compaction_reloads_agents_md_into_frozen_prompt` |
| Shell 沙箱选择 | `tests/test_sandbox.py` |
| 模型能力探测 | `test_model_capability_inference` |
| 工作区别名/删除 | `test_workspace_alias_and_delete` |
| Python SDK 鉴权与请求 | `tests/test_sdk.py` |
| 静态 API Token / 限流 / 代理防伪造 | `tests/test_api_auth.py` |
| 余额归一化与代理/CA 传递 | `test_provider_balance_normalizes_and_uses_network_config` |
| 恢复完整性校验与失败回滚 | `test_invalid_restore_rolls_back_old_database` |
| 权限 Grant 精确匹配 | `test_always_allow_grant_is_exact_and_cannot_be_glob_expanded` |
| Webhook 私网拦截 | `test_webhook_rejects_private_target_by_default` |
| 已有数据从旧 revision 升级到 head | `test_migrations_upgrade_existing_rows_to_head` |
| 虚拟列表 / 数学公式 / Mermaid | `VirtualList.test.tsx`、`Markdown.test.tsx`、`MermaidBlock.test.tsx` |

## 未纳入本清单的说明

- 性能指标（首 token 100ms、1 万条消息流畅、冷启动 <3s）未做严格基准；仅完成了功能级验证与长列表虚拟化实现。
- 故障注入以“构造数据库状态 + 重启 + 前端刷新”为主，未做真实网络断开/进程 kill -9 的混沌测试。
- 真实 DeepSeek / Anthropic / OpenAI 调用需要对应 Key；自动化测试使用 Mock/Scripted Gateway + 本地假 MCP/HTTP 服务，避免外部依赖和费用。
- 明确排除：账号/多租户/RBAC/审计/配额（原第 6 项）、高级分享隐藏/字段级脱敏（原第 11 项）。
