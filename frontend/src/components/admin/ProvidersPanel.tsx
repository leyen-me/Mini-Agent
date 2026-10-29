import { Plus, RefreshCw, Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { PublicProvider } from '../../api/types';
import type { ToastFn } from './useAdmin';
import { runAction } from './useAdmin';

interface Props {
  onToast: ToastFn;
  onReload: () => Promise<void>;
}

function inferReasoningLevels(modelId: string): string {
  return /flash|v4|reason|r1|thinking/i.test(modelId)
    ? 'off, low, medium, high, max'
    : 'off';
}

const EMPTY = {
  id: '',
  name: '',
  protocol: 'openai-chat',
  baseUrl: '',
  apiKey: '',
  models: 'model-id',
};

export function ProvidersPanel({ onToast, onReload }: Props) {
  const [providers, setProviders] = useState<PublicProvider[]>([]);
  const [form, setForm] = useState(EMPTY);
  const [model, setModel] = useState({
    provider: '',
    id: '',
    name: '',
    context: '128000',
    maxOutput: '8192',
    capabilities: 'text, tools',
    reasoningLevels: 'off',
    structuredOutput: false,
  });
  const [busy, setBusy] = useState(false);

  const load = async () => {
    const data = await api.providers.list();
    setProviders(data);
  };

  useEffect(() => {
    void load().catch(() => undefined);
  }, []);

  const create = async () => {
    setBusy(true);
    try {
      await api.providers.create({
        id: form.id.trim(),
        name: form.name.trim() || form.id.trim(),
        protocol: form.protocol,
        baseUrl: form.baseUrl.trim(),
        apiKey: form.apiKey.trim() || undefined,
        models: form.models
          .split(',')
          .map((item) => item.trim())
          .filter(Boolean)
          .map((id) => ({
            id,
            name: id,
            contextWindow: 128_000,
            maxOutput: 8_192,
          })),
      });
      setForm(EMPTY);
      await load();
      await onReload();
      onToast('服务商已创建', 'success');
    } catch (error) {
      onToast((error as Error).message, 'error');
    } finally {
      setBusy(false);
    }
  };

  const addModel = async () => {
    if (!model.provider || !model.id.trim()) return;
    await runAction(
      () =>
        api.providers.upsertModel(model.provider, {
          id: model.id.trim(),
          name: model.name.trim() || model.id.trim(),
          contextWindow: Number(model.context) || 128_000,
          maxOutput: Number(model.maxOutput) || 8_192,
          capabilities: model.capabilities
            .split(',')
            .map((item) => item.trim())
            .filter(Boolean),
          reasoningLevels: model.reasoningLevels
            .split(',')
            .map((item) => item.trim())
            .filter(Boolean),
          structuredOutput: model.structuredOutput,
        }),
      onToast,
      '模型已保存',
    );
    setModel((current) => ({
      ...current,
      id: '',
      name: '',
      reasoningLevels: 'off',
      structuredOutput: false,
    }));
    await load();
    await onReload();
  };

  return (
    <div className="admin-panel">
      <section className="admin-section">
        <h3>服务商</h3>
        <p className="muted small">
          密钥只写入独立 secrets 文件，接口仅返回掩码。支持 OpenAI
          Chat、Responses、Anthropic 和 Mock。
        </p>
        <div className="admin-table">
          {providers.map((provider) => (
            <article className="admin-card" key={provider.id}>
              <header>
                <div>
                  <strong>{provider.name}</strong>
                  <span className="admin-tag">{provider.id}</span>
                  <span className="admin-tag">{provider.protocol}</span>
                </div>
                <div className="admin-actions">
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() =>
                      void runAction(
                        () => api.providers.test(provider.id),
                        onToast,
                        '连接成功',
                      )
                    }
                  >
                    <RefreshCw size={13} /> 测试
                  </button>
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() =>
                      void runAction(
                        () => api.providers.fetchModels(provider.id, true),
                        onToast,
                        '模型已拉取',
                      ).then(async (value) => {
                        if (value !== null) {
                          await load();
                          await onReload();
                        }
                      })
                    }
                  >
                    拉取模型
                  </button>
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() =>
                      void runAction(
                        () =>
                          api.providers.update(provider.id, {
                            enabled: !provider.enabled,
                          }),
                        onToast,
                        provider.enabled ? '已停用' : '已启用',
                      ).then(async (value) => {
                        if (value !== null) {
                          await load();
                          await onReload();
                        }
                      })
                    }
                  >
                    {provider.enabled ? '停用' : '启用'}
                  </button>
                  {!provider.supports_balance && (
                    <button
                      type="button"
                      className="secondary-button"
                      onClick={() =>
                        void runAction(
                          () =>
                            api.providers.update(provider.id, {
                              supportsBalance: true,
                            }),
                          onToast,
                          '已启用余额查询',
                        ).then(async (value) => {
                          if (value !== null) {
                            await load();
                            await onReload();
                          }
                        })
                      }
                    >
                      启用余额
                    </button>
                  )}
                  {provider.supports_balance && (
                    <button
                      type="button"
                      className="secondary-button"
                      onClick={() =>
                        void runAction(async () => {
                          const balance = await api.providers.balance(
                            provider.id,
                          );
                          onToast(
                            `余额：${balance.total} ${balance.currency}`,
                            'info',
                          );
                        }, onToast)
                      }
                    >
                      余额
                    </button>
                  )}
                  <button
                    type="button"
                    className="icon-button danger"
                    aria-label="删除服务商"
                    onClick={() =>
                      void runAction(
                        () => api.providers.remove(provider.id),
                        onToast,
                        '已删除',
                      ).then(async (value) => {
                        if (value !== null) {
                          await load();
                          await onReload();
                        }
                      })
                    }
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </header>
              <p className="muted small">{provider.base_url}</p>
              <p className="small">
                密钥：
                {provider.key_hint ||
                  (provider.configured ? '已配置' : '未配置')}{' '}
                · 状态：
                {provider.enabled ? '启用' : '停用'}
              </p>
              <div className="admin-tags">
                {provider.models.map((item) => (
                  <span className="admin-chip" key={item.id}>
                    <button
                      type="button"
                      className="admin-chip-label"
                      title="编辑模型"
                      aria-label={`编辑模型 ${item.id}`}
                      onClick={() =>
                        setModel({
                          provider: provider.id,
                          id: item.id,
                          name: item.name,
                          context: String(item.context_window),
                          maxOutput: String(item.max_output),
                          capabilities: item.capabilities.join(', '),
                          reasoningLevels: item.reasoning_levels.join(', '),
                          structuredOutput: Boolean(item.structured_output),
                        })
                      }
                    >
                      {item.id}
                    </button>
                    <button
                      type="button"
                      aria-label={`删除模型 ${item.id}`}
                      onClick={() =>
                        void runAction(
                          () => api.providers.deleteModel(provider.id, item.id),
                          onToast,
                          '模型已删除',
                        ).then(async (value) => {
                          if (value !== null) await load();
                        })
                      }
                    >
                      ×
                    </button>
                  </span>
                ))}
              </div>
              <label className="field inline">
                <span>API Key</span>
                <input
                  type="password"
                  placeholder="留空则不修改"
                  onBlur={(event) => {
                    const value = event.target.value.trim();
                    if (!value) return;
                    void runAction(
                      () =>
                        api.providers.update(provider.id, { apiKey: value }),
                      onToast,
                      '密钥已更新',
                    );
                    event.target.value = '';
                  }}
                />
              </label>
            </article>
          ))}
        </div>
      </section>

      <section className="admin-section">
        <h3>新增服务商</h3>
        <div className="form-grid">
          <label className="field">
            <span>ID</span>
            <input
              value={form.id}
              onChange={(event) => setForm({ ...form, id: event.target.value })}
              placeholder="deepseek"
            />
          </label>
          <label className="field">
            <span>名称</span>
            <input
              value={form.name}
              onChange={(event) =>
                setForm({ ...form, name: event.target.value })
              }
            />
          </label>
          <label className="field">
            <span>协议</span>
            <select
              value={form.protocol}
              onChange={(event) =>
                setForm({ ...form, protocol: event.target.value })
              }
            >
              <option value="openai-chat">OpenAI Chat</option>
              <option value="openai-responses">OpenAI Responses</option>
              <option value="anthropic">Anthropic Messages</option>
              <option value="mock">Mock</option>
            </select>
          </label>
          <label className="field wide">
            <span>Base URL</span>
            <input
              value={form.baseUrl}
              onChange={(event) =>
                setForm({ ...form, baseUrl: event.target.value })
              }
              placeholder="https://api.example.com/v1"
            />
          </label>
          <label className="field">
            <span>API Key</span>
            <input
              type="password"
              value={form.apiKey}
              onChange={(event) =>
                setForm({ ...form, apiKey: event.target.value })
              }
            />
          </label>
          <label className="field">
            <span>模型 ID（逗号分隔）</span>
            <input
              value={form.models}
              onChange={(event) =>
                setForm({ ...form, models: event.target.value })
              }
            />
          </label>
        </div>
        <button
          type="button"
          className="primary-button"
          disabled={busy || !form.id.trim()}
          onClick={() => void create()}
        >
          <Plus size={14} /> 创建
        </button>
      </section>

      <section className="admin-section">
        <h3>添加 / 编辑模型</h3>
        <div className="form-grid">
          <label className="field">
            <span>服务商</span>
            <select
              value={model.provider}
              onChange={(event) =>
                setModel({ ...model, provider: event.target.value })
              }
            >
              <option value="">选择</option>
              {providers.map((provider) => (
                <option key={provider.id} value={provider.id}>
                  {provider.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>模型 ID</span>
            <input
              value={model.id}
              onChange={(event) => {
                const id = event.target.value;
                setModel((current) => ({
                  ...current,
                  id,
                  reasoningLevels:
                    current.reasoningLevels === 'off'
                      ? inferReasoningLevels(id)
                      : current.reasoningLevels,
                }));
              }}
            />
          </label>
          <label className="field">
            <span>显示名</span>
            <input
              value={model.name}
              onChange={(event) =>
                setModel({ ...model, name: event.target.value })
              }
            />
          </label>
          <label className="field">
            <span>上下文</span>
            <input
              value={model.context}
              onChange={(event) =>
                setModel({ ...model, context: event.target.value })
              }
            />
          </label>
          <label className="field">
            <span>最大输出</span>
            <input
              value={model.maxOutput}
              onChange={(event) =>
                setModel({ ...model, maxOutput: event.target.value })
              }
            />
          </label>
          <label className="field">
            <span>能力（逗号分隔）</span>
            <input
              value={model.capabilities}
              onChange={(event) =>
                setModel({ ...model, capabilities: event.target.value })
              }
              placeholder="text, tools, reasoning"
            />
          </label>
          <label className="field">
            <span>推理级别（逗号分隔）</span>
            <input
              value={model.reasoningLevels}
              onChange={(event) =>
                setModel({ ...model, reasoningLevels: event.target.value })
              }
              placeholder="off, low, medium, high, max"
            />
          </label>
          <label className="checkbox-field">
            <input
              type="checkbox"
              checked={model.structuredOutput}
              onChange={(event) =>
                setModel({ ...model, structuredOutput: event.target.checked })
              }
            />
            <span>支持结构化输出（JSON Schema）</span>
          </label>
        </div>
        <button
          type="button"
          className="secondary-button"
          onClick={() => void addModel()}
        >
          <Plus size={14} /> 保存模型
        </button>
      </section>
    </div>
  );
}
