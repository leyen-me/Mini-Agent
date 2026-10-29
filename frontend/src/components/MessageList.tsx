import { useVirtualizer } from '@tanstack/react-virtual';
import {
  AlertTriangle,
  Archive,
  ArrowUpLeft,
  ChevronRight,
  GitBranch,
  Info,
} from 'lucide-react';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { ChatMessage, Snapshot } from '../api/types';
import { formatTokens } from '../lib/i18n';
import { useUi } from '../state/UiContext';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { Attachments } from './Attachments';
import { CopyButton } from './CopyButton';
import { Markdown } from './Markdown';
import { TurnView } from './TurnView';

interface Props {
  snapshot: Snapshot;
  onEditMessage: (message: ChatMessage) => void;
  onFork: (messageId: string) => void;
  onUndo?: (turnId: string) => void;
  onRedo?: (turnId: string) => void;
  scrollRef?: React.RefObject<HTMLDivElement | null>;
  onSelectSession?: (sessionId: string) => void;
}

function SystemNotice({ message }: { message: ChatMessage }) {
  const { t } = useUi();
  const isError = /失败|错误|error|failed|重启|recovered/i.test(
    message.content.text,
  );
  const Icon = isError ? AlertTriangle : Info;
  return (
    <div
      className={cn(
        'system-notice my-6 flex items-center gap-3 text-[12px] text-muted-foreground',
        isError && 'notice-warn text-warning',
      )}
      role="note"
    >
      <span className="h-px flex-1 bg-border" aria-hidden="true" />
      <span className="flex max-w-[80%] items-center gap-2 text-center">
        <Icon className="size-3.5 shrink-0" aria-hidden="true" />
        <span>{message.content.text}</span>
      </span>
      <span className="h-px flex-1 bg-border" aria-hidden="true" />
      <span className="sr-only">{t('app.system')}</span>
    </div>
  );
}

function CompactionCard({ snapshot }: { snapshot: Snapshot }) {
  const { t } = useUi();
  if (!snapshot.compaction) return null;
  return (
    <details className="compaction-card group mb-10 border border-dashed border-border-strong bg-background">
      <summary className="flex cursor-pointer list-none items-center gap-3 px-4 py-3 text-[13px] transition-colors hover:bg-accent [&::-webkit-details-marker]:hidden">
        <Archive className="size-4 text-muted-foreground" aria-hidden="true" />
        <span className="font-medium">{t('chat.compaction')}</span>
        <span className="ml-auto font-mono text-[11px] text-muted-foreground tabular-nums">
          {formatTokens(snapshot.compaction.tokensBefore)} →{' '}
          {formatTokens(snapshot.compaction.tokensAfter)}
        </span>
        <ChevronRight
          className="size-3.5 text-subtle transition-transform group-open:rotate-90"
          aria-hidden="true"
        />
      </summary>
      <div className="border-t border-dashed border-border-strong px-4 py-3">
        <div className="flex items-center justify-between text-[12px] text-muted-foreground">
          <span>{t('chat.covered', { n: snapshot.compaction.coveredUntilSeq })}</span>
          <CopyButton text={snapshot.compaction.summary} iconOnly />
        </div>
        <pre className="mt-2 max-h-72 overflow-auto font-mono text-[12px] leading-relaxed whitespace-pre-wrap text-foreground/80">
          {snapshot.compaction.summary}
        </pre>
      </div>
    </details>
  );
}

