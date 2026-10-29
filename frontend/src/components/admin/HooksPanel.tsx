import { Plus, Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { HookInfo } from '../../api/types';
import type { ToastFn } from './useAdmin';
import { runAction } from './useAdmin';

const EVENTS = [
  'session.start',
  'turn.end',
  'tool.before',
  'tool.after',
  'compaction.after',
];
const EMPTY = {
  name: '',
  event: 'turn.end',
  command: '',
  timeoutSeconds: '30',
};

export function HooksPanel({ onToast }: { onToast: ToastFn }) {
  const [hooks, setHooks] = useState<HookInfo[]>([]);
  const [form, setForm] = useState(EMPTY);

  const load = async () => setHooks(await api.hooks.list());
  useEffect(() => {
    void load().catch(() => undefined);
  }, []);

  const create = async () => {
    if (!form.name.trim() || !form.command.trim()) return;
    await runAction(
      () =>
        api.hooks.create({
          name: form.name.trim(),
          event: form.event,
          command: form.command,
          timeoutSeconds: Number(form.timeoutSeconds) || 30,
        }),
      onToast,
      'Hook 已创建',
    );
    setForm(EMPTY);
    await load();
  };

  return (
    <div className="admin-panel">
      <section className="admin-section">
        <h3>生命周期 Hook</h3>
        <p className="muted small">
          Hook 在独立子进程中执行，stdin 收到 JSON 事件，环境变量包含
          MINI_AGENT_EVENT / SESSION_ID / WORKSPACE；输出有超时和长度限制。
        </p>
        <div className="admin-list">
          {hooks.map((hook) => (
            <div className="admin-row" key={hook.id}>
              <div>
                <strong>{hook.name}</strong>
                <span className="admin-tag">{hook.event}</span>
                <span className="admin-tag">{hook.timeoutSeconds}s</span>
                <pre className="admin-detail">{hook.command}</pre>
              </div>
              <div className="admin-actions">
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() =>
                    void runAction(
                      () =>
                        api.hooks.update(hook.id, { enabled: !hook.enabled }),
                      onToast,
                    ).then(async (value) => {
                      if (value !== null) await load();
                    })
                  }
                >
                  {hook.enabled ? '停用' : '启用'}
                </button>
                <button
                  type="button"
                  className="icon-button danger"
                  aria-label="删除 Hook"
                  onClick={() =>
                    void runAction(
                      () => api.hooks.remove(hook.id),
                      onToast,
                      '已删除',
                    ).then(async (value) => {
                      if (value !== null) await load();
                    })
                  }
                >
                  <Trash2 size={14} />
                </button>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="admin-section">
        <h3>新建 Hook</h3>
        <div className="form-grid">
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
            <span>事件</span>
            <select
              value={form.event}
              onChange={(event) =>
                setForm({ ...form, event: event.target.value })
              }
            >
              {EVENTS.map((event) => (
                <option key={event} value={event}>
                  {event}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>超时（秒）</span>
            <input
              value={form.timeoutSeconds}
              onChange={(event) =>
                setForm({ ...form, timeoutSeconds: event.target.value })
              }
            />
          </label>
          <label className="field wide">
            <span>命令</span>
            <textarea
              rows={3}
              value={form.command}
              onChange={(event) =>
                setForm({ ...form, command: event.target.value })
              }
              placeholder="python3 scripts/hook.py"
            />
          </label>
        </div>
        <button
          type="button"
          className="primary-button"
          onClick={() => void create()}
        >
          <Plus size={14} /> 创建
        </button>
      </section>
    </div>
  );
}
