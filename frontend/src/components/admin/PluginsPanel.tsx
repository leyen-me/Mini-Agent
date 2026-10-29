import { ShieldCheck, Trash2, Upload } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { api } from '../../api/client';
import type { PluginInfo } from '../../api/types';
import type { ToastFn } from './useAdmin';
import { runAction } from './useAdmin';

interface Props {
  onToast: ToastFn;
  onReload: () => Promise<void>;
}

export function PluginsPanel({ onToast, onReload }: Props) {
  const [plugins, setPlugins] = useState<PluginInfo[]>([]);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const load = async () => setPlugins(await api.plugins.list());
  useEffect(() => {
    void load().catch(() => undefined);
  }, []);

  const install = async (file: File) => {
    setBusy(true);
    try {
      await api.plugins.install(file);
      await load();
      await onReload();
      onToast('插件已安装，请在列表中信任后启用', 'success');
    } catch (error) {
      onToast((error as Error).message, 'error');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="admin-panel">
      <section className="admin-section">
        <header className="admin-section-header">
          <div>
            <h3>插件</h3>
            <p className="muted small">
              插件只能来自手动上传的
              zip；安装后默认不信任、不启用。插件可贡献工具、Agent、命令、Hook
              和 UI 元数据。
            </p>
          </div>
          <button
            type="button"
            className="primary-button"
            disabled={busy}
            onClick={() => inputRef.current?.click()}
          >
            <Upload size={14} /> 安装插件
          </button>
          <input
            ref={inputRef}
            type="file"
            accept=".zip,application/zip"
            hidden
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void install(file);
              event.target.value = '';
            }}
          />
        </header>
        <div className="admin-list">
          {plugins.map((plugin) => (
            <div className="admin-row" key={plugin.id}>
              <div>
                <strong>{plugin.name}</strong>
                <span className="admin-tag">{plugin.id}</span>
                <span className="admin-tag">v{plugin.version}</span>
                {plugin.trusted ? (
                  <span className="admin-tag success">已信任</span>
                ) : (
                  <span className="admin-tag warn">未信任</span>
                )}
                {plugin.enabled && (
                  <span className="admin-tag success">已启用</span>
                )}
                <p className="muted small">{plugin.description}</p>
                <p className="small">工具：{plugin.tools.join(', ') || '无'}</p>
              </div>
              <div className="admin-actions">
                {!plugin.trusted ? (
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() =>
                      void runAction(
                        () => api.plugins.trust(plugin.id, true),
                        onToast,
                        '已信任',
                      ).then(async (value) => {
                        if (value !== null) {
                          await load();
                          await onReload();
                        }
                      })
                    }
                  >
                    <ShieldCheck size={13} /> 信任
                  </button>
                ) : (
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() =>
                      void runAction(
                        () => api.plugins.enable(plugin.id, !plugin.enabled),
                        onToast,
                        plugin.enabled ? '已停用' : '已启用',
                      ).then(async (value) => {
                        if (value !== null) {
                          await load();
                          await onReload();
                        }
                      })
                    }
                  >
                    {plugin.enabled ? '停用' : '启用'}
                  </button>
                )}
                <button
                  type="button"
                  className="icon-button danger"
                  aria-label="卸载插件"
                  onClick={() =>
                    void runAction(
                      () => api.plugins.remove(plugin.id),
                      onToast,
                      '已卸载',
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
            </div>
          ))}
          {!plugins.length && <p className="muted">尚未安装插件。</p>}
        </div>
      </section>
    </div>
  );
}
