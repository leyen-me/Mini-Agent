import {
  AlertTriangle,
  ArrowDown,
  MessageSquare,
  Play,
  RotateCcw,
  Settings,
  Sparkles,
  Terminal,
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
import { Header } from './Header';
import { InteractionPanel } from './InteractionPanel';
import { MessageList } from './MessageList';
import { SessionStatusBadge } from './StatusBadge';

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
  onToggleSidebar: () => void;
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
  onSuggestion: (prompt: string) => void;
  onDismissRetry: () => void;
  onDismissError: () => void;
  errorDismissed: boolean;
}

function EmptyHero({ config, onSuggestion, onOpenSettings }: { config: PublicConfig; onSuggestion: (prompt: string) => void; onOpenSettings: () => void }) {
  const { t } = useUi();
  const prompts = ['chat.prompt.1', 'chat.prompt.2', 'chat.prompt.3', 'chat.prompt.4'];
  if (!config.model_configured) {
    return (
      <section className="empty-hero">
        <div className="hero-mark"><Sparkles size={30} aria-hidden="true" /></div>
        <h1>{t('chat.noModel')}</h1>
        <p>{t('chat.noModelHint')}</p>
        <button type="button" className="primary-button" onClick={onOpenSettings}>
          <Settings size={15} aria-hidden="true" /> {t('chat.noModelCta')}
        </button>
      </section>
    );
  }
  return (
    <section className="empty-hero">
      <div className="hero-mark"><Terminal size={30} aria-hidden="true" /></div>
      <h1>{t('chat.emptyTitle')}</h1>
      <p>{t('chat.emptySubtitle')}</p>
      <div className="prompt-grid">
        {prompts.map((key) => (
          <button type="button" className="prompt-card" key={key} onClick={() => onSuggestion(t(key))}>
            <MessageSquare size={15} aria-hidden="true" />
            <span>{t(key)}</span>
          </button>
        ))}
      </div>
      <p className="hero-hint">{t('chat.emptyHint')}</p>
    </section>
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
  onToggleSidebar,
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
      <main className="chat-view no-session">
        {loading ? (
          <div className="loading-screen" role="status" aria-live="polite">
            <div className="skeleton wide" />
            <div className="skeleton" />
            <div className="skeleton short" />
            <span className="sr-only">{t('app.loading')}</span>
          </div>
        ) : (
          <div className="centered-error">
            <AlertTriangle size={28} aria-hidden="true" />
            <h2>{fatalError ?? t('app.error')}</h2>
            <button type="button" className="primary-button" onClick={onRetry}>
              <RotateCcw size={15} aria-hidden="true" /> {t('app.retry')}
            </button>
          </div>
        )}
      </main>
    );
  }

  return (
    <main className={`chat-view ${empty ? 'is-empty' : ''}`}>
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
        mobile={mobile}
        sidebarCollapsed={sidebarCollapsed}
      />

      <div className={`chat-scroll ${empty ? 'empty-scroll' : ''}`} ref={scrollRef} onScroll={onScroll}>
        {loading && !snapshot ? (
          <div className="loading-screen" role="status" aria-live="polite">
            <span className="sr-only">{t('chat.processing')}</span>
            <div className="skeleton wide" />
            <div className="skeleton" />
            <div className="skeleton short" />
          </div>
        ) : null}

        {fatalError && (
          <div className="banner error-banner" role="alert">
            <AlertTriangle size={15} aria-hidden="true" />
            <span>{fatalError}</span>
            <button type="button" className="text-button" onClick={onRetry}>
              {t('app.retry')}
            </button>
          </div>
        )}

        {snapshot?.session.status === 'paused' && (
          <div className="banner paused-banner" role="status">
            <SessionStatusBadge status="paused" />
            <span>{t('chat.pausedHint')}</span>
            <button type="button" className="secondary-button" onClick={onResume}>
              <Play size={14} aria-hidden="true" /> {t('chat.resume')}
            </button>
          </div>
        )}

        {snapshot?.session.status === 'error' && !errorDismissed && (
          <div className="banner error-banner" role="alert">
            <AlertTriangle size={15} aria-hidden="true" />
            <span>
              <strong>{t('chat.errorTitle')}</strong> {snapshot.session.lastError ?? t('chat.errorHint')}
            </span>
            <button type="button" className="secondary-button" onClick={onResume}>
              <Play size={14} aria-hidden="true" /> {t('chat.resume')}
            </button>
            <button type="button" className="icon-button" onClick={onDismissError} aria-label={t('app.dismiss')}>
              ×
            </button>
          </div>
        )}

        {retryNotice && (
          <div className="banner retry-banner" role="status">
            <RefreshIcon />
            <span>{retryNotice}</span>
            <button type="button" className="icon-button" onClick={onDismissRetry} aria-label={t('app.dismiss')}>
              ×
            </button>
          </div>
        )}

        {empty ? (
          <div className="empty-center">
            <EmptyHero config={config} onSuggestion={onSuggestion} onOpenSettings={onOpenSettings} />
            {composer}
          </div>
        ) : (
          <>
            {snapshot && (
              <MessageList
                snapshot={snapshot}
                onEditMessage={onEditMessage}
                onFork={onFork}
              />
            )}
            <div ref={bottomRef} />
          </>
        )}
      </div>

      {showJump && (
        <button
          type="button"
          className="jump-bottom"
          onClick={() => {
            bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
            setStick(true);
            setShowJump(false);
          }}
          aria-label={t('chat.jumpToBottom')}
          title={t('chat.jumpToBottom')}
        >
          <ArrowDown size={16} aria-hidden="true" />
        </button>
      )}

      {!empty && (
        <div className="composer-area">
          <InteractionPanel interactions={interactions} onAnswer={onAnswer} />
          {composer}
        </div>
      )}
    </main>
  );
}

function RefreshIcon() {
  return <RotateCcw size={15} aria-hidden="true" />;
}
