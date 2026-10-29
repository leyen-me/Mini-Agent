# 架构与关键决策

## 1. 组件与职责

| 组件 | 代码 | 职责 |
|---|---|---|
| Web UI | `frontend/src` | 布局、会话/消息渲染、Composer、交互面板、设置、SSE 状态折叠 |
| API | `backend/mini_agent/api.py` | REST、SSE、统一错误包装、请求 ID、健康检查 |
| Session Runtime | `runtime.py` | 单会话单写者、Turn 状态机、模型流、工具编排（含并行只读批次）、暂停/恢复、队列、任务通知、子 Agent |
| Model Gateway | `model_gateway.py` | OpenAI Chat / Responses / Anthropic Messages / Mock 流解析、重试、fallback、usage、结构化输出、前缀 hash |
| Tool Registry | `tools.py` | 严格 JSON Schema、超时、截断、artifact、文件/命令/网络/视觉/Skill/搜索/子 Agent 工具 |
| Permission | `security.py` | allow/ask/deny 规则、通配符、分段匹配、路径/SSRF/沙箱/脱敏 |
| Extensions | `skills.py`, `mcp.py`, `plugins.py`, `commands.py`, `hooks.py` | 三层 Skill、MCP stdio/HTTP/SSE、声明式插件、命令渲染、生命周期 Hook |
| Files | `checkpoints.py`, `uploads.py`, `ignore.py`, `sandbox.py` | 检查点/diff/撤销、附件与视觉、ignore 过滤、shell 沙箱 |
| Ops | `ops.py`, `webhooks.py` | 备份/恢复、保留策略、日志导出、Prometheus、HMAC Webhook |
| Task Manager | `tasks.py` | `taskId`、后台 shell、取消、完成通知 |
| Compaction | `compaction.py` | 在 Turn 边界切分、摘要生成、模型消息重建 |
| Recovery | `recovery.py` | 重启后标记中断、补齐 tool result、通知 |
| Persistence | `db.py`, `models.py`, `migrations/` | SQLite WAL、Alembic、全部运行状态 |

## 2. 一次 Turn 的执行序列

```text
POST /sessions/{id}/messages
  → 幂等检查 clientMessageId
  → 会话空闲：写 user message + turn，spawn runtime
    会话运行中：写 queued_message（steer/queue），SSE queue.updated
runtime:
  loop:
    预算检查（step/时长/费用）
    注入 steer、终态任务通知
    必要时自动压缩
    从 raw_json 重建消息（只追加；工具定义排序）
    写 model_request(prefix_hash)，SSE 流式调用模型
       delta：同一事务更新 assistant 内容 + 写 content.delta 事件
       retry：清空部分输出 + message.reset + 重试
    持久化 assistant raw_json、usage、tool_calls
    无 tool_calls → 检查未完成任务/终态任务 → 必要时等待并再走一步 → 结束
    有 tool_calls → schema 校验 → 权限
      全部为可自动允许的只读工具：并行执行并回填
      否则：顺序执行并回填
    → 继续
  turn.completed + session.idle
  → 处理队列中的下一条消息
```

## 3. 持久化模型

核心表与 `create-common-agent-guide.md` §2.1 对齐：

`workspace`、`session`、`turn`、`message`、`tool_call`、`task`、`queued_message`、`pending_interaction`、`event`、`usage_record`、`compaction`、`goal`/`goal_task`、`model_request`、`permission_grant`、`share`、`checkpoint`。

扩展表：`provider`/`provider_model`、`session_skill`、`mcp_server`/`session_mcp`、`custom_command`、`plugin`、`hook`、`upload`、`file_read`、`webhook`/`webhook_delivery`、`backup`。

- `message.raw_json` 原样保存 provider 消息结构；恢复请求时不重渲染。
- `event.id` 全局自增；SSE `Last-Event-ID` 从该表续传。
- `tool_call` 与 `message`（role=tool）通过 `tool_call_id` 一一对应。
- 迁移用 Alembic；SQLite 开启 WAL、foreign_keys、busy_timeout。

## 4. 前缀缓存

要求：连续请求前缀逐字节一致。

实现：

1. 固定 system prompt（会话创建时冻结；日期冻结；不写当前时间）。
2. 只在末尾追加消息；Fork/编辑创建新会话分支。
3. 工具定义按名称排序，`json.dumps(..., sort_keys=True)`。
4. 前缀哈希基于 `TOOLS + MESSAGES` 的确定性拼接，不包含尾部包装；追加消息不会改变旧字节。
5. `model_request` 记录 `prefix_hash`、prefix tokens、`cache_hit_tokens`、`cache_miss_tokens`、retries、duration。
6. 流式 delta 与消息内容同事务提交，避免刷新时内容与事件 ID 错位。
7. 压缩是显式缓存失效点；压缩后重建稳定前缀。

