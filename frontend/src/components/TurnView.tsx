import {
  Check,
  ChevronRight,
  GitFork,
  Pencil,
  Redo2,
  Undo2,
} from 'lucide-react';
import { useMemo, useState } from 'react';
import type { ChatMessage, ToolCall, Turn } from '../api/types';
import { useUi } from '../state/UiContext';
import { IconButton } from '@/components/common/IconButton';
import { cn } from '@/lib/utils';
import { Attachments } from './Attachments';
import { CopyButton } from './CopyButton';
import { Markdown } from './Markdown';
import { ReasoningBlock } from './ReasoningBlock';
import { TurnStatusBadge } from './StatusBadge';
import { ToolCallCard } from './ToolCallCard';

interface Props {
  turn: Turn;
  messages: ChatMessage[];
  toolCalls: ToolCall[];
  onEditMessage: (message: ChatMessage) => void;
  onFork: (messageId: string) => void;
  onUndo?: (turnId: string) => void;
  onRedo?: (turnId: string) => void;
}

export function TurnView({
  turn,
  messages,
  toolCalls,
  onEditMessage,
  onFork,
  onUndo,
  onRedo,
}: Props) {
  const { t } = useUi();
  const [manualExpanded, setManualExpanded] = useState<boolean | null>(null);

  const userMessages = messages.filter((message) => message.role === 'user');
  const assistantMessages = messages.filter(
    (message) => message.role === 'assistant',
  );
  const running =
    turn.status === 'running' ||
    assistantMessages.some((message) => message.status === 'streaming');
  const expanded = manualExpanded ?? running;
  const finalMessage = useMemo(() => {
    for (let index = assistantMessages.length - 1; index >= 0; index -= 1) {
      const message = assistantMessages[index];
      if (
        message.status === 'complete' &&
        message.content.text.trim() &&
        message.content.toolCalls.length === 0
      ) {
        return message;
      }
    }
    return assistantMessages[assistantMessages.length - 1] ?? null;
  }, [assistantMessages]);
  const turnFinished = ['completed', 'failed', 'paused', 'cancelled'].includes(
    turn.status,
  );
  const steps = assistantMessages.filter(
    (message) =>
      message.content.text.trim() || message.content.toolCalls.length,
  ).length;
  const fileChanged = toolCalls.some((call) => {
    const data = (call.output?.data ?? null) as Record<string, unknown> | null;
    return Boolean(
      typeof data?.checkpointId === 'string' ||
      (typeof data?.diff === 'string' && data.diff.length > 0),
    );
  });

  const assistantActions = (message: ChatMessage) => (
    <div className="assistant-actions -ml-1.5 flex items-center opacity-0 transition-opacity group-hover/turn:opacity-100 focus-within:opacity-100">
      <IconButton size="icon-xs" label={t('chat.fork')} onClick={() => onFork(message.id)}>
        <GitFork />
      </IconButton>
      <CopyButton text={message.content.text} iconOnly />
    </div>
  );

  return (
    <article
      className={cn('turn group/turn flex flex-col gap-5 pb-10 [content-visibility:auto]', running && 'turn-running')}
      aria-label={`Turn ${turn.seq}`}
    >
      {userMessages.map((message) => (
        <div className="user-row group/user flex flex-col items-end gap-1" key={message.id}>
          <div className="max-w-[min(85%,620px)] border border-border bg-muted px-4 py-3">
            <span className="sr-only">{t('chat.you')}</span>
            <div className="user-content text-[15px] leading-relaxed break-words whitespace-pre-wrap">
              {message.content.text}
            </div>
            <Attachments items={message.content.attachments} />
          </div>
          <div className="flex items-center opacity-0 transition-opacity group-hover/user:opacity-100 focus-within:opacity-100">
            <IconButton size="icon-xs" label={t('app.edit')} onClick={() => onEditMessage(message)}>
              <Pencil />
            </IconButton>
            <CopyButton text={message.content.text} iconOnly />
          </div>
        </div>
      ))}

      <div className="assistant-panel flex min-w-0 flex-col gap-3">
        <div className="flex min-h-6 items-center gap-3">
          <span
            className={cn(
              'grid size-5 shrink-0 place-items-center bg-foreground font-mono text-[10px] font-semibold text-background',
              running && 'animate-pulse',
            )}
            aria-hidden="true"
          >
            M
          </span>
          <span className="sr-only">{t('chat.assistant')}</span>
          <TurnStatusBadge status={turn.status} />
          <span className="h-3 w-px bg-border" aria-hidden="true" />
          <button
            type="button"
            className="turn-toggle inline-flex items-center gap-1.5 text-[12px] text-muted-foreground transition-colors hover:text-foreground"
            onClick={() => setManualExpanded(!expanded)}
            aria-expanded={expanded}
          >
            {expanded ? t('chat.hideProcess') : t('chat.showProcess')}
            <span className="font-mono text-[11px] text-subtle tabular-nums">
              {t('chat.steps', { n: Math.max(1, steps) })}
            </span>
            <ChevronRight
              className={cn('size-3.5 transition-transform duration-200', expanded && 'rotate-90')}
              aria-hidden="true"
            />
          </button>
          {fileChanged && turnFinished && (
            <span className="ml-auto flex items-center">
              <IconButton size="icon-xs" label={t('chat.undoFiles')} onClick={() => onUndo?.(turn.id)}>
                <Undo2 />
              </IconButton>
              <IconButton size="icon-xs" label={t('chat.redoFiles')} onClick={() => onRedo?.(turn.id)}>
                <Redo2 />
              </IconButton>
            </span>
          )}
          {turn.errorCode && (
            <span
              className={cn(
                'border border-destructive/30 bg-danger-soft px-1.5 font-mono text-[11px] text-destructive',
                !(fileChanged && turnFinished) && 'ml-auto',
              )}
              title={turn.errorMessage ?? ''}
            >
              {turn.errorCode}
            </span>
          )}
        </div>

        <div className="pl-8">
          {expanded ? (
            <ol className="relative flex flex-col gap-5 border-l border-border pl-5">
              {assistantMessages.map((message, index) => {
                const messageCalls = toolCalls.filter((call) => call.messageId === message.id);
                const isFinal = turnFinished && message.id === finalMessage?.id;
                return (
                  <li className="relative flex min-w-0 flex-col gap-3" key={message.id}>
                    <span
                      className={cn(
                        'absolute top-2 -left-[23.5px] size-[7px] border border-border-strong bg-background',
                        isFinal && 'border-foreground bg-foreground',
                        message.status === 'streaming' && 'border-signal bg-signal',
                      )}
                      aria-hidden="true"
                    />
                    <ReasoningBlock
                      text={message.content.reasoning}
                      streaming={message.status === 'streaming' && !message.content.text}
                    />
                    {message.content.text && (
                      <div className={cn('assistant-text', isFinal && 'final-text')}>
                        {isFinal && (
                          <div className="eyebrow mb-2 flex items-center gap-1.5 text-success">
                            <Check className="size-3" aria-hidden="true" />
                            {t('chat.final')}
                          </div>
                        )}
                        <Markdown>{message.content.text}</Markdown>
                        {message.status === 'streaming' && (
                          <span className="streaming-cursor" aria-hidden="true" />
                        )}
                      </div>
                    )}
                    {messageCalls.length > 0 && (
                      <div className="flex flex-col gap-1.5">
                        {messageCalls.map((call) => (
                          <ToolCallCard key={call.id} call={call} />
                        ))}
                      </div>
                    )}
                    {message.status === 'streaming' &&
                      !message.content.text &&
                      !messageCalls.length && (
                        <div className="thinking-dots" role="status" aria-label={t('status.running')}>
                          <span />
                          <span />
                          <span />
                        </div>
                      )}
                    {turnFinished &&
                      index === assistantMessages.length - 1 &&
                      message.status === 'complete' &&
                      assistantActions(message)}
                  </li>
                );
              })}
            </ol>
          ) : (
            <div className="flex flex-col gap-4">
              {finalMessage?.content.reasoning && (
                <ReasoningBlock text={finalMessage.content.reasoning} />
              )}
              {finalMessage?.content.text ? (
                <Markdown>{finalMessage.content.text}</Markdown>
              ) : (
                <p className="text-[14px] text-muted-foreground">
                  {turn.errorMessage ?? t(`turn.${turn.status}`)}
                </p>
              )}
              {toolCalls.length > 0 && (
                <div className="flex flex-col gap-1.5">
                  {toolCalls.map((call) => (
                    <ToolCallCard key={call.id} call={call} />
                  ))}
                </div>
              )}
              {turnFinished &&
                finalMessage?.status === 'complete' &&
                assistantActions(finalMessage)}
            </div>
          )}
        </div>
      </div>
    </article>
  );
}
