export type SessionStatus =
  'idle' | 'running' | 'waiting_user' | 'paused' | 'compacting' | 'error';

export type TurnStatus =
  'running' | 'completed' | 'paused' | 'failed' | 'cancelled';

export type ToolStatus =
  | 'validating'
  | 'awaiting_permission'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'denied'
  | 'cancelled'
  | 'timeout';

export interface Session {
  id: string;
  workspaceId: string;
  agentId: string;
  title: string;
  model: string;
  reasoningLevel: string;
  mode: 'build' | 'plan';
  status: SessionStatus;
  parentSessionId: string | null;
  forkFromMessageId: string | null;
  pinned: boolean;
  archived: boolean;
  lastError: string | null;
  createdAt: string;
  updatedAt: string;
  messageCount?: number;
  active?: boolean;
}

export interface Workspace {
  id: string;
  name: string;
  alias?: string;
  description?: string;
  rootPath: string;
  createdAt: string;
  updatedAt?: string;
}

export interface AgentInfo {
  id: string;
  name: string;
  description: string;
  readOnly: boolean;
  enabled?: boolean;
  builtin?: boolean;
  defaultModel?: string | null;
  defaultReasoningLevel?: string;
  tools?: string[] | null;
  permissions?: Record<string, string>;
  skills?: string[];
  mcp?: string[];
  structuredOutput?: Record<string, unknown> | null;
  systemPrompt?: string;
  createdAt?: string;
  updatedAt?: string;
}

export interface Attachment {
  id: string;
  filename: string;
  mime: string;
  kind: 'image' | 'pdf' | 'text' | 'file';
  url: string;
  width?: number | null;
  height?: number | null;
}

export interface MessageContent {
  text: string;
  reasoning: string;
  toolCalls: Array<{ id: string; name: string; arguments: string }>;
  attachments?: Attachment[];
}

export interface ChatMessage {
  id: string;
  sessionId: string;
  turnId: string | null;
  seq: number;
  role: 'system' | 'user' | 'assistant' | 'tool';
  content: MessageContent;
  status: 'streaming' | 'complete' | 'interrupted' | 'failed';
  clientMessageId: string | null;
  createdAt: string;
}

export interface ToolCall {
  id: string;
  sessionId: string;
  turnId: string;
  messageId: string;
  name: string;
  input: Record<string, unknown>;
  output: ToolOutput | null;
  status: ToolStatus;
  risk: 'low' | 'medium' | 'high';
  errorCode: string | null;
  startedAt: string | null;
  endedAt: string | null;
}

export interface ToolOutput {
  code: number;
  message: string;
  data: unknown;
  truncated: boolean;
  artifacts: string[];
  errorCode?: string;
}

export interface Turn {
  id: string;
  sessionId: string;
  seq: number;
  status: TurnStatus;
  startedAt: string;
  endedAt: string | null;
  errorCode: string | null;
  errorMessage: string | null;
  usage: UsageTotals;
}

export interface UsageTotals {
  inputTokens?: number;
  outputTokens?: number;
  cacheHitTokens?: number;
  cacheMissTokens?: number;
  cost?: number;
}

export interface SessionUsage extends UsageTotals {
  contextUsed: number;
  contextWindow: number;
  contextPercent: number;
  model: string;
}

export interface QueueItem {
  id: string;
  mode: 'steer' | 'queue';
  content: string;
  status: string;
  attachments?: string[];
  createdAt: string;
  clientMessageId?: string | null;
}

export interface QuestionOption {
  value: string;
  label: string;
}

export interface Question {
  id: string;
  header?: string;
  question: string;
  type: 'single' | 'multi' | 'text' | 'confirm';
  options?: QuestionOption[];
  default?: unknown;
  allow_skip?: boolean;
}

