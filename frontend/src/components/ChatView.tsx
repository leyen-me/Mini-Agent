import {
  AlertTriangle,
  ArrowDown,
  ArrowUpRight,
  FolderSearch,
  Play,
  RotateCcw,
  Settings,
  ShieldCheck,
  Target,
  Timer,
  X,
  type LucideIcon,
} from 'lucide-react';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import type {
  AgentInfo,
  ChatMessage,
  Interaction,
  PublicConfig,
  Session,
  Snapshot,
  StreamStatus,
  Workspace,
} from '../api/types';
import { useUi } from '../state/UiContext';
import { IconButton } from '@/components/common/IconButton';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';
import { Header } from './Header';
import { InteractionPanel } from './InteractionPanel';
import { MessageList } from './MessageList';

interface Props {
  session: Session | null;
  snapshot: Snapshot | null;
  config: PublicConfig;
  workspace: Workspace | null;
  agent: AgentInfo | null;
  loading: boolean;
  fatalError: string | null;
  streamStatus: StreamStatus;
  retryNotice: string | null;
  interactions: Interaction[];
  composer: ReactNode;
  mobile: boolean;
  sidebarCollapsed: boolean;
  inspectorOpen: boolean;
  onToggleSidebar: () => void;
  onToggleInspector: () => void;
  onOpenContext: () => void;
  onCycleTheme: () => void;
  onRename: (title: string) => Promise<void>;
  onAnswer: (interactionId: string, answer: Record<string, unknown>) => Promise<void>;
  onResume: () => void;
  onRetry: () => void;
  onOpenSettings: () => void;
  onOpenGoal: () => void;
  onOpenShare: () => void;
  onExport: (format: 'markdown' | 'json') => void;
  onCompact: () => void;
  onEditMessage: (message: ChatMessage) => void;
  onFork: (messageId: string) => void;
  onUndo: (turnId: string) => void;
  onRedo: (turnId: string) => void;
  onSelectSession: (sessionId: string) => void;
  onSuggestion: (prompt: string) => void;
  onDismissRetry: () => void;
  onDismissError: () => void;
  errorDismissed: boolean;
}

const SUGGESTIONS: Array<[string, LucideIcon]> = [
  ['chat.prompt.1', FolderSearch],
  ['chat.prompt.2', ShieldCheck],
  ['chat.prompt.3', Timer],
  ['chat.prompt.4', Target],
];

function EmptyHero({
  config,
  mockModel,
  onSuggestion,
  onOpenSettings,
}: {
  config: PublicConfig;
  mockModel: boolean;
  onSuggestion: (prompt: string) => void;
  onOpenSettings: () => void;
}) {
  const { t } = useUi();
  if (!config.model_configured) {
    return (
      <section className="flex flex-col items-start gap-5 animate-rise">
        <span className="eyebrow">{t('app.name')}</span>
        <h1 className="text-[clamp(28px,4vw,44px)] leading-[1.05] font-semibold tracking-[-0.035em]">
          {t('chat.noModel')}
        </h1>
        <p className="max-w-lg text-[15px] leading-relaxed text-muted-foreground">
          {t('chat.noModelHint')}
        </p>
        <Button size="lg" onClick={onOpenSettings}>
          <Settings /> {t('chat.noModelCta')}
        </Button>
      </section>
    );
  }
  return (
    <section className="flex flex-col gap-8">
      <div className="space-y-4 animate-rise">
        <span className="eyebrow flex items-center gap-2">
          <span className="size-1.5 bg-signal" aria-hidden="true" />
          {t('app.name')} · {t('app.tagline')}
        </span>
        <h1 className="max-w-[16ch] text-[clamp(30px,4.2vw,48px)] leading-[1.04] font-semibold tracking-[-0.04em] text-balance">
          {t('chat.emptyTitle')}
        </h1>
        <p className="max-w-xl text-[15px] leading-relaxed text-muted-foreground">
          {t('chat.emptySubtitle')}
        </p>
      </div>
      <div
        className="corner-ticks animate-rise [animation-delay:80ms]"
        role="group"
        aria-label={t('chat.suggestions')}
      >
        <span className="tick-b" aria-hidden="true" />
        <div className="grid grid-cols-1 gap-px border border-border bg-border sm:grid-cols-2">
          {SUGGESTIONS.map(([key, Icon], index) => (
            <button
              type="button"
              className="prompt-card group relative flex min-h-[96px] flex-col justify-between gap-5 bg-background p-4 sm:p-5 text-left transition-colors hover:bg-muted"
              key={key}
              onClick={() => onSuggestion(t(key))}
            >
              <span className="flex w-full items-center justify-between">
                <span className="font-mono text-[11px] text-subtle tabular-nums">
                  {String(index + 1).padStart(2, '0')}
                </span>
                <ArrowUpRight
                  className="size-4 text-subtle transition-all duration-300 group-hover:translate-x-0.5 group-hover:-translate-y-0.5 group-hover:text-foreground"
                  aria-hidden="true"
                />
              </span>
              <span className="flex items-center gap-3">
                <Icon className="size-4 shrink-0 text-muted-foreground transition-colors group-hover:text-signal" aria-hidden="true" />
                <span className="text-[14px] leading-snug font-medium">{t(key)}</span>
              </span>
            </button>
          ))}
        </div>
      </div>
      {mockModel && (
        <p className="-mt-4 flex items-center gap-2 font-mono text-[11.5px] text-muted-foreground">
          <span className="size-1.5 bg-success" aria-hidden="true" />
          {t('chat.emptyHint')}
        </p>
      )}
    </section>
  );
}

