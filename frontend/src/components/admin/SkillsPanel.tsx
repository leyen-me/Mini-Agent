import { Plus, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { Session, SkillInfo } from '../../api/types';
import type { ToastFn } from './useAdmin';
import { runAction } from './useAdmin';

interface Props {
  session: Session | null;
  activeWorkspace: string | null;
  onToast: ToastFn;
}

export function SkillsPanel({ session, activeWorkspace, onToast }: Props) {
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [form, setForm] = useState({
    scope: 'system' as 'system' | 'workspace',
    name: '',
    description: '',
    content: '',
  });
  const [expanded, setExpanded] = useState<string | null>(null);
  const [detail, setDetail] = useState('');

  const load = useCallback(
    async () => setSkills(await api.skills.list(session?.id)),
    [session?.id],
  );

  useEffect(() => {
    void load().catch(() => undefined);
  }, [load]);

  const show = async (skill: SkillInfo) => {
    if (expanded === skill.name) {
      setExpanded(null);
      return;
    }
    setExpanded(skill.name);
    setDetail('加载中…');
    try {
      const data = await api.skills.get(skill.name, session?.id);
      setDetail(data.content || '');
    } catch (error) {
      setDetail((error as Error).message);
    }
  };

  const save = async () => {
    if (!form.name.trim()) return;
    const manifest = `---\nname: ${form.name.trim()}\ndescription: ${form.description.trim()}\n---\n\n${form.content}`;
    await runAction(
      () =>
        api.skills.save({
          scope: form.scope,
          name: form.name.trim(),
          description: form.description,
          content: manifest,
          workspaceId:
            form.scope === 'workspace'
              ? (activeWorkspace ?? undefined)
              : undefined,
        }),
      onToast,
      'Skill 已保存',
    );
    setForm({ ...form, name: '', description: '', content: '' });
    await load();
  };

  return (
    <div className="admin-panel">
      <section className="admin-section">
        <header className="admin-section-header">
          <div>
            <h3>Skill</h3>
            <p className="muted small">
              system prompt 只注入元信息；完整内容由 load_skill
              工具按需读取，避免污染前缀缓存。
            </p>
          </div>
          {session && (
            <span className="admin-tag">会话 {session.id.slice(-6)}</span>
          )}
        </header>
        <div className="admin-list">
          {skills.map((skill) => (
            <div className="admin-row" key={`${skill.scope}-${skill.name}`}>
              <div>
                <strong>{skill.name}</strong>
                <span className="admin-tag">{skill.scope}</span>
                {skill.enabled && (
                  <span className="admin-tag success">已启用</span>
                )}
                <p className="muted small">{skill.description}</p>
                <p className="small">
                  触发词：{skill.triggers.join(', ') || '无'} · 文件：
                  {skill.files.length}
                </p>
              </div>
              <div className="admin-actions">
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() => void show(skill)}
                >
                  {expanded === skill.name ? '收起' : '查看'}
                </button>
                {session && (
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() =>
                      void runAction(
                        () =>
                          api.skills.toggle(
                            session.id,
                            skill.name,
                            !skill.enabled,
                          ),
                        onToast,
                        skill.enabled ? '已停用' : '已启用',
                      ).then(async (value) => {
                        if (value !== null) await load();
                      })
                    }
                  >
                    {skill.enabled ? '停用' : '启用'}
                  </button>
                )}
                {skill.scope !== 'builtin' && (
                  <button
                    type="button"
                    className="icon-button danger"
                    aria-label="删除 Skill"
                    onClick={() =>
                      void runAction(
                        () =>
                          api.skills.remove(
                            skill.scope,
                            skill.name,
                            skill.scope === 'workspace'
                              ? (activeWorkspace ?? undefined)
                              : undefined,
                          ),
                        onToast,
                        '已删除',
                      ).then(async (value) => {
                        if (value !== null) await load();
                      })
                    }
                  >
                    <Trash2 size={14} />
                  </button>
                )}
              </div>
              {expanded === skill.name && (
                <pre className="admin-detail">{detail}</pre>
              )}
            </div>
          ))}
        </div>
      </section>

      <section className="admin-section">
        <h3>新建 Skill</h3>
        <div className="form-grid">
          <label className="field">
            <span>作用域</span>
            <select
              value={form.scope}
              onChange={(event) =>
                setForm({
                  ...form,
                  scope: event.target.value as 'system' | 'workspace',
                })
              }
            >
              <option value="system">系统（data/skills）</option>
              <option value="workspace">工作区（.mini-agent/skills）</option>
            </select>
          </label>
          <label className="field">
            <span>名称</span>
            <input
              value={form.name}
              onChange={(event) =>
                setForm({ ...form, name: event.target.value })
              }
              placeholder="release-notes"
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
            <span>正文（Markdown）</span>
            <textarea
              rows={8}
              value={form.content}
              onChange={(event) =>
                setForm({ ...form, content: event.target.value })
              }
            />
          </label>
        </div>
        <button
          type="button"
          className="primary-button"
          disabled={!form.name.trim()}
          onClick={() => void save()}
        >
          <Plus size={14} /> 保存 Skill
        </button>
      </section>
    </div>
  );
}
