import type {
  BootstrapData,
  DebugRequest,
  FileListing,
  Goal,
  PermissionGrant,
  PublicConfig,
  Session,
  ShareInfo,
  Snapshot,
  SessionStatus,
  AgentInfo,
  PublicProvider,
  Workspace,
  SkillInfo,
  McpServer,
  CommandInfo,
  PluginInfo,
  HookInfo,
  WebhookInfo,
  BackupInfo,
  CheckpointInfo,
  BalanceInfo,
  UploadInfo,
  Attachment,
} from './types';

const BASE = (
  (import.meta.env.VITE_API_BASE as string | undefined) ?? '/api/v1'
).replace(/\/$/, '');
const TOKEN = (import.meta.env.VITE_API_TOKEN as string | undefined) ?? '';

export function currentApiToken(): string {
  try {
    return localStorage.getItem('mini-agent-api-token') || TOKEN;
  } catch {
    return TOKEN;
  }
}

export class ApiError extends Error {
  code: string;
  status: number;
  data: unknown;

  constructor(
    message: string,
    code: string,
    status: number,
    data: unknown = null,
  ) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
    this.data = data;
  }
}

interface Envelope<T> {
  code: string;
  message: string;
  data: T;
  timestamp?: string;
  requestId?: string;
}

async function request<T>(
  path: string,
  options: RequestInit & { signal?: AbortSignal } = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  const token = currentApiToken();
  if (token) headers.set('Authorization', `Bearer ${token}`);
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      ...options,
      headers,
      credentials: 'same-origin',
    });
  } catch (error) {
    if ((error as Error).name === 'AbortError') throw error;
    throw new ApiError(
      '无法连接后端服务，请确认服务已启动',
      'NETWORK_ERROR',
      0,
    );
  }
  const text = await response.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }
  if (!response.ok) {
    const envelope = (body ?? {}) as Partial<Envelope<unknown>>;
    throw new ApiError(
      envelope.message || `请求失败（HTTP ${response.status}）`,
      envelope.code || `HTTP_${response.status}`,
      response.status,
      envelope.data,
    );
  }
  if (body && typeof body === 'object' && 'data' in body) {
    return (body as Envelope<T>).data;
  }
  return body as T;
}

async function download(path: string, filename: string) {
  const response = await fetch(`${BASE}${path}`, {
    headers: currentApiToken()
      ? { Authorization: `Bearer ${currentApiToken()}` }
      : {},
  });
  if (!response.ok) {
    throw new ApiError('下载失败', `HTTP_${response.status}`, response.status);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 2_000);
}

