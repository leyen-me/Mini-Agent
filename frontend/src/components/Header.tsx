import {
  ChevronDown,
  Download,
  Gauge,
  Menu,
  Moon,
  MoreHorizontal,
  Pencil,
  Settings,
  Share2,
  Sun,
  Target,
  Terminal,
  X,
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type {
  AgentInfo,
  Session,
  SessionUsage,
  Snapshot,
  StreamStatus,
  Workspace,
} from '../api/types';
import { formatCost, formatTokens } from '../lib/i18n';
import { useUi } from '../state/UiContext';

interface Props {
  session: Session;
  snapshot: Snapshot | null;
  workspace: Workspace | null;
  agent: AgentInfo | null;
  streamStatus: StreamStatus;
  retryNotice: string | null;
  onRename: (title: string) => Promise<void>;
  onOpenSettings: () => void;
  onOpenGoal: () => void;
  onOpenShare: () => void;
  onExport: (format: 'markdown' | 'json') => void;
  onCompact: () => void;
  onToggleSidebar: () => void;
  mobile: boolean;
  sidebarCollapsed: boolean;
}

function statusCopy(status: StreamStatus, t: (key: string) => string) {
  return t(`app.connection.${status}`);
}

export function Header({
  session,
  snapshot,
  workspace,
  agent,
  streamStatus,
  retryNotice,
  onRename,
  onOpenSettings,
  onOpenGoal,
  onOpenShare,
  onExport,
  onCompact,
  onToggleSidebar,
  mobile,
  sidebarCollapsed,
}: Props) {
  const { t, themeSetting, resolvedTheme, setTheme } = useUi();
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(session.title);
  const [usageOpen, setUsageOpen] = useState(false);
  const [actionsOpen, setActionsOpen] = useState(false);
  const [connectionDismissed, setConnectionDismissed] = useState(false);
  const usageRef = useRef<HTMLDivElement>(null);
  const actionsRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setTitle(session.title);
  }, [session.title]);

  useEffect(() => {
    if (streamStatus === 'connected') setConnectionDismissed(false);
  }, [streamStatus]);

  useEffect(() => {
    if (editing) inputRef.current?.select();
  }, [editing]);

  useEffect(() => {
    const handler = (event: MouseEvent) => {
      if (usageRef.current && !usageRef.current.contains(event.target as Node)) setUsageOpen(false);
      if (actionsRef.current && !actionsRef.current.contains(event.target as Node)) setActionsOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const saveTitle = async () => {
    const next = title.trim();
    setEditing(false);
    if (next && next !== session.title) await onRename(next);
    else setTitle(session.title);
  };

  const usage: SessionUsage = snapshot?.usage ?? {
    contextUsed: 0,
    contextWindow: 128000,
    contextPercent: 0,
    model: session.model,
  };
  const goal = snapshot?.goal;
  const goalDone = goal?.tasks.filter((task) => task.status === 'completed').length ?? 0;
  const goalTotal = goal?.tasks.length ?? 0;
  const warning = usage.contextPercent >= 80;
  const runningTasks = snapshot?.tasks.filter((task) => task.status === 'running' || task.status === 'queued') ?? [];
  const showExpandButton = mobile || sidebarCollapsed;

  return (
    <header className="chat-header">
      <div className="header-left">
        {showExpandButton && (
          <button
            type="button"
            className="icon-button"
            onClick={onToggleSidebar}
            aria-label={t('app.sessions')}
            title={t('app.sessions')}
          >
            <Menu size={18} aria-hidden="true" />
          </button>
        )}
        <div className="title-block">
          {editing ? (
            <input
              ref={inputRef}
              className="title-input"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              onBlur={() => void saveTitle()}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault();
                  void saveTitle();
                }
                if (event.key === 'Escape') {
                  setTitle(session.title);
                  setEditing(false);
                }
              }}
              aria-label={t('app.sessions')}
            />
          ) : (
            <button type="button" className="title-button" onClick={() => setEditing(true)} title={t('app.edit')}>
              <span className="session-title">{session.title}</span>
              <Pencil size={12} aria-hidden="true" className="title-pencil" />
            </button>
          )}
          <div className="session-meta">
            <span className="meta-chip" title={workspace?.rootPath}>
              <Terminal size={12} aria-hidden="true" /> {workspace?.name ?? '—'}
            </span>
            <span className="meta-dot" aria-hidden="true">·</span>
            <span className="meta-chip">{agent?.name ?? session.agentId}</span>
            <span className={`meta-chip mode-chip mode-${session.mode}`}>
              {session.mode === 'plan' ? t('chat.mode.plan') : t('chat.mode.build')}
            </span>
            {runningTasks.length > 0 && (
              <span className="meta-chip task-chip" title={t('chat.tasks')}>
                <span className="task-pulse" aria-hidden="true" /> {t('chat.tasks')} {runningTasks.length}
              </span>
            )}
          </div>
        </div>
      </div>

      <div className="header-right">
        {retryNotice && <span className="retry-chip" title={retryNotice}>{t('chat.retryBadge')}</span>}
        <span className={`connection-chip connection-${streamStatus}`} title={t('app.connection.retryHint')}>
          <span className="connection-dot" aria-hidden="true" />
          <span>{statusCopy(streamStatus, t)}</span>
        </span>
        {goal && (
          <button type="button" className="goal-chip" onClick={onOpenGoal} title={goal.objective}>
            <Target size={14} aria-hidden="true" />
            <span className="goal-chip-label">{goal.objective}</span>
            <span className="goal-chip-progress">
              {goalDone}/{goalTotal}
            </span>
          </button>
        )}
        <div className="usage-wrap" ref={usageRef}>
          <button
            type="button"
            className={`usage-button ${warning ? 'usage-warning' : ''}`}
            onClick={() => setUsageOpen((value) => !value)}
            aria-expanded={usageOpen}
            aria-label={t('chat.contextUsage', {
              used: formatTokens(usage.contextUsed),
              total: formatTokens(usage.contextWindow),
              percent: usage.contextPercent,
            })}
            title={warning ? t('chat.usage.overLimit') : t('chat.context')}
          >
            <Gauge size={14} aria-hidden="true" />
            <span className="usage-label">
              {formatTokens(usage.contextUsed)} / {formatTokens(usage.contextWindow)}
            </span>
            <span className="usage-percent">({usage.contextPercent}%)</span>
          </button>
          {usageOpen && (
            <div className="popover usage-popover">
              <header>
                <strong>{t('chat.contextDetails')}</strong>
                <span>{warning ? t('chat.usage.overLimit') : t('chat.usage.estimated')}</span>
              </header>
              <dl>
                <div><dt>{t('chat.usage.input')}</dt><dd>{usage.inputTokens?.toLocaleString() ?? 0}</dd></div>
                <div><dt>{t('chat.usage.output')}</dt><dd>{usage.outputTokens?.toLocaleString() ?? 0}</dd></div>
                <div><dt>{t('chat.usage.cacheHit')}</dt><dd>{usage.cacheHitTokens?.toLocaleString() ?? 0}</dd></div>
                <div><dt>{t('chat.usage.cacheMiss')}</dt><dd>{usage.cacheMissTokens?.toLocaleString() ?? 0}</dd></div>
                <div><dt>{t('chat.usage.cost')}</dt><dd>{formatCost(usage.cost ?? 0)}</dd></div>
              </dl>
              <div className="context-bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={usage.contextPercent}>
                <span style={{ width: `${Math.min(100, usage.contextPercent)}%` }} />
              </div>
            </div>
          )}
        </div>
        <button
          type="button"
          className="icon-button"
          onClick={() => setTheme(themeSetting === 'dark' ? 'light' : themeSetting === 'light' ? 'system' : 'dark')}
          title={t('app.theme')}
          aria-label={t('app.theme')}
        >
          {resolvedTheme === 'dark' ? <Sun size={17} aria-hidden="true" /> : <Moon size={17} aria-hidden="true" />}
        </button>
        <div className="actions-wrap" ref={actionsRef}>
          <button
            type="button"
            className="icon-button"
            onClick={() => setActionsOpen((value) => !value)}
            aria-expanded={actionsOpen}
            aria-label={t('app.more')}
            title={t('app.more')}
          >
            <MoreHorizontal size={18} aria-hidden="true" />
          </button>
          {actionsOpen && (
            <div className="popover actions-popover" role="menu">
              <button type="button" role="menuitem" onClick={() => { setActionsOpen(false); onOpenShare(); }}>
                <Share2 size={15} aria-hidden="true" /> {t('chat.share')}
              </button>
              <button type="button" role="menuitem" onClick={() => { setActionsOpen(false); onExport('markdown'); }}>
                <Download size={15} aria-hidden="true" /> {t('chat.exportMarkdown')}
              </button>
              <button type="button" role="menuitem" onClick={() => { setActionsOpen(false); onExport('json'); }}>
                <Download size={15} aria-hidden="true" /> {t('chat.exportJson')}
              </button>
              <button type="button" role="menuitem" onClick={() => { setActionsOpen(false); onCompact(); }}>
                <ChevronDown size={15} aria-hidden="true" /> {t('chat.compactNow')}
              </button>
              <button type="button" role="menuitem" onClick={() => { setActionsOpen(false); onOpenGoal(); }}>
                <Target size={15} aria-hidden="true" /> {goal ? t('chat.goal.title') : t('chat.goal.set')}
              </button>
              <hr />
              <button type="button" role="menuitem" onClick={() => { setActionsOpen(false); onOpenSettings(); }}>
                <Settings size={15} aria-hidden="true" /> {t('app.settings')}
              </button>
            </div>
          )}
        </div>
      </div>
      {(streamStatus === 'reconnecting' || streamStatus === 'offline') && !connectionDismissed && (
        <div className="connection-banner" role="status">
          <span>{t('app.connection.retryHint')}</span>
          <button type="button" className="icon-button" onClick={() => setConnectionDismissed(true)} aria-label={t('app.dismiss')}>
            <X size={13} aria-hidden="true" />
          </button>
        </div>
      )}
    </header>
  );
}
