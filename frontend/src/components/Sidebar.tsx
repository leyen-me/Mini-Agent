import {
  Archive,
  Bot,
  Command,
  FolderTree,
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Pin,
  PinOff,
  Plug,
  Plus,
  Search,
  Settings,
  Sparkles,
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type { Session, Workspace } from '../api/types';
import { relativeTime } from '../lib/i18n';
import { useUi } from '../state/UiContext';
import { FileTree } from './FileTree';
import { SessionStatusBadge } from './StatusBadge';

interface Props {
  sessions: Session[];
  selectedId: string | null;
  workspaces: Workspace[];
  activeWorkspaceId: string | null;
  collapsed: boolean;
  width: number;
  mobile: boolean;
  onSelect: (id: string) => void;
  onNewSession: () => void;
  onOpenSettings: () => void;
  onOpenSkills: () => void;
  onOpenMcp: () => void;
  onOpenAgents: () => void;
  onOpenPalette: () => void;
  onSearch: (query: string) => void | Promise<void>;
  onWorkspaceChange: (id: string) => void;
  onPin: (session: Session, pinned: boolean) => void;
  onArchive: (session: Session) => void;
  onToggleCollapse: () => void;
  onWidthChange: (width: number) => void;
  onReferenceFile: (path: string) => void;
}

type Filter = 'all' | 'running' | 'waiting_user' | 'error';

export function Sidebar({
  sessions,
  selectedId,
  workspaces,
  activeWorkspaceId,
  collapsed,
  width,
  mobile,
  onSelect,
  onNewSession,
  onOpenSettings,
  onOpenSkills,
  onOpenMcp,
  onOpenAgents,
  onOpenPalette,
  onSearch,
  onWorkspaceChange,
  onPin,
  onArchive,
  onToggleCollapse,
  onWidthChange,
  onReferenceFile,
}: Props) {
  const { t, language } = useUi();
  const [tab, setTab] = useState<'sessions' | 'files'>('sessions');
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const searchTimer = useRef<number | null>(null);
  const firstSearch = useRef(true);
  const dragging = useRef(false);

  useEffect(() => {
    if (firstSearch.current) {
      firstSearch.current = false;
      return;
    }
    if (searchTimer.current) window.clearTimeout(searchTimer.current);
    searchTimer.current = window.setTimeout(() => onSearch(query.trim()), 250);
    return () => {
      if (searchTimer.current) window.clearTimeout(searchTimer.current);
    };
  }, [query, onSearch]);

  useEffect(() => {
    const onMove = (event: PointerEvent) => {
      if (!dragging.current) return;
      onWidthChange(Math.min(420, Math.max(232, event.clientX)));
    };
    const onUp = () => {
      dragging.current = false;
      document.body.classList.remove('resizing-sidebar');
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    return () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
  }, [onWidthChange]);

  const visible = sessions.filter((session) => {
    if (filter === 'all') return true;
    if (filter === 'running') return session.status === 'running' || session.status === 'compacting';
    if (filter === 'waiting_user') return session.status === 'waiting_user';
    return session.status === 'error';
  });

  return (
    <aside
      className={`sidebar ${collapsed ? 'collapsed' : ''} ${mobile ? 'mobile' : ''}`}
      style={!mobile ? { width: collapsed ? 64 : width } : undefined}
      aria-label={t('app.sessions')}
    >
      <div className="sidebar-top">
        <div className="brand">
          <button
            type="button"
            className="brand-mark"
            onClick={onNewSession}
            title={t('app.newSession')}
            aria-label={t('app.newSession')}
          >
            <span aria-hidden="true">M</span>
          </button>
          {!collapsed && (
            <div className="brand-text">
              <strong>{t('app.name')}</strong>
              <span>{t('app.tagline')}</span>
            </div>
          )}
          {!mobile && (
            <button
              type="button"
              className="icon-button collapse-button"
              onClick={onToggleCollapse}
              title={collapsed ? t('app.sessions') : t('app.sidebar.collapse')}
              aria-label={collapsed ? t('app.sessions') : t('app.sidebar.collapse')}
            >
              {collapsed ? <PanelLeftOpen size={16} aria-hidden="true" /> : <PanelLeftClose size={16} aria-hidden="true" />}
            </button>
          )}
        </div>

        <button type="button" className="new-session-button" onClick={onNewSession} title={t('app.newSession')}>
          <Plus size={16} aria-hidden="true" />
          {!collapsed && <span>{t('app.newSession')}</span>}
          {!collapsed && <kbd>⌘N</kbd>}
        </button>

        <nav className="rail-actions" aria-label={t('app.skills')}>
          <button type="button" className="rail-button" onClick={onOpenSkills} title={t('app.skills')}>
            <Sparkles size={15} aria-hidden="true" />
            {!collapsed && <span>{t('app.skills')}</span>}
          </button>
          <button type="button" className="rail-button" onClick={onOpenMcp} title={t('app.mcp')}>
            <Plug size={15} aria-hidden="true" />
            {!collapsed && <span>{t('app.mcp')}</span>}
          </button>
          <button type="button" className="rail-button" onClick={onOpenAgents} title={t('app.agents')}>
            <Bot size={15} aria-hidden="true" />
            {!collapsed && <span>{t('app.agents')}</span>}
          </button>
          <button type="button" className="rail-button" onClick={onOpenPalette} title={t('app.commandPalette')}>
            <Command size={15} aria-hidden="true" />
            {!collapsed && <span>{t('app.commandPalette')}</span>}
          </button>
        </nav>

        {!collapsed && (
          <label className="workspace-select">
            <span>{t('app.workspace')}</span>
            <select
              value={activeWorkspaceId ?? ''}
              onChange={(event) => onWorkspaceChange(event.target.value)}
              aria-label={t('app.workspace')}
            >
              {workspaces.map((workspace) => (
                <option key={workspace.id} value={workspace.id}>
                  {workspace.name}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      {!collapsed && (
        <div className="sidebar-middle">
          <div className="sidebar-tabs" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={tab === 'sessions'}
              className={tab === 'sessions' ? 'active' : ''}
              onClick={() => setTab('sessions')}
            >
              <MessageSquare size={13} aria-hidden="true" /> {t('app.sessions')}
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === 'files'}
              className={tab === 'files' ? 'active' : ''}
              onClick={() => setTab('files')}
            >
              <FolderTree size={13} aria-hidden="true" /> {t('app.files')}
            </button>
          </div>

          {tab === 'sessions' ? (
            <>
              <div className="search-row">
                <Search size={14} aria-hidden="true" />
                <input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder={t('app.searchSessions')}
                  aria-label={t('app.searchSessions')}
                />
              </div>
              <div className="filter-row">
                {(['all', 'running', 'waiting_user', 'error'] as const).map((value) => (
                  <button
                    type="button"
                    key={value}
                    className={filter === value ? 'active' : ''}
                    onClick={() => setFilter(value)}
                  >
                    {value === 'all' ? t('app.all') : t(`status.${value}`)}
                  </button>
                ))}
              </div>
              <div className="session-list" role="list">
                {visible.length === 0 && (
                  <div className="session-empty">
                    <MessageSquare size={20} aria-hidden="true" />
                    <p>{t('app.noSessions')}</p>
                    <span>{t('app.noSessionsHint')}</span>
                  </div>
                )}
                {visible.map((session) => (
                  <div
                    key={session.id}
                    className={`session-item ${selectedId === session.id ? 'active' : ''}`}
                    role="listitem"
                  >
                    <button type="button" className="session-main" onClick={() => onSelect(session.id)}>
                      <div className="session-title-row">
                        {session.pinned && <Pin size={11} aria-label={t('app.pinned')} />}
                        <span className="session-title">{session.title || t('app.newSession')}</span>
                      </div>
                      <div className="session-sub">
                        <SessionStatusBadge status={session.status} compact />
                        <span className="session-time">{relativeTime(session.updatedAt, language)}</span>
                      </div>
                    </button>
                    <div className="session-actions">
                      <button
                        type="button"
                        className="icon-button"
                        title={session.pinned ? t('app.unpin') : t('app.pin')}
                        aria-label={session.pinned ? t('app.unpin') : t('app.pin')}
                        onClick={() => onPin(session, !session.pinned)}
                      >
                        {session.pinned ? <PinOff size={13} aria-hidden="true" /> : <Pin size={13} aria-hidden="true" />}
                      </button>
                      <button
                        type="button"
                        className="icon-button"
                        title={t('app.archive')}
                        aria-label={t('app.archive')}
                        onClick={() => onArchive(session)}
                      >
                        <Archive size={13} aria-hidden="true" />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </>
          ) : activeWorkspaceId ? (
            <FileTree workspaceId={activeWorkspaceId} onReference={onReferenceFile} />
          ) : null}
        </div>
      )}

      <div className="sidebar-bottom">
        <button type="button" className="rail-button" onClick={onOpenSettings} title={t('app.settings')}>
          <Settings size={16} aria-hidden="true" />
          {!collapsed && <span>{t('app.settings')}</span>}
        </button>
        {!collapsed && <span className="sidebar-version">v0.1</span>}
      </div>

      {!mobile && !collapsed && (
        <div
          className="resize-handle"
          role="separator"
          aria-orientation="vertical"
          aria-label={t('app.sidebar.resize')}
          onPointerDown={() => {
            dragging.current = true;
            document.body.classList.add('resizing-sidebar');
          }}
        />
      )}
    </aside>
  );
}
