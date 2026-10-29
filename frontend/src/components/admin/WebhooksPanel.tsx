import { Plus, Send, Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { WebhookInfo } from '../../api/types';
import type { ToastFn } from './useAdmin';
import { runAction } from './useAdmin';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

const EMPTY = { name: '', url: '', events: 'turn.completed', secret: '' };

export function WebhooksPanel({ onToast }: { onToast: ToastFn }) {
  const [hooks, setHooks] = useState<WebhookInfo[]>([]);
  const [form, setForm] = useState(EMPTY);

  const load = async () => setHooks(await api.webhooks.list());
  useEffect(() => {
    void load().catch(() => undefined);
  }, []);

  const create = async () => {
    if (!form.name.trim() || !form.url.trim()) return;
    await runAction(
      () =>
        api.webhooks.create({
          name: form.name.trim(),
          url: form.url.trim(),
          events: form.events
            .split(',')
            .map((item) => item.trim())
            .filter(Boolean),
          secret: form.secret,
        }),
      onToast,
      'Webhook 已创建',
    );
    setForm(EMPTY);
    await load();
  };

  return (
    <div className="admin-panel">
      <section className="admin-section">
        <h3>Webhook</h3>
        <p className="muted small">
          出站事件使用 HMAC-SHA256
          签名（X-Mini-Agent-Signature），失败按指数退避重试，并保存每次投递结果。
        </p>
        <div className="admin-list">
          {hooks.map((hook) => (
            <div className="admin-row" key={hook.id}>
              <div>
                <strong>{hook.name}</strong>
                <span
                  className={`admin-tag ${hook.lastStatus === 'succeeded' ? 'success' : hook.lastStatus === 'failed' ? 'warn' : ''}`}
                >
                  {hook.lastStatus || '未投递'}
                </span>
                {hook.secretConfigured && (
                  <span className="admin-tag">已签名</span>
                )}
                <p className="muted small">{hook.url}</p>
                <p className="small">事件：{hook.events.join(', ') || '*'}</p>
                {hook.lastError && (
                  <p className="small error-text">{hook.lastError}</p>
                )}
              </div>
              <div className="admin-actions">
                <Button
                  type="button"
                  variant="outline" size="sm"
                  onClick={() =>
                    void runAction(
                      () => api.webhooks.test(hook.id),
                      onToast,
                      '测试已发送',
                    ).then(async (value) => {
                      if (value !== null) await load();
                    })
                  }
                >
                  <Send size={13} /> 测试
                </Button>
                <Button
                  type="button"
                  variant="outline" size="sm"
                  onClick={() =>
                    void runAction(
                      () =>
                        api.webhooks.update(hook.id, {
                          enabled: !hook.enabled,
                        }),
                      onToast,
                    ).then(async (value) => {
                      if (value !== null) await load();
                    })
                  }
                >
                  {hook.enabled ? '停用' : '启用'}
                </Button>
                <Button
                  type="button"
                  variant="ghost" size="icon-sm" className="text-muted-foreground hover:bg-danger-soft hover:text-destructive"
                  aria-label="删除 Webhook"
                  onClick={() =>
                    void runAction(
                      () => api.webhooks.remove(hook.id),
                      onToast,
                      '已删除',
                    ).then(async (value) => {
                      if (value !== null) await load();
                    })
                  }
                >
                  <Trash2 size={14} />
                </Button>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="admin-section">
        <h3>新增 Webhook</h3>
        <div className="form-grid">
          <label className="field">
            <span>名称</span>
            <Input
              value={form.name}
              onChange={(event) =>
                setForm({ ...form, name: event.target.value })
              }
            />
          </label>
          <label className="field wide">
            <span>URL</span>
            <Input
              value={form.url}
              onChange={(event) =>
                setForm({ ...form, url: event.target.value })
              }
              placeholder="https://example.com/hook"
            />
          </label>
          <label className="field">
            <span>事件（逗号分隔，* 为全部）</span>
            <Input
              value={form.events}
              onChange={(event) =>
                setForm({ ...form, events: event.target.value })
              }
            />
          </label>
          <label className="field">
            <span>签名 Secret</span>
            <Input
              type="password"
              value={form.secret}
              onChange={(event) =>
                setForm({ ...form, secret: event.target.value })
              }
            />
          </label>
        </div>
        <Button
          type="button"
          size="sm"
          onClick={() => void create()}
        >
          <Plus size={14} /> 创建
        </Button>
      </section>
    </div>
  );
}