export const api = {
  bootstrap(signal?: AbortSignal) {
    return request<BootstrapData>('/bootstrap', { signal });
  },
  config(signal?: AbortSignal) {
    return request<PublicConfig>('/config', { signal });
  },
  patchConfig(body: {
    general?: Record<string, unknown>;
    defaults?: Record<string, unknown>;
    network?: Record<string, unknown>;
    search?: Record<string, unknown>;
    tools?: Record<string, unknown>;
    sandbox?: Record<string, unknown>;
    retention?: Record<string, unknown>;
    compaction?: Record<string, unknown>;
    subagents?: Record<string, unknown>;
    backup?: Record<string, unknown>;
  }) {
    return request<PublicConfig>('/config', {
      method: 'PATCH',
      body: JSON.stringify(body),
    });
  },
  sessions(
    params: {
      workspaceId?: string;
      search?: string;
      archived?: boolean;
      limit?: number;
    } = {},
  ) {
    const query = new URLSearchParams();
    if (params.workspaceId) query.set('workspace_id', params.workspaceId);
    if (params.search) query.set('search', params.search);
    if (params.archived !== undefined)
      query.set('archived', String(params.archived));
    if (params.limit) query.set('limit', String(params.limit));
    const suffix = query.size ? `?${query.toString()}` : '';
    return request<Session[]>(`/sessions${suffix}`);
  },
  createSession(body: {
    workspaceId?: string;
    agentId?: string;
    model?: string;
    mode?: 'build' | 'plan';
    reasoningLevel?: string;
  }) {
    return request<{ session: Session }>('/sessions', {
      method: 'POST',
      body: JSON.stringify(body),
    });
  },
  snapshot(sessionId: string, signal?: AbortSignal) {
    return request<Snapshot>(`/sessions/${sessionId}`, { signal });
  },
  updateSession(
    sessionId: string,
    body: {
      title?: string;
      pinned?: boolean;
      archived?: boolean;
      model?: string;
      reasoningLevel?: string;
      mode?: 'build' | 'plan';
      agentId?: string;
    },
  ) {
    return request<{ session: Session }>(`/sessions/${sessionId}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    });
  },
  sendMessage(
    sessionId: string,
    body: {
      content: string;
      clientMessageId?: string;
      mode?: 'auto' | 'steer' | 'queue';
      attachmentIds?: string[];
    },
  ) {
    return request<{
      queued: boolean;
      duplicate?: boolean;
      turnId?: string;
      message?: unknown;
      queueItem?: { id: string; content: string; mode: string };
    }>(`/sessions/${sessionId}/messages`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
  },
  pause(sessionId: string) {
    return request<{ status: SessionStatus }>(`/sessions/${sessionId}/pause`, {
      method: 'POST',
    });
  },
  cancel(sessionId: string) {
    return request<{ status: SessionStatus }>(`/sessions/${sessionId}/cancel`, {
      method: 'POST',
    });
  },
  resume(sessionId: string) {
    return request<{ status: string; turnId: string }>(
      `/sessions/${sessionId}/resume`,
      {
        method: 'POST',
      },
    );
  },
  answer(
    sessionId: string,
    interactionId: string,
    answer: Record<string, unknown>,
  ) {
    return request<Record<string, unknown>>(
      `/sessions/${sessionId}/interactions/${interactionId}/answer`,
      { method: 'POST', body: JSON.stringify({ answer }) },
    );
  },
  updateQueue(
    sessionId: string,
    queueId: string,
    body: { content?: string; mode?: 'steer' | 'queue' },
  ) {
    return request<{ queue: unknown[] }>(
      `/sessions/${sessionId}/queue/${queueId}`,
      {
        method: 'PATCH',
        body: JSON.stringify(body),
      },
    );
  },
  deleteQueue(sessionId: string, queueId: string) {
    return request<{ queue: unknown[] }>(
      `/sessions/${sessionId}/queue/${queueId}`,
      {
        method: 'DELETE',
      },
    );
  },
  compact(sessionId: string) {
    return request<Record<string, unknown>>(`/sessions/${sessionId}/compact`, {
      method: 'POST',
    });
  },
  goals: {
    update(
      sessionId: string,
      body: {
        objective: string;
        criteria: string;
        tasks: Array<{
          title: string;
          status: 'pending' | 'in_progress' | 'completed' | 'blocked';
        }>;
      },
    ) {
      return request<{ goal: Goal }>(`/sessions/${sessionId}/goal`, {
        method: 'PUT',
        body: JSON.stringify(body),
      });
    },
    updateTask(
      sessionId: string,
      taskId: string,
      body: {
        title?: string;
        status?: 'pending' | 'in_progress' | 'completed' | 'blocked';
      },
    ) {
      return request<{ goal: Goal }>(
        `/sessions/${sessionId}/goal/tasks/${taskId}`,
        {
          method: 'PATCH',
          body: JSON.stringify(body),
        },
      );
    },
  },
  exports: {
    async download(
      sessionId: string,
      format: 'markdown' | 'json',
      options: { includeReasoning?: boolean; includeTools?: boolean } = {},
    ) {
      const query = new URLSearchParams({ format });
      if (options.includeReasoning !== undefined)
        query.set('include_reasoning', String(options.includeReasoning));
      if (options.includeTools !== undefined)
        query.set('include_tools', String(options.includeTools));
      const response = await fetch(
        `${BASE}/sessions/${sessionId}/export?${query.toString()}`,
      );
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new ApiError(
          body.message || '导出失败',
          body.code || 'EXPORT_FAILED',
          response.status,
        );
      }
      const blob = await response.blob();
      const disposition = response.headers.get('Content-Disposition') ?? '';
      const match = /filename\*=UTF-8''([^;]+)/.exec(disposition);
      const filename = match
        ? decodeURIComponent(match[1])
        : `session.${format === 'json' ? 'json' : 'md'}`;
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = filename;
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 2_000);
    },
  },
  shares: {
    list(sessionId: string) {
      return request<ShareInfo[]>(`/sessions/${sessionId}/shares`);
    },
    create(sessionId: string, expiresInHours = 72) {
      return request<ShareInfo>(`/sessions/${sessionId}/shares`, {
        method: 'POST',
        body: JSON.stringify({ expiresInHours, hideToolIo: true }),
      });
    },
    revoke(shareId: string) {
      return request<{ id: string; revoked: boolean }>(`/shares/${shareId}`, {
        method: 'DELETE',
      });
    },
    get(token: string, signal?: AbortSignal) {
      return request<
        Snapshot & { share: { expiresAt: string | null; createdAt: string } }
      >(`/shares/${token}`, { signal });
    },
  },
  fork(sessionId: string, messageId?: string) {
    return request<{ session: Session }>(`/sessions/${sessionId}/fork`, {
      method: 'POST',
      body: JSON.stringify({ messageId }),
    });
  },
  editMessage(sessionId: string, messageId: string, content: string) {
    return request<{ session: Session; branch: boolean }>(
      `/sessions/${sessionId}/messages/${messageId}/edit`,
      {
        method: 'POST',
        body: JSON.stringify({ content, clientMessageId: crypto.randomUUID() }),
      },
    );
  },
  files(workspaceId: string, path = '.') {
    const query = new URLSearchParams({ path });
    return request<FileListing>(
      `/workspaces/${workspaceId}/files?${query.toString()}`,
    );
  },
  workspaces: {
    list() {
      return request<
        Array<{ id: string; name: string; rootPath: string; createdAt: string }>
      >('/workspaces');
    },
    create(name: string) {
      return request<{
        id: string;
        name: string;
        rootPath: string;
        createdAt: string;
      }>('/workspaces', { method: 'POST', body: JSON.stringify({ name }) });
    },
  },
  permissions() {
    return request<{
      config: Record<
        string,
        { default: string; allow: string[]; deny: string[] }
      >;
      grants: PermissionGrant[];
    }>('/permissions');
  },
  revokeGrant(grantId: string) {
    return request<{ id: string; revoked: boolean }>(
      `/permissions/grants/${grantId}`,
      {
        method: 'DELETE',
      },
    );
  },
  debugRequests(sessionId: string) {
    return request<DebugRequest[]>(`/sessions/${sessionId}/debug/requests`);
  },
  systemStatus() {
    return request<Record<string, unknown>>('/system/status');
  },
  deleteSession(sessionId: string) {
    return request<{ id: string; deleted: boolean }>(
      `/sessions/${sessionId}?confirm=true`,
      {
        method: 'DELETE',
      },
    );
  },
  importSession(data: Record<string, unknown>) {
    return request<{ session: Session }>('/sessions/import', {
      method: 'POST',
      body: JSON.stringify({ data }),
    });
  },
  async upload(file: File, sessionId?: string): Promise<Attachment> {
    const form = new FormData();
    form.append('file', file);
    if (sessionId) form.append('session_id', sessionId);
    const response = await fetch(`${BASE}/uploads`, {
      method: 'POST',
      body: form,
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new ApiError(
        body.message || '上传失败',
        body.code || 'UPLOAD_FAILED',
        response.status,
      );
    }
    const result = (body as { data: UploadInfo }).data;
    return result.upload;
  },
  checkpoints: {
    list(sessionId: string, turnId?: string) {
      const query = turnId ? `?turn_id=${encodeURIComponent(turnId)}` : '';
      return request<CheckpointInfo[]>(
        `/sessions/${sessionId}/checkpoints${query}`,
      );
    },
    undo(sessionId: string, turnId: string) {
      return request<{ restored: string[] }>(
        `/sessions/${sessionId}/turns/${turnId}/undo`,
        {
          method: 'POST',
        },
      );
    },
    redo(sessionId: string, turnId: string) {
      return request<{ restored: string[] }>(
        `/sessions/${sessionId}/turns/${turnId}/redo`,
        {
          method: 'POST',
        },
      );
    },
    diff(checkpointId: string) {
      return request<{
        id: string;
        path: string;
        diff: string;
        operation: string;
      }>(`/checkpoints/${checkpointId}/diff`);
    },
  },
  providers: {
    list() {
      return request<PublicProvider[]>('/providers');
    },
    create(body: Record<string, unknown>) {
      return request<{ provider: PublicProvider }>('/providers', {
        method: 'POST',
        body: JSON.stringify(body),
      });
    },
    update(providerId: string, body: Record<string, unknown>) {
      return request<PublicProvider[]>(`/providers/${providerId}`, {
        method: 'PATCH',
        body: JSON.stringify(body),
      });
    },
    remove(providerId: string) {
      return request<{ id: string; deleted: boolean }>(
        `/providers/${providerId}`,
        {
          method: 'DELETE',
        },
      );
    },
    test(providerId: string) {
      return request<{ ok: boolean; status: string; tools?: unknown[] }>(
        `/providers/${providerId}/test`,
        { method: 'POST' },
      );
    },
    fetchModels(providerId: string, importModels = true) {
      return request<{
        models: Array<{ id: string; name: string }>;
        imported: string[];
      }>(`/providers/${providerId}/fetch-models`, {
        method: 'POST',
        body: JSON.stringify({ importModels }),
      });
    },
    balance(providerId: string) {
      return request<BalanceInfo>(`/providers/${providerId}/balance`);
    },
    upsertModel(providerId: string, body: Record<string, unknown>) {
      return request<{ model: unknown }>(`/providers/${providerId}/models`, {
        method: 'POST',
        body: JSON.stringify(body),
      });
    },
    deleteModel(providerId: string, modelId: string) {
      return request<{ id: string; deleted: boolean }>(
        `/providers/${providerId}/models/${modelId}`,
        {
          method: 'DELETE',
        },
      );
    },
  },
  agentsApi: {
    list() {
      return request<AgentInfo[]>('/agents');
    },
    create(body: Record<string, unknown>) {
      return request<{ agent: AgentInfo }>('/agents', {
        method: 'POST',
        body: JSON.stringify(body),
      });
    },
    update(agentId: string, body: Record<string, unknown>) {
      return request<{ agent: AgentInfo }>(`/agents/${agentId}`, {
        method: 'PATCH',
        body: JSON.stringify(body),
      });
    },
    remove(agentId: string) {
      return request<{ id: string; deleted: boolean }>(`/agents/${agentId}`, {
        method: 'DELETE',
      });
    },
    duplicate(agentId: string) {
      return request<{ agent: AgentInfo }>(`/agents/${agentId}/duplicate`, {
        method: 'POST',
      });
    },
    export(agentId: string) {
      return request<Record<string, unknown>>(`/agents/${agentId}/export`);
    },
    import(body: Record<string, unknown>) {
      return request<{ agent: AgentInfo }>('/agents/import', {
        method: 'POST',
        body: JSON.stringify(body),
      });
    },
  },
  skills: {
    list(sessionId?: string) {
      return request<SkillInfo[]>(
        `/skills${sessionId ? `?session_id=${sessionId}` : ''}`,
      );
    },
    get(name: string, sessionId?: string) {
      return request<SkillInfo & { content: string }>(
        `/skills/${encodeURIComponent(name)}${sessionId ? `?session_id=${sessionId}` : ''}`,
      );
    },
    save(body: {
      scope: 'system' | 'workspace';
      name: string;
      description: string;
      content: string;
      workspaceId?: string;
    }) {
      return request<{ name: string }>('/skills', {
        method: 'POST',
        body: JSON.stringify(body),
      });
    },
    remove(scope: string, name: string, workspaceId?: string) {
      const suffix = workspaceId ? `?workspace_id=${workspaceId}` : '';
      return request<{ deleted: boolean }>(
        `/skills/${scope}/${encodeURIComponent(name)}${suffix}`,
        {
          method: 'DELETE',
        },
      );
    },
    toggle(sessionId: string, name: string, enabled: boolean) {
      return request<{ enabled: boolean }>(
        `/sessions/${sessionId}/skills/${encodeURIComponent(name)}/toggle`,
        { method: 'POST', body: JSON.stringify({ enabled }) },
      );
    },
  },
  mcp: {
    list(sessionId?: string) {
      return request<McpServer[]>(
        `/mcp/servers${sessionId ? `?session_id=${sessionId}` : ''}`,
      );
    },
    create(body: Record<string, unknown>) {
      return request<{ server: McpServer }>('/mcp/servers', {
        method: 'POST',
        body: JSON.stringify(body),
      });
    },
    update(serverId: string, body: Record<string, unknown>) {
      return request<{ server: McpServer }>(`/mcp/servers/${serverId}`, {
        method: 'PATCH',
        body: JSON.stringify(body),
      });
    },
    remove(serverId: string) {
      return request<{ id: string; deleted: boolean }>(
        `/mcp/servers/${serverId}`,
        {
          method: 'DELETE',
        },
      );
    },
    test(serverId: string) {
      return request<{ ok: boolean; tools: Array<{ name: string }> }>(
        `/mcp/servers/${serverId}/test`,
        { method: 'POST' },
      );
    },
    refresh(serverId: string) {
      return request<{ tools: Array<{ name: string }> }>(
        `/mcp/servers/${serverId}/refresh`,
        {
          method: 'POST',
        },
      );
    },
    toggle(sessionId: string, serverId: string, enabled: boolean) {
      return request<{ enabled: boolean }>(
        `/sessions/${sessionId}/mcp/${serverId}/toggle`,
        {
          method: 'POST',
          body: JSON.stringify({ enabled }),
        },
      );
    },
  },
  commands: {
    list() {
      return request<CommandInfo[]>('/commands');
    },
    create(body: Record<string, unknown>) {
      return request<{ command: CommandInfo }>('/commands', {
        method: 'POST',
        body: JSON.stringify(body),
      });
    },
    update(commandId: string, body: Record<string, unknown>) {
      return request<{ command: CommandInfo }>(`/commands/${commandId}`, {
        method: 'PATCH',
        body: JSON.stringify(body),
      });
    },
    remove(commandId: string) {
      return request<{ deleted: boolean }>(`/commands/${commandId}`, {
        method: 'DELETE',
      });
    },
    render(commandId: string, args: string) {
      return request<{ prompt: string }>(`/commands/${commandId}/render`, {
        method: 'POST',
        body: JSON.stringify({ arguments: args }),
      });
    },
    resolve(text: string) {
      return request<{ command: CommandInfo; prompt: string }>(
        '/commands/resolve',
        {
          method: 'POST',
          body: JSON.stringify({ text }),
        },
      );
    },
  },
  hooks: {
    list() {
      return request<HookInfo[]>('/hooks');
    },
    create(body: Record<string, unknown>) {
      return request<{ hook: HookInfo }>('/hooks', {
        method: 'POST',
        body: JSON.stringify(body),
      });
    },
    update(hookId: string, body: Record<string, unknown>) {
      return request<{ hook: HookInfo }>(`/hooks/${hookId}`, {
        method: 'PATCH',
        body: JSON.stringify(body),
      });
    },
    remove(hookId: string) {
      return request<{ deleted: boolean }>(`/hooks/${hookId}`, {
        method: 'DELETE',
      });
    },
  },
  plugins: {
    list() {
      return request<PluginInfo[]>('/plugins');
    },
    async install(file: File) {
      const form = new FormData();
      form.append('file', file);
      const response = await fetch(`${BASE}/plugins/install`, {
        method: 'POST',
        body: form,
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new ApiError(
          body.message || '安装失败',
          body.code || 'PLUGIN_FAILED',
          response.status,
        );
      }
      return (body as { data: { plugin: PluginInfo } }).data.plugin;
    },
    enable(pluginId: string, enabled: boolean) {
      return request<{ plugin: PluginInfo }>(`/plugins/${pluginId}/enable`, {
        method: 'POST',
        body: JSON.stringify({ enabled }),
      });
    },
    trust(pluginId: string, trusted: boolean) {
      return request<{ plugin: PluginInfo }>(`/plugins/${pluginId}/trust`, {
        method: 'POST',
        body: JSON.stringify({ trusted }),
      });
    },
    remove(pluginId: string) {
      return request<{ deleted: boolean }>(`/plugins/${pluginId}`, {
        method: 'DELETE',
      });
    },
  },
  webhooks: {
    list() {
      return request<WebhookInfo[]>('/webhooks');
    },
    create(body: Record<string, unknown>) {
      return request<{ webhook: WebhookInfo }>('/webhooks', {
        method: 'POST',
        body: JSON.stringify(body),
      });
    },
    update(webhookId: string, body: Record<string, unknown>) {
      return request<{ webhook: WebhookInfo }>(`/webhooks/${webhookId}`, {
        method: 'PATCH',
        body: JSON.stringify(body),
      });
    },
    remove(webhookId: string) {
      return request<{ deleted: boolean }>(`/webhooks/${webhookId}`, {
        method: 'DELETE',
      });
    },
    test(webhookId: string) {
      return request<{ ok: boolean; status: string }>(
        `/webhooks/${webhookId}/test`,
        {
          method: 'POST',
        },
      );
    },
  },
  backups: {
    list() {
      return request<BackupInfo[]>('/backups');
    },
    create(includeArtifacts?: boolean) {
      return request<BackupInfo>('/backups', {
        method: 'POST',
        body: JSON.stringify({ includeArtifacts }),
      });
    },
    remove(backupId: string) {
      return request<{ deleted: boolean }>(`/backups/${backupId}`, {
        method: 'DELETE',
      });
    },
    restore(backupId: string) {
      return request<{ requiresRestart: boolean }>('/backups/restore', {
        method: 'POST',
        body: JSON.stringify({ backupId }),
      });
    },
    downloadUrl(backupId: string) {
      return `${BASE}/backups/${backupId}/download`;
    },
    download(backupId: string, filename = `${backupId}.zip`) {
      return download(`/backups/${backupId}/download`, filename);
    },
  },
  cleanupRetention() {
    return request<Record<string, number>>('/system/retention/cleanup', {
      method: 'POST',
    });
  },
  logsExportUrl() {
    return `${BASE}/system/logs/export`;
  },
  downloadLogs() {
    return download('/system/logs/export', 'mini-agent-logs.zip');
  },
  policy() {
    return request<{ path: string | null; source: string; enforced: boolean }>(
      '/system/policy',
    );
  },
  workspacesApi: {
    update(
      workspaceId: string,
      body: { name?: string; alias?: string; description?: string },
    ) {
      return request<Workspace>(`/workspaces/${workspaceId}`, {
        method: 'PATCH',
        body: JSON.stringify(body),
      });
    },
    remove(workspaceId: string) {
      return request<{ deleted: boolean }>(
        `/workspaces/${workspaceId}?confirm=true`,
        {
          method: 'DELETE',
        },
      );
    },
    search(
      workspaceId: string,
      q: string,
      mode: 'name' | 'content' = 'content',
    ) {
      return request<{
        results: Array<{
          path: string;
          line: number | null;
          text: string | null;
        }>;
      }>(
        `/workspaces/${workspaceId}/search?q=${encodeURIComponent(q)}&mode=${mode}`,
      );
    },
  },
};

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

export function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error);
}
