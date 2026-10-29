import { Plus, Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { HookInfo } from '../../api/types';
import type { ToastFn } from './useAdmin';
import { runAction } from './useAdmin';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { SimpleSelect } from '@/components/common/SimpleSelect';

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
                <Button
                  type="button"
                  variant="outline" size="sm"
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
                </Button>
                <Button
                  type="button"
                  variant="ghost" size="icon-sm" className="text-muted-foreground hover:bg-danger-soft hover:text-destructive"
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
                </Button>
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
            <Input
              value={form.name}
              onChange={(event) =>
                setForm({ ...form, name: event.target.value })
              }
            />
          </label>
          <label className="field">
            <span>事件</span>
            <SimpleSelect
              value={form.event}
              onValueChange={(value) => setForm({ ...form, event: value })}
              options={EVENTS.map((event) => ({
                value: event,
                label: <span className="font-mono text-[12.5px]">{event}</span>,
              }))}
            />
          </label>
          <label className="field">
            <span>超时（秒）</span>
            <Input
              value={form.timeoutSeconds}
              onChange={(event) =>
                setForm({ ...form, timeoutSeconds: event.target.value })
              }
            />
          </label>
          <label className="field wide">
            <span>命令</span>
            <Textarea
              rows={3}
              value={form.command}
              onChange={(event) =>
                setForm({ ...form, command: event.target.value })
              }
              placeholder="python3 scripts/hook.py"
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
