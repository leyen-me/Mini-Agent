import { Plus, Trash2, Upload } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { api } from '../../api/client';
import type { AgentInfo } from '../../api/types';
import type { ToastFn } from './useAdmin';
import { runAction, splitList } from './useAdmin';

interface Props {
  agents: AgentInfo[];
  onToast: ToastFn;
  onReload: () => Promise<void>;
}

interface Draft {
  name: string;
  description: string;
  systemPrompt: string;
  readOnly: boolean;
  defaultModel: string;
  defaultReasoningLevel: string;
  tools: string;
  skills: string;
  mcp: string;
  permissions: string;
  structuredOutput: string;
}

const EMPTY: Draft = {
  name: '',
  description: '',
  systemPrompt: '',
  readOnly: false,
  defaultModel: '',
  defaultReasoningLevel: 'off',
  tools: '',
  skills: '',
  mcp: '',
  permissions: '{}',
  structuredOutput: '',
};

export function AgentsPanel({ agents, onToast, onReload }: Props) {
  const [editing, setEditing] = useState<AgentInfo | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const importRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!editing) {
      setDraft(EMPTY);
      return;
    }
    setDraft({
      name: editing.name,
      description: editing.description,
      systemPrompt: editing.systemPrompt ?? '',
      readOnly: editing.readOnly,
      defaultModel: editing.defaultModel ?? '',
      defaultReasoningLevel: editing.defaultReasoningLevel ?? 'off',
      tools: (editing.tools ?? []).join(', '),
      skills: (editing.skills ?? []).join(', '),
      mcp: (editing.mcp ?? []).join(', '),
      permissions: JSON.stringify(editing.permissions ?? {}, null, 2),
      structuredOutput: editing.structuredOutput
        ? JSON.stringify(editing.structuredOutput, null, 2)
        : '',
    });
  }, [editing]);

  const payload = () => {
    let permissions: unknown = {};
    let structuredOutput: unknown = null;
    try {
      permissions = draft.permissions.trim()
        ? JSON.parse(draft.permissions)
        : {};
    } catch {
      throw new Error('权限 JSON 无效');
    }
    try {
      structuredOutput = draft.structuredOutput.trim()
        ? JSON.parse(draft.structuredOutput)
        : null;
    } catch {
      throw new Error('结构化输出 JSON 无效');
    }
    return {
      name: draft.name.trim(),
      description: draft.description.trim(),
      systemPrompt: draft.systemPrompt,
      readOnly: draft.readOnly,
      defaultModel: draft.defaultModel.trim() || null,
      defaultReasoningLevel: draft.defaultReasoningLevel,
      tools: draft.tools.trim() ? splitList(draft.tools) : null,
      skills: splitList(draft.skills),
      mcp: splitList(draft.mcp),
      permissions,
      structuredOutput,
    };
  };

  const save = async () => {
    try {
      const body = payload();
      if (!body.name) throw new Error('名称不能为空');
      if (editing) await api.agentsApi.update(editing.id, body);
      else await api.agentsApi.create(body);
      setEditing(null);
      await onReload();
      onToast(editing ? 'Agent 已更新' : 'Agent 已创建', 'success');
    } catch (error) {
      onToast((error as Error).message, 'error');
    }
  };

  return (
    <div className="admin-panel">
      <section className="admin-section">
        <header className="admin-section-header">
          <div>
            <h3>Agent</h3>
            <p className="muted small">
              工具白名单为空表示不限制；权限配置会与全局权限取更严格值。
            </p>
          </div>
          <div className="admin-actions">
            <button
              type="button"
              className="secondary-button"
              onClick={() => importRef.current?.click()}
            >
              <Upload size={14} /> 导入
            </button>
            <button
              type="button"
              className="primary-button"
              onClick={() => setEditing(null)}
            >
              <Plus size={14} /> 新建
            </button>
            <input
              ref={importRef}
              type="file"
              accept="application/json,.json"
              hidden
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = '';
                if (!file) return;
                void file.text().then(async (text) => {
                  try {
                    await api.agentsApi.import(
                      JSON.parse(text) as Record<string, unknown>,
                    );
                    await onReload();
                    onToast('Agent 已导入', 'success');
                  } catch (error) {
                    onToast((error as Error).message, 'error');
                  }
                });
              }}
            />
          </div>
        </header>
        <div className="admin-list">
          {agents.map((agent) => (
            <div className="admin-row" key={agent.id}>
              <div>
                <strong>{agent.name}</strong>
                <span className="admin-tag">{agent.id}</span>
                {agent.readOnly && <span className="admin-tag warn">只读</span>}
                {agent.builtin && <span className="admin-tag">内置</span>}
                <p className="muted small">{agent.description}</p>
                <p className="small">
                  模型：{agent.defaultModel || '默认'} · 工具：
                  {agent.tools === null || agent.tools === undefined
                    ? '全部'
                    : `${agent.tools.length} 个`}
                </p>
              </div>
              <div className="admin-actions">
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() => setEditing(agent)}
                >
                  编辑
                </button>
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() =>
                    void runAction(
                      () => api.agentsApi.duplicate(agent.id),
                      onToast,
                      '已复制',
                    ).then(async (value) => {
                      if (value !== null) await onReload();
                    })
                  }
                >
                  复制
                </button>
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() =>
                    void runAction(
                      async () => {
                        const data = await api.agentsApi.export(agent.id);
                        const blob = new Blob([JSON.stringify(data, null, 2)], {
                          type: 'application/json',
                        });
                        const url = URL.createObjectURL(blob);
                        const anchor = document.createElement('a');
                        anchor.href = url;
                        anchor.download = `agent-${agent.id}.json`;
                        anchor.click();
                        URL.revokeObjectURL(url);
                      },
                      onToast,
                      '已导出',
                    )
                  }
                >
                  导出
                </button>
                <button
                  type="button"
                  className="icon-button danger"
                  aria-label="删除 Agent"
                  disabled={agent.builtin}
                  onClick={() =>
                    void runAction(
                      () => api.agentsApi.remove(agent.id),
                      onToast,
                      '已删除',
                    ).then(async (value) => {
                      if (value !== null) await onReload();
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
        <h3>{editing ? `编辑 ${editing.name}` : '新建 Agent'}</h3>
        <div className="form-grid">
          <label className="field">
            <span>名称</span>
            <input
              value={draft.name}
              onChange={(event) =>
                setDraft({ ...draft, name: event.target.value })
              }
            />
          </label>
          <label className="field">
            <span>默认模型</span>
            <input
              value={draft.defaultModel}
              placeholder="provider/model"
              onChange={(event) =>
                setDraft({ ...draft, defaultModel: event.target.value })
              }
            />
          </label>
          <label className="field wide">
            <span>描述</span>
            <input
              value={draft.description}
              onChange={(event) =>
                setDraft({ ...draft, description: event.target.value })
              }
            />
          </label>
          <label className="field wide">
            <span>系统 Prompt</span>
            <textarea
              rows={4}
              value={draft.systemPrompt}
              onChange={(event) =>
                setDraft({ ...draft, systemPrompt: event.target.value })
              }
            />
          </label>
          <label className="field wide">
            <span>工具白名单（逗号分隔，留空为全部）</span>
            <input
              value={draft.tools}
              placeholder="read_file, grep, shell"
              onChange={(event) =>
                setDraft({ ...draft, tools: event.target.value })
              }
            />
          </label>
          <label className="field">
            <span>默认 Skill</span>
            <input
              value={draft.skills}
              placeholder="code-review"
              onChange={(event) =>
                setDraft({ ...draft, skills: event.target.value })
              }
            />
          </label>
          <label className="field">
            <span>默认 MCP</span>
            <input
              value={draft.mcp}
              placeholder="mcp_..."
              onChange={(event) =>
                setDraft({ ...draft, mcp: event.target.value })
              }
            />
          </label>
          <label className="field">
            <span>权限 JSON</span>
            <textarea
              rows={3}
              value={draft.permissions}
              onChange={(event) =>
                setDraft({ ...draft, permissions: event.target.value })
              }
            />
          </label>
          <label className="field">
            <span>结构化输出 JSON Schema（可选）</span>
            <textarea
              rows={3}
              value={draft.structuredOutput}
              onChange={(event) =>
                setDraft({ ...draft, structuredOutput: event.target.value })
              }
            />
          </label>
          <label className="checkbox-field">
            <input
              type="checkbox"
              checked={draft.readOnly}
              onChange={(event) =>
                setDraft({ ...draft, readOnly: event.target.checked })
              }
            />
            <span>只读（强制 Plan 模式）</span>
          </label>
        </div>
        <div className="admin-actions">
          <button
            type="button"
            className="primary-button"
            onClick={() => void save()}
          >
            {editing ? '保存' : '创建'}
          </button>
          {editing && (
            <button
              type="button"
              className="secondary-button"
              onClick={() => setEditing(null)}
            >
              取消
            </button>
          )}
        </div>
      </section>
    </div>
  );
}