export function MessageList({
  snapshot,
  onEditMessage,
  onFork,
  onUndo,
  onRedo,
  scrollRef,
  onSelectSession,
}: Props) {
  const { t } = useUi();
  const turns = new Map(snapshot.turns.map((turn) => [turn.id, turn]));
  const messagesByTurn = new Map<string, ChatMessage[]>();
  for (const message of snapshot.messages) {
    if (
      !message.turnId ||
      (message.role !== 'user' && message.role !== 'assistant')
    )
      continue;
    const list = messagesByTurn.get(message.turnId) ?? [];
    list.push(message);
    messagesByTurn.set(message.turnId, list);
  }
  const renderedTurns = new Set<string>();
  const blocks: React.ReactNode[] = [];

  for (const message of snapshot.messages) {
    if (message.role === 'system' && !message.turnId) {
      blocks.push(<SystemNotice key={message.id} message={message} />);
      continue;
    }
    if (
      message.turnId &&
      turns.has(message.turnId) &&
      !renderedTurns.has(message.turnId)
    ) {
      const turn = turns.get(message.turnId)!;
      const turnMessages = messagesByTurn.get(message.turnId) ?? [];
      if (turnMessages.length) {
        renderedTurns.add(message.turnId);
        blocks.push(
          <TurnView
            key={turn.id}
            turn={turn}
            messages={turnMessages}
            toolCalls={snapshot.toolCalls.filter(
              (call) => call.turnId === turn.id,
            )}
            onEditMessage={onEditMessage}
            onFork={onFork}
            onUndo={onUndo}
            onRedo={onRedo}
          />,
        );
      }
      continue;
    }
    if (!message.turnId) {
      if (message.role === 'user') {
        blocks.push(
          <div className="user-row mb-10 flex justify-end" key={message.id}>
            <div className="user-content max-w-[min(85%,620px)] border border-border bg-muted px-4 py-3 text-[15px] leading-relaxed whitespace-pre-wrap">
              {message.content.text}
              <Attachments items={message.content.attachments} />
            </div>
          </div>,
        );
      } else if (message.role === 'assistant' && message.content.text) {
        blocks.push(
          <div className="assistant-panel mb-10 pl-8" key={message.id}>
            <Markdown>{message.content.text}</Markdown>
          </div>,
        );
      }
    }
  }
  for (const turn of snapshot.turns) {
    if (renderedTurns.has(turn.id)) continue;
    const turnMessages = messagesByTurn.get(turn.id) ?? [];
    if (!turnMessages.length) continue;
    blocks.push(
      <TurnView
        key={turn.id}
        turn={turn}
        messages={turnMessages}
        toolCalls={snapshot.toolCalls.filter((call) => call.turnId === turn.id)}
        onEditMessage={onEditMessage}
        onFork={onFork}
        onUndo={onUndo}
        onRedo={onRedo}
      />,
    );
  }

  const allBlocks = snapshot.compaction
    ? [<CompactionCard key="compaction" snapshot={snapshot} />, ...blocks]
    : blocks;
  const relationBar =
    snapshot.childSessions?.length || snapshot.session.parentSessionId ? (
      <div className="subagent-bar mb-8 flex flex-wrap gap-2">
        {snapshot.session.parentSessionId && (
          <Button
            variant="outline"
            size="xs"
            onClick={() => onSelectSession?.(snapshot.session.parentSessionId!)}
          >
            <ArrowUpLeft />
            {t('chat.backToParent')}
          </Button>
        )}
        {snapshot.childSessions?.map((child) => (
          <Button
            variant="outline"
            size="xs"
            key={child.id}
            onClick={() => onSelectSession?.(child.id)}
          >
            <GitBranch />
            {child.title || child.id.slice(-6)}
            <span className="font-mono text-[10.5px] text-muted-foreground">
              {t(`status.${child.status}`)}
            </span>
          </Button>
        ))}
      </div>
    ) : null;
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [scrollMargin, setScrollMargin] = useState(0);
  const [scrollReady, setScrollReady] = useState(false);
  useEffect(() => {
    setScrollReady(Boolean(scrollRef?.current));
  }, [scrollRef, allBlocks.length]);
  const virtualize = allBlocks.length > 40 && scrollReady;

  const virtualizer = useVirtualizer({
    count: allBlocks.length,
    getScrollElement: () => scrollRef?.current ?? null,
    estimateSize: () => 320,
    overscan: 6,
    scrollMargin,
  });

  useLayoutEffect(() => {
    if (!virtualize) return;
    const element = containerRef.current;
    const scroll = scrollRef?.current;
    if (!element || !scroll) return;
    const offset =
      element.getBoundingClientRect().top -
      scroll.getBoundingClientRect().top +
      scroll.scrollTop;
    setScrollMargin(offset);
  }, [virtualize, allBlocks.length, snapshot.session.id, scrollRef]);

  if (virtualize) {
    return (
      <div className="message-list message-list-virtual-wrap mx-auto w-full max-w-[760px]">
        {relationBar}
        <div
          className="message-list-virtual"
          ref={containerRef}
          style={{
            height: `${virtualizer.getTotalSize()}px`,
            position: 'relative',
          }}
        >
          {virtualizer.getVirtualItems().map((item) => (
            <div
              key={item.key}
              data-index={item.index}
              ref={virtualizer.measureElement}
              className="virtual-row"
              style={{
                position: 'absolute',
                top: 0,
                left: 0,
                width: '100%',
                transform: `translateY(${item.start - scrollMargin}px)`,
              }}
            >
              {allBlocks[item.index]}
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="message-list mx-auto w-full max-w-[760px]" ref={containerRef}>
      {relationBar}
      {allBlocks}
      {snapshot.messages.length === 0 && (
        <p className="text-muted-foreground">{t('app.noSessionsHint')}</p>
      )}
    </div>
  );
}