function Banner({
  tone,
  icon,
  children,
  actions,
}: {
  tone: 'error' | 'warning';
  icon: ReactNode;
  children: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div
      className={cn(
        'banner mx-auto mb-6 flex w-full max-w-[760px] items-center gap-3 border px-4 py-3 text-[13px] animate-rise',
        tone === 'error'
          ? 'error-banner border-destructive/30 bg-danger-soft text-destructive'
          : 'border-warning/30 bg-warning-soft text-warning',
      )}
      role={tone === 'error' ? 'alert' : 'status'}
    >
      <span className="shrink-0 [&_svg]:size-4">{icon}</span>
      <span className="min-w-0 flex-1">{children}</span>
      {actions}
    </div>
  );
}

export function ChatView({
  session,
  snapshot,
  config,
  workspace,
  agent,
  loading,
  fatalError,
  streamStatus,
  retryNotice,
  interactions,
  composer,
  mobile,
  sidebarCollapsed,
  inspectorOpen,
  onToggleSidebar,
  onToggleInspector,
  onOpenContext,
  onCycleTheme,
  onRename,
  onAnswer,
  onResume,
  onRetry,
  onOpenSettings,
  onOpenGoal,
  onOpenShare,
  onExport,
  onCompact,
  onEditMessage,
  onFork,
  onUndo,
  onRedo,
  onSelectSession,
  onSuggestion,
  onDismissRetry,
  onDismissError,
  errorDismissed,
}: Props) {
  const { t } = useUi();
  const scrollRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const [stick, setStick] = useState(true);
  const [showJump, setShowJump] = useState(false);
  const messages = snapshot?.messages ?? [];
  const empty = Boolean(snapshot) && messages.length === 0 && interactions.length === 0;
  const lastMessage = messages[messages.length - 1];
  const sessionProviderId = session?.model.split('/')[0];
  const mockModel =
    config.providers.find((provider) => provider.id === sessionProviderId)?.protocol ===
    'mock';
  const contentLength = lastMessage?.content.text.length ?? 0;

  useEffect(() => {
    if (stick) bottomRef.current?.scrollIntoView({ block: 'end' });
  }, [messages.length, contentLength, interactions.length, stick, session?.id]);

  useEffect(() => {
    const element = scrollRef.current;
    if (!element) return;
    element.scrollTop = element.scrollHeight;
    setStick(true);
  }, [session?.id]);

  const onScroll = () => {
    const element = scrollRef.current;
    if (!element) return;
    const distance = element.scrollHeight - element.scrollTop - element.clientHeight;
    const nextStick = distance < 90;
    setStick(nextStick);
    setShowJump(!nextStick);
  };

  if (!session) {
    return (
      <main className="chat-view grid min-w-0 flex-1 place-items-center bg-background">
        {loading ? (
          <div className="flex w-[min(420px,90%)] flex-col gap-3" role="status" aria-live="polite">
            <Skeleton className="h-5 w-full" />
            <Skeleton className="h-3 w-2/3" />
            <Skeleton className="h-3 w-1/3" />
            <span className="sr-only">{t('app.loading')}</span>
          </div>
        ) : (
          <div className="flex max-w-md flex-col items-center gap-4 text-center">
            <AlertTriangle className="size-6 text-destructive" aria-hidden="true" />
            <h2 className="text-lg font-semibold">{fatalError ?? t('app.error')}</h2>
            <Button onClick={onRetry}>
              <RotateCcw /> {t('app.retry')}
            </Button>
          </div>
        )}
      </main>
    );
  }

  return (
    <main
      className={cn(
        'chat-view relative flex min-h-0 min-w-0 flex-1 flex-col bg-background',
        empty && 'is-empty',
      )}
    >
      <Header
        session={snapshot?.session ?? session}
        snapshot={snapshot}
        workspace={workspace}
        agent={agent}
        streamStatus={streamStatus}
        retryNotice={retryNotice}
        onRename={onRename}
        onOpenSettings={onOpenSettings}
        onOpenGoal={onOpenGoal}
        onOpenShare={onOpenShare}
        onExport={onExport}
        onCompact={onCompact}
        onToggleSidebar={onToggleSidebar}
        onCycleTheme={onCycleTheme}
        onOpenContext={onOpenContext}
        onToggleInspector={onToggleInspector}
        inspectorOpen={inspectorOpen}
        mobile={mobile}
        sidebarCollapsed={sidebarCollapsed}
        config={config}
      />

      <div
        ref={scrollRef}
        onScroll={onScroll}
        className={cn(
          'chat-scroll min-h-0 flex-1 overflow-x-hidden overflow-y-auto overscroll-contain px-5 md:px-10',
          empty ? 'flex flex-col py-8' : 'pt-10 pb-4',
        )}
      >
        {loading && !snapshot ? (
          <div className="mx-auto flex max-w-[760px] flex-col gap-3" role="status" aria-live="polite">
            <span className="sr-only">{t('chat.processing')}</span>
            <Skeleton className="h-5 w-full" />
            <Skeleton className="h-3 w-2/3" />
            <Skeleton className="h-3 w-1/3" />
          </div>
        ) : null}

        {fatalError && (
          <Banner
            tone="error"
            icon={<AlertTriangle />}
            actions={
              <Button variant="ghost" size="sm" onClick={onRetry}>
                {t('app.retry')}
              </Button>
            }
          >
            {fatalError}
          </Banner>
        )}

        {snapshot?.session.status === 'paused' && (
          <Banner
            tone="warning"
            icon={<Play />}
            actions={
              <Button variant="outline" size="sm" onClick={onResume}>
                <Play /> {t('chat.resume')}
              </Button>
            }
          >
            {t('chat.pausedHint')}
          </Banner>
        )}

        {snapshot?.session.status === 'error' && !errorDismissed && (
          <Banner
            tone="error"
            icon={<AlertTriangle />}
            actions={
              <>
                <Button variant="outline" size="sm" onClick={onResume}>
                  <Play /> {t('chat.resume')}
                </Button>
                <IconButton size="icon-xs" label={t('app.dismiss')} onClick={onDismissError}>
                  <X />
                </IconButton>
              </>
            }
          >
            <strong className="font-semibold">{t('chat.errorTitle')}</strong>{' '}
            {snapshot.session.lastError ?? t('chat.errorHint')}
          </Banner>
        )}

        {retryNotice && (
          <Banner
            tone="warning"
            icon={<RotateCcw />}
            actions={
              <IconButton size="icon-xs" label={t('app.dismiss')} onClick={onDismissRetry}>
                <X />
              </IconButton>
            }
          >
            {retryNotice}
          </Banner>
        )}

        {empty ? (
          <div className="m-auto flex w-full max-w-[760px] flex-col gap-7">
            <EmptyHero
              config={config}
              mockModel={mockModel}
              onSuggestion={onSuggestion}
              onOpenSettings={onOpenSettings}
            />
            {composer}
          </div>
        ) : (
          <>
            {snapshot && (
              <MessageList
                snapshot={snapshot}
                onEditMessage={onEditMessage}
                onFork={onFork}
                onUndo={onUndo}
                onRedo={onRedo}
                scrollRef={scrollRef}
                onSelectSession={onSelectSession}
              />
            )}
            <div ref={bottomRef} />
          </>
        )}
      </div>

      {showJump && (
        <Button
          variant="outline"
          size="icon-sm"
          className="absolute bottom-44 left-1/2 z-10 -translate-x-1/2 bg-background shadow-md animate-rise"
          onClick={() => {
            bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
            setStick(true);
            setShowJump(false);
          }}
          aria-label={t('chat.jumpToBottom')}
          title={t('chat.jumpToBottom')}
        >
          <ArrowDown />
        </Button>
      )}

      {!empty && (
        <div className="composer-area relative z-[5] flex shrink-0 flex-col items-center gap-3 bg-gradient-to-t from-background from-70% to-transparent px-4 pt-4 pb-4 md:px-10 [@media(max-width:560px)]:max-h-[72dvh] [@media(max-width:560px)]:overflow-y-auto">
          <InteractionPanel interactions={interactions} onAnswer={onAnswer} />
          {composer}
        </div>
      )}
    </main>
  );
}
