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
} from './types';

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? '/api/v1';

export class ApiError extends Error {
  code: string;
  status: number;
  data: unknown;

  constructor(message: string, code: string, status: number, data: unknown = null) {
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
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, { ...options, headers, credentials: 'same-origin' });
  } catch (error) {
    if ((error as Error).name === 'AbortError') throw error;
    throw new ApiError('无法连接后端服务，请确认服务已启动', 'NETWORK_ERROR', 0);
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

export const api = {
  bootstrap(signal?: AbortSignal) {
    return request<BootstrapData>('/bootstrap', { signal });
  },
  config(signal?: AbortSignal) {
    return request<PublicConfig>('/config', { signal });
  },
  patchConfig(body: { general?: Record<string, unknown>; defaults?: Record<string, unknown> }) {
    return request<PublicConfig>('/config', { method: 'PATCH', body: JSON.stringify(body) });
  },
  sessions(params: { workspaceId?: string; search?: string; archived?: boolean; limit?: number } = {}) {
    const query = new URLSearchParams();
    if (params.workspaceId) query.set('workspace_id', params.workspaceId);
    if (params.search) query.set('search', params.search);
    if (params.archived !== undefined) query.set('archived', String(params.archived));
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
    return request<{ session: Session }>('/sessions', { method: 'POST', body: JSON.stringify(body) });
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
    body: { content: string; clientMessageId?: string; mode?: 'auto' | 'steer' | 'queue' },
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
    return request<{ status: SessionStatus }>(`/sessions/${sessionId}/pause`, { method: 'POST' });
  },
  cancel(sessionId: string) {
    return request<{ status: SessionStatus }>(`/sessions/${sessionId}/cancel`, { method: 'POST' });
  },
  resume(sessionId: string) {
    return request<{ status: string; turnId: string }>(`/sessions/${sessionId}/resume`, {
      method: 'POST',
    });
  },
  answer(sessionId: string, interactionId: string, answer: Record<string, unknown>) {
    return request<Record<string, unknown>>(
      `/sessions/${sessionId}/interactions/${interactionId}/answer`,
      { method: 'POST', body: JSON.stringify({ answer }) },
    );
  },
  updateQueue(sessionId: string, queueId: string, body: { content?: string; mode?: 'steer' | 'queue' }) {
    return request<{ queue: unknown[] }>(`/sessions/${sessionId}/queue/${queueId}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    });
  },
  deleteQueue(sessionId: string, queueId: string) {
    return request<{ queue: unknown[] }>(`/sessions/${sessionId}/queue/${queueId}`, {
      method: 'DELETE',
    });
  },
  compact(sessionId: string) {
    return request<Record<string, unknown>>(`/sessions/${sessionId}/compact`, { method: 'POST' });
  },
  goals: {
    update(
      sessionId: string,
      body: {
        objective: string;
        criteria: string;
        tasks: Array<{ title: string; status: 'pending' | 'in_progress' | 'completed' | 'blocked' }>;
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
      body: { title?: string; status?: 'pending' | 'in_progress' | 'completed' | 'blocked' },
    ) {
      return request<{ goal: Goal }>(`/sessions/${sessionId}/goal/tasks/${taskId}`, {
        method: 'PATCH',
        body: JSON.stringify(body),
      });
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
      if (options.includeTools !== undefined) query.set('include_tools', String(options.includeTools));
      const response = await fetch(`${BASE}/sessions/${sessionId}/export?${query.toString()}`);
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new ApiError(body.message || '导出失败', body.code || 'EXPORT_FAILED', response.status);
      }
      const blob = await response.blob();
      const disposition = response.headers.get('Content-Disposition') ?? '';
      const match = /filename\*=UTF-8''([^;]+)/.exec(disposition);
      const filename = match ? decodeURIComponent(match[1]) : `session.${format === 'json' ? 'json' : 'md'}`;
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
      return request<{ id: string; revoked: boolean }>(`/shares/${shareId}`, { method: 'DELETE' });
    },
    get(token: string, signal?: AbortSignal) {
      return request<Snapshot & { share: { expiresAt: string | null; createdAt: string } }>(
        `/shares/${token}`,
        { signal },
      );
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
      { method: 'POST', body: JSON.stringify({ content, clientMessageId: crypto.randomUUID() }) },
    );
  },
  files(workspaceId: string, path = '.') {
    const query = new URLSearchParams({ path });
    return request<FileListing>(`/workspaces/${workspaceId}/files?${query.toString()}`);
  },
  workspaces: {
    list() {
      return request<Array<{ id: string; name: string; rootPath: string; createdAt: string }>>(
        '/workspaces',
      );
    },
    create(name: string) {
      return request<{ id: string; name: string; rootPath: string; createdAt: string }>(
        '/workspaces',
        { method: 'POST', body: JSON.stringify({ name }) },
      );
    },
  },
  permissions() {
    return request<{ config: Record<string, { default: string; allow: string[]; deny: string[] }>; grants: PermissionGrant[] }>(
      '/permissions',
    );
  },
  revokeGrant(grantId: string) {
    return request<{ id: string; revoked: boolean }>(`/permissions/grants/${grantId}`, {
      method: 'DELETE',
    });
  },
  debugRequests(sessionId: string) {
    return request<DebugRequest[]>(`/sessions/${sessionId}/debug/requests`);
  },
  systemStatus() {
    return request<Record<string, unknown>>('/system/status');
  },
};

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

export function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error);
}
