export type SessionStatus =
  | 'idle'
  | 'running'
  | 'waiting_user'
  | 'paused'
  | 'compacting'
  | 'error';

export type TurnStatus = 'running' | 'completed' | 'paused' | 'failed' | 'cancelled';

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
  rootPath: string;
  createdAt: string;
}

export interface AgentInfo {
  id: string;
  name: string;
  description: string;
  readOnly: boolean;
}

export interface MessageContent {
  text: string;
  reasoning: string;
  toolCalls: Array<{ id: string; name: string; arguments: string }>;
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
  enabled: boolean;
  configured: boolean;
  key_hint: string;
  models: Array<{
    id: string;
    name: string;
    context_window: number;
    max_output: number;
    capabilities: string[];
    reasoning_levels: string[];
  }>;
}

export interface PermissionRule {
  default: 'allow' | 'ask' | 'deny';
  allow: string[];
  deny: string[];
}

export interface PublicConfig {
  general: { language: 'zh-CN' | 'en-US'; theme: 'light' | 'dark' | 'system'; notifications: boolean };
  defaults: { model: string; reasoning_level: string; agent: string; mode: 'build' | 'plan' };
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
  compaction: { enabled: boolean; keep_recent_tokens: number; trigger_ratio: number };
  permissions: Record<string, PermissionRule>;
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

export type StreamStatus = 'connecting' | 'connected' | 'reconnecting' | 'offline';
