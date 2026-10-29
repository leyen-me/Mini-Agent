import { Download, Plus, RotateCcw, Trash2, Upload } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { api, currentApiToken } from '../../api/client';
import type { BackupInfo, Workspace } from '../../api/types';
import type { ToastFn } from './useAdmin';
import { runAction } from './useAdmin';
import { Button, buttonVariants } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

interface Props {
  workspaces: Workspace[];
  onToast: ToastFn;
  onReload: () => Promise<void>;
}

export function DataPanel({ workspaces, onToast, onReload }: Props) {
  const [backups, setBackups] = useState<BackupInfo[]>([]);
  const [policy, setPolicy] = useState<{
    path: string | null;
    source: string;
    enforced: boolean;
  } | null>(null);
  const [workspaceForm, setWorkspaceForm] = useState({
    name: '',
    alias: '',
    description: '',
  });
  const [apiToken, setApiToken] = useState(() => currentApiToken());
  const importRef = useRef<HTMLInputElement>(null);

  const load = async () => {
    const [backupList, policyInfo] = await Promise.all([
      api.backups.list(),
      api.policy(),
    ]);
    setBackups(backupList);
    setPolicy(policyInfo);
  };

  useEffect(() => {
    void load().catch(() => undefined);
  }, []);

  const restore = async (backup: BackupInfo) => {
    if (
      !window.confirm(
        `恢复备份 ${backup.filename}？恢复将在下次重启时应用，当前数据会先自动备份。`,
      )
    )
      return;
    const result = await runAction(
      () => api.backups.restore(backup.id),
      onToast,
    );
    if (result) onToast('恢复已暂存，重启服务后生效', 'success');
  };

  const importSession = async (file: File) => {
    try {
      const data = JSON.parse(await file.text()) as Record<string, unknown>;
      const result = await api.importSession(data);
      onToast(`已导入会话：${result.session.title}`, 'success');
      await onReload();
    } catch (error) {
      onToast((error as Error).message, 'error');
    }
  };

  return (
    <div className="admin-panel">
      <section className="admin-section">
        <h3>工作区</h3>
        <div className="admin-list">
          {workspaces.map((workspace) => (
            <div className="admin-row" key={workspace.id}>
              <div>
                <strong>{workspace.alias || workspace.name}</strong>
                <span className="admin-tag">{workspace.id}</span>
                <p className="muted small">{workspace.rootPath}</p>
                {workspace.description && (
                  <p className="small">{workspace.description}</p>
                )}
              </div>
              <div className="admin-actions">
                <Button
                  type="button"
                  variant="outline" size="sm"
                  onClick={() => {
                    const alias = window.prompt(
                      '工作区别名',
                      workspace.alias || workspace.name,
                    );
                    if (alias === null) return;
                    void runAction(
                      () => api.workspacesApi.update(workspace.id, { alias }),
                      onToast,
                      '已更新',
                    ).then(async (value) => {
                      if (value !== null) await onReload();
                    });
                  }}
                >
                  重命名
                </Button>
                <Button
                  type="button"
                  variant="ghost" size="icon-sm" className="text-muted-foreground hover:bg-danger-soft hover:text-destructive"
                  aria-label="删除工作区"
                  onClick={() => {
                    if (!window.confirm('删除工作区？仅当没有会话时允许。'))
                      return;
                    void runAction(
                      () => api.workspacesApi.remove(workspace.id),
                      onToast,
                      '已删除',
                    ).then(async (value) => {
                      if (value !== null) await onReload();
                    });
                  }}
                >
                  <Trash2 size={14} />
                </Button>
              </div>
            </div>
          ))}
        </div>
        <div className="form-grid">
          <label className="field">
            <span>工作区名称</span>
            <Input
              value={workspaceForm.name}
              onChange={(event) =>
                setWorkspaceForm({ ...workspaceForm, name: event.target.value })
              }
            />
          </label>
          <label className="field">
            <span>别名</span>
            <Input
              value={workspaceForm.alias}
              onChange={(event) =>
                setWorkspaceForm({
                  ...workspaceForm,
                  alias: event.target.value,
                })
              }
            />
          </label>
          <label className="field wide">
            <span>描述</span>
            <Input
              value={workspaceForm.description}
              onChange={(event) =>
                setWorkspaceForm({
                  ...workspaceForm,
                  description: event.target.value,
                })
              }
            />
          </label>
        </div>
        <Button
          type="button"
          variant="outline" size="sm"
          disabled={!workspaceForm.name.trim()}
          onClick={() =>
            runAction(
              async () => {
                const workspace = await api.workspaces.create(
                  workspaceForm.name.trim(),
                );
                await api.workspacesApi.update(workspace.id, {
                  alias: workspaceForm.alias,
                  description: workspaceForm.description,
                });
              },
              onToast,
              '工作区已创建',
            ).then(async (value) => {
              if (value !== null) {
                setWorkspaceForm({ name: '', alias: '', description: '' });
                await onReload();
              }
            })
          }
        >
          <Plus size={14} /> 创建工作区
        </Button>
      </section>

      <section className="admin-section">
        <header className="admin-section-header">
          <div>
            <h3>备份与恢复</h3>
            <p className="muted small">
              备份包含 SQLite
              数据库快照，可选包含上传、artifacts、检查点。恢复需要重启服务。
            </p>
          </div>
          <Button
            type="button"
            size="sm"
            onClick={() =>
              runAction(
                () => api.backups.create(true),
                onToast,
                '备份已创建',
              ).then(async (value) => {
                if (value !== null) await load();
              })
            }
          >
            <Download size={14} /> 创建备份
          </Button>
        </header>
        <div className="admin-list">
          {backups.map((backup) => (
            <div className="admin-row" key={backup.id}>
              <div>
                <strong>{backup.filename}</strong>
                <p className="muted small">
                  {(backup.size / 1024).toFixed(1)} KB ·{' '}
                  {backup.includes.join(', ')} ·{' '}
                  {new Date(backup.createdAt).toLocaleString()}
                </p>
              </div>
              <div className="admin-actions">
                <a
                  className={buttonVariants({ variant: 'outline', size: 'sm' })}
                  href={api.backups.downloadUrl(backup.id)}
                >
                  下载
                </a>
                <Button
                  type="button"
                  variant="outline" size="sm"
                  onClick={() => void restore(backup)}
                >
                  <RotateCcw size={13} /> 恢复
                </Button>
                <Button
                  type="button"
                  variant="ghost" size="icon-sm" className="text-muted-foreground hover:bg-danger-soft hover:text-destructive"
                  aria-label="删除备份"
                  onClick={() =>
                    void runAction(
                      () => api.backups.remove(backup.id),
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
          {!backups.length && <p className="muted">尚无备份。</p>}
        </div>
      </section>

      <section className="admin-section">
        <h3>导入 / 运维</h3>
        <div className="admin-actions">
          <Button
            type="button"
            variant="outline" size="sm"
            onClick={() => importRef.current?.click()}
          >
            <Upload size={14} /> 导入会话 JSON
          </Button>
          <Button
            type="button"
            variant="outline" size="sm"
            onClick={() => void runAction(() => api.downloadLogs(), onToast)}
          >
            导出日志
          </Button>
          <Button
            type="button"
            variant="outline" size="sm"
            onClick={() =>
              void runAction(
                () => api.cleanupRetention(),
                onToast,
                '清理完成',
              ).then((value) => {
                if (value) onToast(`清理：${JSON.stringify(value)}`, 'info');
              })
            }
          >
            执行保留策略清理
          </Button>
        </div>
        <input
          ref={importRef}
          type="file"
          accept="application/json,.json"
          hidden
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void importSession(file);
            event.target.value = '';
          }}
        />
        {policy && (
          <p className="muted small">
            策略文件：{policy.path ?? '未配置'} · 来源：{policy.source} · 强制：
            {policy.enforced ? '是' : '否'}
          </p>
        )}
        <label className="field">
          <span>浏览器 API Token（仅本浏览器 local storage）</span>
          <Input
            type="password"
            value={apiToken}
            placeholder="远程部署或反向代理时填写"
            onChange={(event) => setApiToken(event.target.value)}
            onBlur={() => {
              try {
                if (apiToken.trim()) {
                  localStorage.setItem('mini-agent-api-token', apiToken.trim());
                } else {
                  localStorage.removeItem('mini-agent-api-token');
                }
                onToast('API Token 已保存到当前浏览器', 'success');
              } catch {
                onToast('无法保存 API Token', 'error');
              }
            }}
          />
        </label>
        <p className="muted small">
          Prometheus 指标：<code>/metrics</code>；健康检查：
          <code>/api/v1/healthz</code>。
        </p>
      </section>
    </div>
  );
}