export interface Interaction {
  id: string;
  sessionId: string;
  turnId: string | null;
  type: 'ask_question' | 'permission' | 'budget';
  payload: {
    questions?: Question[];
    toolCallId?: string;
    tool?: string;
    input?: Record<string, unknown>;
    risk?: 'low' | 'medium' | 'high';
    category?: string;
    resource?: string;
    reason?: string;
  };
  status: 'pending' | 'answered' | 'cancelled';
  answer: Record<string, unknown> | null;
  createdAt: string;
}

export interface TaskInfo {
  id: string;
  sessionId: string;
  name: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
  progress: number;
  outputRef: string | null;
  summary: string;
  error: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface GoalTask {
  id: string;
  title: string;
  status: 'pending' | 'in_progress' | 'completed' | 'blocked';
  position: number;
}

export interface Goal {
  id: string;
  objective: string;
  criteria: string;
  status: string;
  tasks: GoalTask[];
  updatedAt: string;
}

export interface Compaction {
  id: string;
  summary: string;
  coveredUntilSeq: number;
  tokensBefore: number;
  tokensAfter: number;
  createdAt: string;
}

export interface Snapshot {
  session: Session;
  workspace: Workspace | null;
  agent: AgentInfo | null;
  messages: ChatMessage[];
  turns: Turn[];
  toolCalls: ToolCall[];
  queue: QueueItem[];
  interactions: Interaction[];
  tasks: TaskInfo[];
  usage: SessionUsage;
  goal: Goal | null;
  compaction: Compaction | null;
  skills?: SkillInfo[];
  mcp?: McpServer[];
  childSessions?: Array<{ id: string; title: string; status: SessionStatus }>;
  lastEventId: number;
  systemPrompt?: string;
  envInfo?: string;
  active?: boolean;
}

export interface ModelInfo {
  ref: string;
  provider: string;
  provider_name: string;
  protocol: string;
  name: string;
  context_window: number;
  max_output: number;
  capabilities: string[];
  reasoning_levels: string[];
  pricing: {
    input_per_million: number;
    output_per_million: number;
    cache_hit_per_million: number;
  };
}

export interface PublicProvider {
  id: string;
  name: string;
  protocol: string;
  base_url: string;
  auth_type?: string;
  enabled: boolean;
  configured: boolean;
  key_hint: string;
  headers?: Record<string, string>;
  oauth?: Record<string, unknown> | null;
  supports_balance?: boolean;
  models: Array<{
    id: string;
    name: string;
    context_window: number;
    max_output: number;
    capabilities: string[];
    reasoning_levels: string[];
    structured_output?: boolean;
    pricing?: {
      input_per_million: number;
      output_per_million: number;
      cache_hit_per_million: number;
    };
    supports_vision?: boolean;
    supports_structured_output?: boolean;
  }>;
}

export interface SkillInfo {
  name: string;
  description: string;
  version: string;
  triggers: string[];
  scope: 'builtin' | 'system' | 'workspace' | string;
  path: string;
  files: string[];
  enabled: boolean;
}

export interface McpTool {
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
}

export interface McpServer {
  id: string;
  name: string;
  description: string;
  transport: 'stdio' | 'http' | 'sse';
  command: string;
  args: string[];
  url: string;
  headers: Record<string, string>;
  env: Record<string, string>;
  tools: McpTool[];
  enabled: boolean;
  timeoutSeconds: number;
  scope: string;
  lastStatus: string;
  lastError: string | null;
  createdAt: string;
  updatedAt: string;
  sessionEnabled?: boolean;
}

export interface CommandInfo {
  id: string;
  name: string;
  description: string;
  promptTemplate: string;
  agentId: string | null;
  model: string | null;
  reasoningLevel: string | null;
  enabled: boolean;
  scope: string;
  pluginId?: string;
  createdAt: string;
  updatedAt: string;
}

export interface PluginInfo {
  id: string;
  name: string;
  version: string;
  description: string;
  trusted: boolean;
  enabled: boolean;
  path: string;
  manifest: Record<string, unknown>;
  tools: string[];
  createdAt: string;
  updatedAt: string;
}

export interface HookInfo {
  id: string;
  name: string;
  event: string;
  command: string;
  enabled: boolean;
  timeoutSeconds: number;
  scope: string;
  createdAt: string;
  updatedAt: string;
}

export interface WebhookInfo {
  id: string;
  name: string;
  url: string;
  events: string[];
  enabled: boolean;
  secretConfigured: boolean;
  lastStatus: string;
  lastError: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface BackupInfo {
  id: string;
  filename: string;
  size: number;
  includes: string[];
  createdAt: string;
}

export interface CheckpointInfo {
  id: string;
  turnId: string;
  path: string;
  operation: string;
  applied: boolean;
  createdAt: string;
}

export interface BalanceInfo {
  currency: string;
  total: number;
  granted: number;
  toppedUp: number;
  available: boolean;
  raw?: unknown;
}

export interface UploadInfo {
  upload: Attachment & { createdAt: string };
}

export interface PermissionRule {
  default: 'allow' | 'ask' | 'deny';
  allow: string[];
  deny: string[];
}

export interface PublicConfig {
  general: {
    language: 'zh-CN' | 'en-US';
    theme: 'light' | 'dark' | 'system';
    notifications: boolean;
  };
  defaults: {
    model: string;
    reasoning_level: string;
    agent: string;
    mode: 'build' | 'plan';
  };
  providers: PublicProvider[];
  models: ModelInfo[];
  model_configured: boolean;
  budget: {
    max_steps_per_turn: number;
    max_duration_seconds: number;
    max_cost_per_turn: number;
    max_repeat_failures: number;
    max_tool_output_chars: number;
  };
  compaction: {
    enabled: boolean;
    keep_recent_tokens: number;
    trigger_ratio: number;
    use_model?: boolean;
    model?: string;
    reload_project_rules?: boolean;
  };
  permissions: Record<string, PermissionRule>;
  network?: { proxy: string; ca_bundle: string; user_agent: string };
  search?: { provider: string; max_results: number };
  sandbox?: { mode: string; allow_network: boolean; docker_image: string };
  retention?: {
    events_days: number;
    uploads_days: number;
    artifacts_days: number;
    backups_days: number;
    logs_days: number;
  };
  tools?: { format: { enabled: boolean; commands: Record<string, string> } };
  subagents?: {
    max_depth: number;
    max_concurrent: number;
    timeout_seconds: number;
  };
  backup?: {
    enabled: boolean;
    include_artifacts: boolean;
    auto_before_migration: boolean;
  };
  api?: {
    require_token_remote: boolean;
    rate_limit_per_minute: number;
    token_configured: boolean;
  };
  policy_path?: string | null;
  source: string;
  config_path: string | null;
  workspace_root: string;
  data_dir: string;
}

export interface BootstrapData {
  config: PublicConfig;
  workspaces: Workspace[];
  agents: AgentInfo[];
  sessions: Session[];
  activeSessions: string[];
  recovery: { count: number; sessions: Array<Record<string, unknown>> };
  version: string;
}

export interface PermissionGrant {
  id: string;
  tool: string;
  pattern: string;
  scope: string;
  createdAt: string;
}

export interface DebugRequest {
  id: string;
  turnId: string | null;
  step: number;
  model: string;
  prefixHash: string;
  prefixTokens: number;
  cacheHitTokens: number;
  cacheMissTokens: number;
  retries: number;
  durationMs: number;
  createdAt: string;
}

export interface ShareInfo {
  id: string;
  token: string;
  url: string;
  expiresAt: string | null;
  revoked?: boolean;
  createdAt?: string;
}

export interface FileEntry {
  name: string;
  path: string;
  type: 'file' | 'dir';
  size: number | null;
}

export interface FileListing {
  path: string;
  isDirectory: boolean;
  entries: FileEntry[];
}

export interface StreamEvent {
  id: number;
  sessionId: string;
  type: string;
  [key: string]: unknown;
}

export type StreamStatus =
  'connecting' | 'connected' | 'reconnecting' | 'offline';
