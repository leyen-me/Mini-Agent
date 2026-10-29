import { Play, Plus, Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { AgentInfo, CommandInfo } from '../../api/types';
import type { ToastFn } from './useAdmin';
import { runAction } from './useAdmin';

interface Props {
  agents: AgentInfo[];
  onToast: ToastFn;
}

const EMPTY = {
  name: '',
  description: '',
  promptTemplate: '',
  agentId: '',
  model: '',
  reasoningLevel: '',
};

export function CommandsPanel({ agents, onToast }: Props) {
  const [commands, setCommands] = useState<CommandInfo[]>([]);
  const [form, setForm] = useState(EMPTY);
  const [editing, setEditing] = useState<CommandInfo | null>(null);
  const [preview, setPreview] = useState<{
    name: string;
    args: string;
    prompt: string;
  } | null>(null);
  const [previewArgs, setPreviewArgs] = useState('');

  const load = async () => setCommands(await api.commands.list());
  useEffect(() => {
    void load().catch(() => undefined);
  }, []);

  const startEdit = (command: CommandInfo) => {
    setEditing(command);
    setForm({
      name: command.name,
      description: command.description,
      promptTemplate: command.promptTemplate,
      agentId: command.agentId ?? '',
      model: command.model ?? '',
      reasoningLevel: command.reasoningLevel ?? '',
    });
  };

  const save = async () => {
    try {
      if (!form.name.trim() || !form.promptTemplate.trim())
        throw new Error('名称和模板不能为空');
      const body = {
        name: form.name.trim().replace(/^\//, ''),
        description: form.description,
        promptTemplate: form.promptTemplate,
        agentId: form.agentId || null,
        model: form.model || null,
        reasoningLevel: form.reasoningLevel || null,
      };
      if (editing) await api.commands.update(editing.id, body);
      else await api.commands.create(body);
      setEditing(null);
      setForm(EMPTY);
      await load();
      onToast('命令已保存', 'success');
    } catch (error) {
      onToast((error as Error).message, 'error');
    }
  };

  const runPreview = async (command: CommandInfo) => {
    try {
      const result = await api.commands.render(command.id, previewArgs);
      setPreview({
        name: command.name,
        args: previewArgs,
        prompt: result.prompt,
      });
    } catch (error) {
      onToast((error as Error).message, 'error');
    }
  };

  return (
    <div className="admin-panel">
      <section className="admin-section">
        <h3>自定义命令</h3>
        <p className="muted small">
          支持 $ARGUMENTS 与 $1..$9 占位符；插件命令只读展示。
        </p>
        <div className="admin-list">
          {commands.map((command) => (
            <div className="admin-row" key={command.id}>
              <div>
                <strong>/{command.name}</strong>
                {command.pluginId && (
                  <span className="admin-tag">插件 {command.pluginId}</span>
                )}
                <p className="muted small">{command.description}</p>
                <pre className="admin-detail">{command.promptTemplate}</pre>
              </div>
              <div className="admin-actions">
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() => {
                    setPreviewArgs('');
                    void runPreview(command);
                  }}
                >
                  <Play size={13} /> 预览
                </button>
                {!command.pluginId && (
                  <>
                    <button
                      type="button"
                      className="secondary-button"
                      onClick={() => startEdit(command)}
                    >
                      编辑
                    </button>
                    <button
                      type="button"
                      className="icon-button danger"
                      aria-label="删除命令"
                      onClick={() =>
                        void runAction(
                          () => api.commands.remove(command.id),
                          onToast,
                          '已删除',
                        ).then(async (value) => {
                          if (value !== null) await load();
                        })
                      }
                    >
                      <Trash2 size={14} />
                    </button>
                  </>
                )}
              </div>
              {preview?.name === command.name && (
                <div className="command-inline-preview">
                  <label className="field">
                    <span>参数</span>
                    <input
                      value={previewArgs}
                      onChange={(event) => setPreviewArgs(event.target.value)}
                      onKeyDown={(event) =>
                        event.key === 'Enter' && void runPreview(command)
                      }
                    />
                  </label>
                  <pre>{preview.prompt}</pre>
                </div>
              )}
            </div>
          ))}
        </div>
      </section>

      <section className="admin-section">
        <h3>{editing ? `编辑 /${editing.name}` : '新建命令'}</h3>
        <div className="form-grid">
          <label className="field">
            <span>名称</span>
            <input
              value={form.name}
              onChange={(event) =>
                setForm({ ...form, name: event.target.value })
              }
              placeholder="review"
            />
          </label>
          <label className="field">
            <span>指定 Agent</span>
            <select
              value={form.agentId}
              onChange={(event) =>
                setForm({ ...form, agentId: event.target.value })
              }
            >
              <option value="">不改变</option>
              {agents.map((agent) => (
                <option key={agent.id} value={agent.id}>
                  {agent.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>指定模型</span>
            <input
              value={form.model}
              onChange={(event) =>
                setForm({ ...form, model: event.target.value })
              }
              placeholder="provider/model"
            />
          </label>
          <label className="field">
            <span>推理等级</span>
            <input
              value={form.reasoningLevel}
              onChange={(event) =>
                setForm({ ...form, reasoningLevel: event.target.value })
              }
              placeholder="off / high"
            />
          </label>
          <label className="field wide">
            <span>描述</span>
            <input
              value={form.description}
              onChange={(event) =>
                setForm({ ...form, description: event.target.value })
              }
            />
          </label>
          <label className="field wide">
            <span>Prompt 模板</span>
            <textarea
              rows={6}
              value={form.promptTemplate}
              onChange={(event) =>
                setForm({ ...form, promptTemplate: event.target.value })
              }
              placeholder="请审查 $1，重点关注：$ARGUMENTS"
            />
          </label>
        </div>
        <div className="admin-actions">
          <button
            type="button"
            className="primary-button"
            onClick={() => void save()}
          >
            <Plus size={14} /> {editing ? '保存' : '创建'}
          </button>
          {editing && (
            <button
              type="button"
              className="secondary-button"
              onClick={() => {
                setEditing(null);
                setForm(EMPTY);
              }}
            >
              取消
            </button>
          )}
        </div>
      </section>
    </div>
  );
}
