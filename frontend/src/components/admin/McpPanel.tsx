import { Plus, RefreshCw, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { McpServer, Session } from '../../api/types';
import type { ToastFn } from './useAdmin';
import { runAction } from './useAdmin';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { SimpleSelect } from '@/components/common/SimpleSelect';

interface Props {
  session: Session | null;
  onToast: ToastFn;
}

const EMPTY = {
  name: '',
  description: '',
  transport: 'stdio' as 'stdio' | 'http' | 'sse',
  command: '',
  args: '',
  url: '',
  headers: '{}',
  env: '{}',
};

export function McpPanel({ session, onToast }: Props) {
  const [servers, setServers] = useState<McpServer[]>([]);
  const [form, setForm] = useState(EMPTY);

  const load = useCallback(
    async () => setServers(await api.mcp.list(session?.id)),
    [session?.id],
  );

  useEffect(() => {
    void load().catch(() => undefined);
  }, [load]);

  const create = async () => {
    try {
      const headers = form.headers.trim() ? JSON.parse(form.headers) : {};
      const env = form.env.trim() ? JSON.parse(form.env) : {};
      await api.mcp.create({
        name: form.name.trim(),
        description: form.description,
        transport: form.transport,
        command: form.command.trim(),
        args: form.args.split(/\s+/).filter(Boolean),
        url: form.url.trim(),
        headers,
        env,
      });
      setForm(EMPTY);
      await load();
      onToast('MCP 服务已创建', 'success');
    } catch (error) {
      onToast((error as Error).message, 'error');
    }
  };

  return (
    <div className="admin-panel">
      <section className="admin-section">
        <header className="admin-section-header">
          <div>
            <h3>MCP 服务</h3>
            <p className="muted small">
              支持 stdio、Streamable HTTP 和 SSE。工具名按 mcp__serverId__tool
              命名空间暴露。
            </p>
          </div>
        </header>
        <div className="admin-list">
          {servers.map((server) => (
            <div className="admin-row" key={server.id}>
              <div>
                <strong>{server.name}</strong>
                <span className="admin-tag">{server.transport}</span>
                <span
                  className={`admin-tag ${server.lastStatus === 'connected' ? 'success' : 'warn'}`}
                >
                  {server.lastStatus || '未连接'}
                </span>
                {session && server.sessionEnabled && (
                  <span className="admin-tag success">本会话启用</span>
                )}
                <p className="muted small">
                  {server.transport === 'stdio'
                    ? `${server.command} ${server.args.join(' ')}`
                    : server.url}
                </p>
                <p className="small">
                  工具：
                  {server.tools.map((tool) => tool.name).join(', ') || '未获取'}
                </p>
                {server.lastError && (
                  <p className="small error-text">{server.lastError}</p>
                )}
              </div>
              <div className="admin-actions">
                <Button
                  type="button"
                  variant="outline" size="sm"
                  onClick={() =>
                    void runAction(
                      () => api.mcp.test(server.id),
                      onToast,
                      '连接成功',
                    ).then(async (value) => {
                      if (value !== null) await load();
                    })
                  }
                >
                  <RefreshCw size={13} /> 测试
                </Button>
                <Button
                  type="button"
                  variant="outline" size="sm"
                  onClick={() =>
                    void runAction(
                      () => api.mcp.refresh(server.id),
                      onToast,
                      '工具已刷新',
                    ).then(async (value) => {
                      if (value !== null) await load();
                    })
                  }
                >
                  刷新
                </Button>
                {session && (
                  <Button
                    type="button"
                    variant="outline" size="sm"
                    onClick={() =>
                      void runAction(
                        () =>
                          api.mcp.toggle(
                            session.id,
                            server.id,
                            !server.sessionEnabled,
                          ),
                        onToast,
                        server.sessionEnabled ? '已停用' : '已启用',
                      ).then(async (value) => {
                        if (value !== null) await load();
                      })
                    }
                  >
                    {server.sessionEnabled ? '本会话停用' : '本会话启用'}
                  </Button>
                )}
                <Button
                  type="button"
                  variant="ghost" size="icon-sm" className="text-muted-foreground hover:bg-danger-soft hover:text-destructive"
                  aria-label="删除 MCP"
                  onClick={() =>
                    void runAction(
                      () => api.mcp.remove(server.id),
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
        <h3>新增 MCP 服务</h3>
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
            <span>传输</span>
            <SimpleSelect
              value={form.transport}
              onValueChange={(value) =>
                setForm({ ...form, transport: value as typeof form.transport })
              }
              options={[
                { value: 'stdio', label: 'stdio' },
                { value: 'http', label: 'Streamable HTTP' },
                { value: 'sse', label: 'SSE' },
              ]}
            />
          </label>
          <label className="field wide">
            <span>描述</span>
            <Input
              value={form.description}
              onChange={(event) =>
                setForm({ ...form, description: event.target.value })
              }
            />
          </label>
          {form.transport === 'stdio' ? (
            <>
              <label className="field">
                <span>命令</span>
                <Input
                  value={form.command}
                  onChange={(event) =>
                    setForm({ ...form, command: event.target.value })
                  }
                  placeholder="npx"
                />
              </label>
              <label className="field">
                <span>参数（空格分隔）</span>
                <Input
                  value={form.args}
                  onChange={(event) =>
                    setForm({ ...form, args: event.target.value })
                  }
                />
              </label>
              <label className="field wide">
                <span>环境变量 JSON</span>
                <Textarea
                  rows={2}
                  value={form.env}
                  onChange={(event) =>
                    setForm({ ...form, env: event.target.value })
                  }
                />
              </label>
            </>
          ) : (
            <>
              <label className="field wide">
                <span>URL</span>
                <Input
                  value={form.url}
                  onChange={(event) =>
                    setForm({ ...form, url: event.target.value })
                  }
                />
              </label>
              <label className="field wide">
                <span>Headers JSON</span>
                <Textarea
                  rows={2}
                  value={form.headers}
                  onChange={(event) =>
                    setForm({ ...form, headers: event.target.value })
                  }
                />
              </label>
            </>
          )}
        </div>
        <Button
          type="button"
          size="sm"
          disabled={!form.name.trim()}
          onClick={() => void create()}
        >
          <Plus size={14} /> 创建
        </Button>
      </section>
    </div>
  );
}
