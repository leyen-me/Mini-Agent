import { render, type RenderOptions } from '@testing-library/react';
import type { ReactElement, ReactNode } from 'react';
import type { PublicConfig, Session, Snapshot } from '../api/types';
import { createTranslator, type Language } from '../lib/i18n';
import { UiContext, type UiContextValue } from '../state/UiContext';

export function uiValue(language: Language = 'zh-CN'): UiContextValue {
  const t = createTranslator(language);
  return {
    language,
    t,
    themeSetting: 'light',
    resolvedTheme: 'light',
    setLanguage: () => undefined,
    setTheme: () => undefined,
  };
}

export function renderWithUi(ui: ReactElement, language: Language = 'zh-CN') {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <UiContext.Provider value={uiValue(language)}>{children}</UiContext.Provider>
  );
  return render(ui, { wrapper } as RenderOptions);
}

export function makeSession(overrides: Partial<Session> = {}): Session {
  return {
    id: 'sess_test',
    workspaceId: 'ws_test',
    agentId: 'build',
    title: 'Test session',
    model: 'mock/echo',
    reasoningLevel: 'off',
    mode: 'build',
    status: 'idle',
    parentSessionId: null,
    forkFromMessageId: null,
    pinned: false,
    archived: false,
    lastError: null,
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

export function makeConfig(overrides: Partial<PublicConfig> = {}): PublicConfig {
  return {
    general: { language: 'zh-CN', theme: 'light', notifications: false },
    defaults: { model: 'mock/echo', reasoning_level: 'off', agent: 'build', mode: 'build' },
    providers: [
      {
        id: 'mock',
        name: 'Mock',
        protocol: 'mock',
        base_url: '',
        enabled: true,
        configured: true,
        key_hint: '',
        models: [
          {
            id: 'echo',
            name: 'Echo',
            context_window: 32000,
            max_output: 4000,
            capabilities: ['text', 'tools'],
            reasoning_levels: ['off', 'high'],
          },
        ],
      },
    ],
    models: [
      {
        ref: 'mock/echo',
        provider: 'mock',
        provider_name: 'Mock',
        protocol: 'mock',
        name: 'Echo',
        context_window: 32000,
        max_output: 4000,
        capabilities: ['text', 'tools'],
        reasoning_levels: ['off', 'high'],
        pricing: { input_per_million: 0, output_per_million: 0, cache_hit_per_million: 0 },
      },
    ],
    model_configured: true,
    budget: {
      max_steps_per_turn: 50,
      max_duration_seconds: 900,
      max_cost_per_turn: 1,
      max_repeat_failures: 3,
      max_tool_output_chars: 24000,
    },
    compaction: { enabled: true, keep_recent_tokens: 4000, trigger_ratio: 0.85 },
    permissions: {
      read: { default: 'allow', allow: [], deny: [] },
      write: { default: 'ask', allow: [], deny: [] },
      shell: { default: 'ask', allow: [], deny: [] },
    },
    source: 'test',
    config_path: null,
    workspace_root: '/tmp/ws',
    data_dir: '/tmp/data',
    ...overrides,
  };
}

export function makeSnapshot(overrides: Partial<Snapshot> = {}): Snapshot {
  return {
    session: makeSession(),
    workspace: { id: 'ws_test', name: 'Test', rootPath: '/tmp/ws', createdAt: '2026-01-01T00:00:00Z' },
    agent: { id: 'build', name: 'Build', description: '', readOnly: false },
    messages: [],
    turns: [],
    toolCalls: [],
    queue: [],
    interactions: [],
    tasks: [],
    usage: {
      inputTokens: 0,
      outputTokens: 0,
      cacheHitTokens: 0,
      cacheMissTokens: 0,
      cost: 0,
      contextUsed: 100,
      contextWindow: 32000,
      contextPercent: 0.3,
      model: 'mock/echo',
    },
    goal: null,
    compaction: null,
    lastEventId: 0,
    ...overrides,
  };
}
