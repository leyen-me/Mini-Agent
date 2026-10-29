import {
  Archive,
  Bot,
  Folder,
  GitBranch,
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Pin,
  PinOff,
  Plug,
  Plus,
  Search,
  Settings,
  Settings2,
  Sparkles,
  Trash2,
} from 'lucide-react';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import type { Session, Workspace } from '../api/types';
import { relativeTime } from '../lib/i18n';
import { useUi } from '../state/UiContext';
import { IconButton } from '@/components/common/IconButton';
import { SimpleSelect } from '@/components/common/SimpleSelect';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Kbd } from '@/components/ui/kbd';
import { cn } from '@/lib/utils';
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
  onOpenAdmin: () => void;
  onDeleteSession: (session: Session) => void;
  onOpenPalette: () => void;
  onSearch: (query: string) => void | Promise<void>;
  onWorkspaceChange: (id: string) => void;
  onPin: (session: Session, pinned: boolean) => void;
  onArchive: (session: Session) => void;
  onToggleCollapse: () => void;
  onWidthChange: (width: number) => void;
}

type Filter = 'all' | 'running' | 'waiting_user' | 'error';
const FILTERS: Filter[] = ['all', 'running', 'waiting_user', 'error'];
const MIN_WIDTH = 240;
const MAX_WIDTH = 420;

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
  onOpenAdmin,
  onDeleteSession,
  onOpenPalette,
  onSearch,
  onWorkspaceChange,
  onPin,
  onArchive,
  onToggleCollapse,
  onWidthChange,
}: Props) {
  const { t, language } = useUi();
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
      onWidthChange(Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, event.clientX)));
    };
    const onUp = () => {
      dragging.current = false;
      document.body.classList.remove('select-none', 'cursor-col-resize');
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
    if (filter === 'running')
      return session.status === 'running' || session.status === 'compacting';
    return session.status === filter;
  });

  const navItems: Array<[string, ReactNode, () => void]> = [
    [t('app.skills'), <Sparkles key="s" />, onOpenSkills],
    [t('app.mcp'), <Plug key="m" />, onOpenMcp],
    [t('app.agents'), <Bot key="a" />, onOpenAgents],
    [t('app.admin'), <Settings2 key="d" />, onOpenAdmin],
  ];

  return (
    <aside
      className={cn(
        'sidebar relative flex h-full min-h-0 flex-col border-r border-border bg-sidebar transition-[width] duration-200 ease-out',
        mobile && 'w-[min(86vw,320px)]',
      )}
      style={!mobile ? { width: collapsed ? 56 : width } : undefined}
      aria-label={t('app.sessions')}
    >
      {/* Brand row */}
      <div
        className={cn(
          'flex h-14 shrink-0 items-center gap-2.5 border-b border-border',
          collapsed ? 'justify-center px-0' : 'px-4',
        )}
      >
        <button
          type="button"
          onClick={onNewSession}
          aria-label={t('app.newSession')}
          className="brand-mark grid size-6 shrink-0 place-items-center bg-foreground font-mono text-[12px] font-semibold text-background transition-transform hover:rotate-90"
        >
          <span aria-hidden="true">M</span>
        </button>
        {!collapsed && (
          <>
            <div className="min-w-0 flex-1 truncate text-[14px] font-semibold tracking-tight">
              {t('app.name')}
            </div>
            <IconButton
              label={t('app.commandPalette')}
              shortcut={<Kbd>⌘K</Kbd>}
              onClick={onOpenPalette}
            >
              <Search />
            </IconButton>
            {!mobile && (
              <IconButton
                label={t('app.sidebar.collapse')}
                onClick={onToggleCollapse}
              >
                <PanelLeftClose />
              </IconButton>
            )}
          </>
        )}
      </div>

      {collapsed ? (
        <div className="flex flex-col items-center gap-1 py-3">
          <IconButton
            label={t('app.sessions')}
            side="right"
            onClick={onToggleCollapse}
          >
            <PanelLeftOpen />
          </IconButton>
          <IconButton
            label={t('app.newSession')}
            side="right"
            variant="default"
            className="new-session-button text-primary-foreground hover:text-primary-foreground"
            onClick={onNewSession}
          >
            <Plus />
          </IconButton>
          <IconButton
            label={t('app.commandPalette')}
            side="right"
            onClick={onOpenPalette}
          >
            <Search />
          </IconButton>
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="space-y-2 p-3">
            <Button
              className="new-session-button h-9 w-full justify-between px-3"
              onClick={onNewSession}
            >
              <span className="flex items-center gap-2">
                <Plus />
                {t('app.newSession')}
              </span>
              <Kbd className="border-primary-foreground/20 bg-transparent text-primary-foreground/70 [@media(hover:none)]:hidden">
                ⌘N
              </Kbd>
            </Button>
            <div className="workspace-select">
              <SimpleSelect
                value={activeWorkspaceId ?? ''}
                ariaLabel={t('app.workspace')}
                onValueChange={onWorkspaceChange}
                icon={<Folder className="text-muted-foreground" />}
                className="h-9 border-transparent bg-transparent px-2.5 text-[13px] hover:bg-accent dark:bg-transparent"
                options={workspaces.map((workspace) => ({
                  value: workspace.id,
                  label: workspace.alias || workspace.name,
                }))}
              />
            </div>
          </div>

          <div className="space-y-2 px-3 pb-2">
            <div className="relative">
              <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={t('app.searchSessions')}
                aria-label={t('app.searchSessions')}
                className="h-8 bg-background pl-8 text-[13px]"
              />
            </div>
            <div
              className="flex items-center gap-3 px-0.5"
              role="group"
              aria-label={t('app.sessions')}
            >
              {FILTERS.map((value) => (
                <button
                  type="button"
                  key={value}
                  aria-pressed={filter === value}
                  onClick={() => setFilter(value)}
                  className={cn(
                    'relative py-1 text-[12px] text-muted-foreground transition-colors hover:text-foreground',
                    filter === value &&
                      'font-medium text-foreground after:absolute after:inset-x-0 after:-bottom-px after:h-px after:bg-foreground',
                  )}
                >
                  {value === 'all' ? t('app.all') : t(`status.${value}`)}
                </button>
              ))}
            </div>
          </div>

          <div className="flex items-center justify-between px-4 pt-3 pb-1.5">
            <span className="eyebrow">{t('app.sessions')}</span>
            <span className="font-mono text-[10.5px] text-subtle tabular-nums">
              {String(visible.length).padStart(2, '0')}
            </span>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3" role="list">
            {visible.length === 0 && (
              <div className="flex flex-col items-center gap-2 px-4 py-12 text-center">
                <MessageSquare className="size-5 text-subtle" />
                <p className="text-[13px] font-medium">{t('app.noSessions')}</p>
                <p className="text-xs text-muted-foreground">
                  {t('app.noSessionsHint')}
                </p>
              </div>
            )}
            {visible.map((session) => {
              const active = selectedId === session.id;
              return (
                <div
                  key={session.id}
                  role="listitem"
                  className={cn('session-item group relative', active && 'active')}
                >
                  <button
                    type="button"
                    onClick={() => onSelect(session.id)}
                    aria-current={active ? 'page' : undefined}
                    className={cn(
                      'flex w-full flex-col gap-1 px-3 py-2.5 text-left transition-colors hover:bg-accent',
                      active &&
                        'bg-background shadow-[inset_2px_0_0_var(--foreground),0_0_0_1px_var(--border)] hover:bg-background',
                    )}
                  >
                    <span className="flex min-w-0 items-center gap-1.5">
                      {session.pinned && (
                        <Pin
                          className="size-3 shrink-0 text-muted-foreground"
                          aria-label={t('app.pinned')}
                        />
                      )}
                      {session.parentSessionId && (
                        <GitBranch
                          className="size-3 shrink-0 text-muted-foreground"
                          aria-label={t('app.subagent')}
                        />
                      )}
                      <span
                        className={cn(
                          'min-w-0 flex-1 truncate text-[13px] text-foreground/80',
                          active && 'font-medium text-foreground',
                        )}
                      >
                        {session.title || t('app.newSession')}
                      </span>
                      <span className="shrink-0 font-mono text-[10.5px] text-subtle tabular-nums group-focus-within:opacity-0 group-hover:opacity-0">
                        {relativeTime(session.updatedAt, language)}
                      </span>
                    </span>
                    {session.status !== 'idle' && (
                      <SessionStatusBadge status={session.status} compact />
                    )}
                  </button>
                  <div className="pointer-events-none absolute top-1.5 right-1.5 flex items-center opacity-0 transition-opacity group-focus-within:pointer-events-auto group-focus-within:opacity-100 group-hover:pointer-events-auto group-hover:opacity-100">
                    <IconButton
                      size="icon-xs"
                      label={session.pinned ? t('app.unpin') : t('app.pin')}
                      onClick={() => onPin(session, !session.pinned)}
                    >
                      {session.pinned ? <PinOff /> : <Pin />}
                    </IconButton>
                    <IconButton
                      size="icon-xs"
                      label={t('app.archive')}
                      onClick={() => onArchive(session)}
                    >
                      <Archive />
                    </IconButton>
                    <IconButton
                      size="icon-xs"
                      label={t('app.delete')}
                      className="hover:bg-danger-soft hover:text-destructive"
                      onClick={() => onDeleteSession(session)}
                    >
                      <Trash2 />
                    </IconButton>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      <nav
        className={cn(
          'mt-auto shrink-0 border-t border-border p-2',
          collapsed && 'flex flex-col items-center gap-1',
        )}
        aria-label={t('app.extensions')}
      >
        {navItems.map(([label, icon, onClick]) =>
          collapsed ? (
            <IconButton key={label} label={label} side="right" onClick={onClick}>
              {icon}
            </IconButton>
          ) : (
            <NavButton key={label} icon={icon} label={label} onClick={onClick} />
          ),
        )}
        {collapsed ? (
          <IconButton label={t('app.settings')} side="right" onClick={onOpenSettings}>
            <Settings />
          </IconButton>
        ) : (
          <div className="mt-1 flex items-center border-t border-border pt-1">
            <NavButton
              icon={<Settings />}
              label={t('app.settings')}
              onClick={onOpenSettings}
            />
            <span className="px-2 font-mono text-[10.5px] text-subtle">v0.2</span>
          </div>
        )}
      </nav>

      {!mobile && !collapsed && (
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label={t('app.sidebar.resize')}
          className="absolute top-0 -right-[3px] z-10 h-full w-1.5 cursor-col-resize transition-colors hover:bg-foreground/15"
          onPointerDown={() => {
            dragging.current = true;
            document.body.classList.add('select-none', 'cursor-col-resize');
          }}
        />
      )}
    </aside>
  );
}

function NavButton({
  icon,
  label,
  onClick,
}: {
  icon: ReactNode;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex h-8 w-full min-w-0 items-center gap-2.5 px-2.5 text-[13px] text-foreground/75 transition-colors hover:bg-accent hover:text-foreground [&_svg]:size-4 [&_svg]:shrink-0 [&_svg]:text-muted-foreground hover:[&_svg]:text-foreground"
    >
      {icon}
      <span className="truncate">{label}</span>
    </button>
  );
}