测试：`backend/tests/test_acceptance.py::test_a4_five_turn_prefix_is_byte_stable_and_cache_grows` 与 `test_model_gateway.py::test_prefix_serialization_is_append_only_and_deterministic`。

## 5. 暂停、取消与恢复

- 暂停：设置 `stop_reason`、触发工具取消事件、取消 turn task。
- 清理：从 assistant `tool_calls` 重建完整调用列表，为每个调用补齐 `USER_PAUSED` tool result；未开始的调用也会补 ToolCall 行。
- 取消：保留已产生内容，Turn = `cancelled`，会话回 `idle`。
- 恢复：追加“用户恢复”系统消息，开启新 Turn；不重放危险操作。
- 服务重启：`recovery.py` 将 running 状态改为 failed/cancelled，补齐 tool result，任务标记失败，写入 system 通知。

## 6. 权限与不可信内容

- 分类：read/write/shell/network/interaction/task/mcp/plugin/subagent。
- 规则：deny 优先；shell 按 `|`、`&&`、`;` 分段逐段匹配；支持 `*` 通配符。
- Plan 模式强制只读（写、命令、子 Agent、MCP、插件全部拒绝）。
- 子 Agent 始终沿用父 Agent 的工具白名单与权限，请求的其他 Agent 只贡献 Prompt/模型/更严格的只读约束，杜绝权限提升。
- 拒绝后使用 `denied_signatures` 阻止相同工具+参数重复调用；相同参数连续失败 N 次也阻断。
- 所有工具输出标记 `untrusted`；网页/文件/命令内容在系统提示中被定义为数据，不能绕过权限。
- 子进程只继承 PATH/HOME/LANG 等白名单变量，API Key 不出现在子进程环境。
- `shell` / `shell_background` 默认 `auto` 沙箱：macOS `sandbox-exec`、Linux `bwrap`、Docker；限制写入与网络，不可用时回退并返回 warning。
- `web_fetch` 对每一跳 DNS 解析结果做公网地址校验，拒绝 localhost/内网/链路本地/元数据地址。

## 7. 关键权衡（ADR 摘要）

| 决策 | 备选 | 理由 |
|---|---|---|
| SQLite + WAL + Alembic | Postgres / 无迁移 | 单机最小部署，成本低；保留迁移路径 |
| REST + SSE | WebSocket | 与 `Last-Event-ID` 原生契合，断线恢复简单 |
| 单进程 asyncio Runtime | Celery/队列 | 最小版本避免额外基础设施；任务状态已持久化，可平滑替换 |
| 前端快照 + 事件折叠 | 只存前端状态 | 刷新/重启可恢复；事件可重放 |
| 流式 delta 同事务落库 | 批量 flush | 刷新恢复不会缺段或重复；代价是写放大，最小规模可接受 |
| 权限询问持久化 | 内存 Future | 刷新后弹窗仍在；Future 只在进程内，重启时明确取消 |
| 确定性摘要 + 可选 LLM 摘要 | 只做 LLM 摘要 | 默认无额外费用、可测试；配置独立便宜模型时更连贯；压缩后可重载 AGENTS/Skill/MCP |
| 多协议适配器 | 只支持 OpenAI Chat | 兼容 Responses/Anthropic 与 OAuth/余额；统一内部消息协议 |
| 前端虚拟列表 | 全量渲染 | 长对话只渲染可视区；代价是 DOM 测量复杂度 |

## 8. 可靠性与失败模式

- **模型 429/5xx/网络**：指数退避 + 抖动，最多 3 次重试；尊重 `Retry-After`；401/402/参数错误不重试。
- **流中断**：丢弃不完整输出，前端收到 `message.reset`，最终只保留重试成功的内容。
- **工具超时/取消**：进程组 SIGTERM→SIGKILL；每个 tool_call 都有结果。
- **SSE 断线**：前端先刷新快照，再以新 `Last-Event-ID` 连接；服务端先订阅再回放，按事件 ID 去重。
- **重启**：不自动执行任何危险工具；显式失败并通知。
- **预算超限**：暂停并追加系统通知，等待用户恢复/取消。
- **死循环**：同工具同参数失败达到阈值后阻断并提示换方法。

## 9. 性能与运维边界

- 单机 SQLite 串行写；适合个人/小团队。
- 长对话消息列表超过 40 个块后使用 `@tanstack/react-virtual` 虚拟化；后台任务、工具卡片仍按需渲染。
- 管理中心按需懒加载；Mermaid/KaTeX 按需拆包；主包仍包含 Markdown/高亮。
- 备份使用 SQLite backup API + zip；恢复暂存到磁盘并在下次启动应用。保留策略与日志轮转避免磁盘无限增长。
- 静态 API Token 仅提供单密钥边界，不等价于账号/租户隔离。
