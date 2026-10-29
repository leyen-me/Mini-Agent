import {
  Download,
  FileJson,
  Menu,
  Moon,
  MoreHorizontal,
  PanelLeftOpen,
  PanelRight,
  Settings,
  Share2,
  Shrink,
  Sun,
  Target,
  Wallet,
  X,
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import type {
  AgentInfo,
  BalanceInfo,
  PublicConfig,
  Session,
  Snapshot,
  StreamStatus,
  Workspace,
} from '../api/types';
import { formatTokens } from '../lib/i18n';
import { useUi } from '../state/UiContext';
import { IconButton } from '@/components/common/IconButton';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { cn } from '@/lib/utils';

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
  onCycleTheme: () => void;
  onOpenContext: () => void;
  onToggleInspector: () => void;
  inspectorOpen: boolean;
  mobile: boolean;
  sidebarCollapsed: boolean;
  config?: PublicConfig;
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
  onCycleTheme,
  onOpenContext,
  onToggleInspector,
  inspectorOpen,
  mobile,
  sidebarCollapsed,
  config,
}: Props) {
  const { t, resolvedTheme } = useUi();
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(session.title);
  const [connectionDismissed, setConnectionDismissed] = useState(false);
  const [balance, setBalance] = useState<BalanceInfo | null>(null);
  const [balanceLoading, setBalanceLoading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setTitle(session.title);
  }, [session.title]);

  useEffect(() => {
    if (streamStatus === 'connected') setConnectionDismissed(false);
  }, [streamStatus]);

  const providerId = session.model.split('/')[0];
  const provider = config?.providers.find((item) => item.id === providerId);
  const canShowBalance = Boolean(provider?.supports_balance);

  const refreshBalance = async () => {
    if (!provider || balanceLoading) return;
    setBalanceLoading(true);
    try {
      setBalance(await api.providers.balance(provider.id));
    } catch {
      setBalance(null);
    } finally {
      setBalanceLoading(false);
    }
  };

  useEffect(() => {
    if (!canShowBalance) {
      setBalance(null);
      return;
    }
    void refreshBalance();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canShowBalance, providerId]);

  useEffect(() => {
    if (editing) inputRef.current?.select();
  }, [editing]);

  const saveTitle = async () => {
    const next = title.trim();
    setEditing(false);
    if (next && next !== session.title) await onRename(next);
    else setTitle(session.title);
  };

  const usage = snapshot?.usage ?? {
    contextUsed: 0,
    contextWindow: 128000,
    contextPercent: 0,
    model: session.model,
  };
  const goal = snapshot?.goal;
  const goalDone = goal?.tasks.filter((task) => task.status === 'completed').length ?? 0;
  const goalTotal = goal?.tasks.length ?? 0;
  const warning = usage.contextPercent >= 80;
  const runningTasks =
    snapshot?.tasks.filter((task) => task.status === 'running' || task.status === 'queued') ??
    [];
  const connected = streamStatus === 'connected';

  return (
    <header className="chat-header relative z-10 flex h-14 shrink-0 items-center gap-3 border-b border-border bg-background/85 px-4 backdrop-blur-md md:px-5">
      {(mobile || sidebarCollapsed) && (
        <IconButton label={t('app.sessions')} className="-ml-1.5" onClick={onToggleSidebar}>
          {mobile ? <Menu /> : <PanelLeftOpen />}
        </IconButton>
      )}

      <div className="flex min-w-0 flex-1 items-center gap-3">
        {editing ? (
          <input
            ref={inputRef}
            className="title-input h-8 min-w-[240px] border border-foreground bg-background px-2 text-[14px] font-medium outline-none"
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
          <button
            type="button"
            onClick={() => setEditing(true)}
            title={t('app.edit')}
            className="max-w-[min(26rem,55%)] min-w-[5rem] shrink-0 truncate text-left text-[14px] font-medium tracking-tight decoration-border-strong underline-offset-4 hover:underline"
          >
            {session.title}
          </button>
        )}
        <div className="hidden min-w-0 shrink items-center gap-2 overflow-hidden font-mono text-[11.5px] whitespace-nowrap text-muted-foreground md:flex">
          <span className="text-subtle">/</span>
          <span className="max-w-40 truncate" title={workspace?.rootPath}>
            {workspace?.name ?? '—'}
          </span>
          <span className="text-subtle">/</span>
          <span className="max-w-32 truncate">{agent?.name ?? session.agentId}</span>
        </div>
        <span
          className={cn(
            'shrink-0 border px-1.5 font-mono text-[10px] leading-[18px] font-medium tracking-wider uppercase',
            session.mode === 'plan'
              ? 'border-warning/40 bg-warning-soft text-warning'
              : 'border-border text-muted-foreground',
          )}
        >
          {session.mode === 'plan' ? t('chat.mode.plan') : t('chat.mode.build')}
        </span>
        {runningTasks.length > 0 && (
          <span className="hidden shrink-0 items-center gap-1.5 font-mono text-[11px] text-signal sm:inline-flex">
            <span className="size-1.5 animate-pulse bg-signal" aria-hidden="true" />
            {t('chat.tasks')} {runningTasks.length}
          </span>
        )}
      </div>

      <div className="flex shrink-0 items-center gap-1">
        {retryNotice && (
          <span
            className="hidden border border-warning/30 bg-warning-soft px-2 py-0.5 text-[11px] text-warning sm:inline"
            title={retryNotice}
          >
            {t('chat.retryBadge')}
          </span>
        )}
        <span
          className={cn(
            'connection-chip inline-flex h-7 items-center gap-2 px-2 text-[11.5px]',
            `connection-${streamStatus}`,
            !connected && 'text-warning',
            streamStatus === 'offline' && 'text-destructive',
          )}
          title={t(`app.connection.${streamStatus}`)}
        >
          <span
            className={cn('size-1.5', connected ? 'bg-success' : 'animate-pulse bg-current')}
            aria-hidden="true"
          />
          <span className={cn(connected ? 'sr-only' : 'hidden sm:inline')}>
            {t(`app.connection.${streamStatus}`)}
          </span>
        </span>

        {goal && (
          <button
            type="button"
            className="goal-chip inline-flex h-7 max-w-60 items-center gap-2 border border-border px-2 text-[12px] transition-colors hover:border-foreground"
            onClick={onOpenGoal}
            title={goal.objective}
          >
            <Target className="size-3.5 shrink-0 text-signal" aria-hidden="true" />
            <span className="hidden max-w-36 truncate lg:inline">{goal.objective}</span>
            <span className="font-mono text-[11px] font-medium tabular-nums">
              {goalDone}/{goalTotal}
            </span>
          </button>
        )}

        {canShowBalance && (
          <Button
            variant="ghost"
            size="xs"
            className="hidden font-mono text-muted-foreground sm:inline-flex"
            title={t('header.refreshBalance')}
            onClick={() => void refreshBalance()}
          >
            <Wallet />
            {balanceLoading
              ? '…'
              : balance
                ? `${balance.total.toFixed(2)} ${balance.currency}`
                : t('header.balance')}
          </Button>
        )}

        <button
          type="button"
          onClick={onOpenContext}
          aria-label={t('chat.contextUsage', {
            used: formatTokens(usage.contextUsed),
            total: formatTokens(usage.contextWindow),
            percent: usage.contextPercent,
          })}
          title={warning ? t('chat.usage.overLimit') : t('chat.contextDetails')}
          className={cn(
            'usage-button inline-flex h-7 items-center gap-2 px-2 font-mono text-[11.5px] text-muted-foreground tabular-nums transition-colors hover:bg-accent hover:text-foreground',
            warning && 'text-warning',
          )}
        >
          <ContextRing percent={usage.contextPercent} />
          <span className="hidden lg:inline">
            {formatTokens(usage.contextUsed)} / {formatTokens(usage.contextWindow)}
          </span>
        </button>

        <span className="mx-1 hidden h-4 w-px bg-border sm:block" aria-hidden="true" />

        <IconButton label={t('app.theme')} onClick={onCycleTheme}>
          {resolvedTheme === 'dark' ? <Sun /> : <Moon />}
        </IconButton>
        <IconButton
          label={t('inspector.toggle')}
          onClick={onToggleInspector}
          aria-pressed={inspectorOpen}
          className={cn(inspectorOpen && 'bg-accent text-foreground')}
        >
          <PanelRight />
        </IconButton>
        {/* Non-modal: a modal menu that opens a Dialog leaves pointer-events:none on <body>. */}
        <DropdownMenu modal={false}>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={t('app.more')}
              className="text-muted-foreground hover:text-foreground"
            >
              <MoreHorizontal />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuItem onSelect={onOpenShare}>
              <Share2 /> {t('chat.share')}
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => onExport('markdown')}>
              <Download /> {t('chat.exportMarkdown')}
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => onExport('json')}>
              <FileJson /> {t('chat.exportJson')}
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={onCompact}>
              <Shrink /> {t('chat.compactNow')}
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={onOpenGoal}>
              <Target /> {goal ? t('chat.goal.title') : t('chat.goal.set')}
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={onOpenSettings}>
              <Settings /> {t('app.settings')}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {(streamStatus === 'reconnecting' || streamStatus === 'offline') &&
        !connectionDismissed && (
          <div
            className="connection-banner absolute inset-x-0 top-full flex items-center gap-2 border-b border-warning/25 bg-warning-soft px-5 py-1.5 text-[12px] text-warning animate-in fade-in-0"
            role="status"
          >
            <span className="flex-1">{t('app.connection.retryHint')}</span>
            <IconButton
              size="icon-xs"
              label={t('app.dismiss')}
              className="text-warning hover:text-warning"
              onClick={() => setConnectionDismissed(true)}
            >
              <X />
            </IconButton>
          </div>
        )}
    </header>
  );
}

function ContextRing({ percent }: { percent: number }) {
  const radius = 6;
  const circumference = 2 * Math.PI * radius;
  const filled = (Math.min(100, Math.max(0, percent)) / 100) * circumference;
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" className="shrink-0">
      <circle
        cx="8"
        cy="8"
        r={radius}
        fill="none"
        stroke="currentColor"
        strokeOpacity="0.2"
        strokeWidth="2"
      />
      <circle
        cx="8"
        cy="8"
        r={radius}
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeDasharray={`${filled} ${circumference}`}
        transform="rotate(-90 8 8)"
      />
    </svg>
  );
}
