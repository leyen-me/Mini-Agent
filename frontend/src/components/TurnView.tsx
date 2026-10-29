import {
  Check,
  ChevronDown,
  ChevronRight,
  GitFork,
  Pencil,
  Redo2,
  Undo2,
} from 'lucide-react';
import { useMemo, useState } from 'react';
import type { ChatMessage, ToolCall, Turn } from '../api/types';
import { useUi } from '../state/UiContext';
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
  const Chevron = expanded ? ChevronDown : ChevronRight;
  const fileChanged = toolCalls.some((call) => {
    const data = (call.output?.data ?? null) as Record<string, unknown> | null;
    return Boolean(
      typeof data?.checkpointId === 'string' ||
      (typeof data?.diff === 'string' && data.diff.length > 0),
    );
  });

  return (
    <article
      className={`turn ${running ? 'turn-running' : ''}`}
      aria-label={`Turn ${turn.seq}`}
    >
      {userMessages.map((message) => (
        <div className="message-row user-row" key={message.id}>
          <div className="message-bubble user-bubble">
            <div className="message-meta">
              <span className="message-author">{t('chat.you')}</span>
            </div>
            <div className="user-content">{message.content.text}</div>
            <Attachments items={message.content.attachments} />
            <div className="message-actions">
              <button
                type="button"
                className="icon-button"
                title={t('app.edit')}
                aria-label={t('app.edit')}
                onClick={() => onEditMessage(message)}
              >
                <Pencil size={14} aria-hidden="true" />
              </button>
              <CopyButton text={message.content.text} iconOnly />
            </div>
          </div>
        </div>
      ))}

      <div
        className={`assistant-panel ${expanded ? 'is-expanded' : 'is-collapsed'}`}
      >
        <div className="assistant-panel-header">
          <button
            type="button"
            className="turn-toggle"
            onClick={() => setManualExpanded(!expanded)}
            aria-expanded={expanded}
          >
            <Chevron size={14} aria-hidden="true" />
            <span>{expanded ? t('chat.process') : t('chat.final')}</span>
            <span className="turn-steps">
              {t('chat.steps', { n: Math.max(1, steps) })}
            </span>
          </button>
          <TurnStatusBadge status={turn.status} />
          {fileChanged && turnFinished && (
            <span className="turn-file-actions">
              <button
                type="button"
                className="icon-button"
                title={t('chat.undoFiles')}
                aria-label={t('chat.undoFiles')}
                onClick={() => onUndo?.(turn.id)}
              >
                <Undo2 size={14} aria-hidden="true" />
              </button>
              <button
                type="button"
                className="icon-button"
                title={t('chat.redoFiles')}
                aria-label={t('chat.redoFiles')}
                onClick={() => onRedo?.(turn.id)}
              >
                <Redo2 size={14} aria-hidden="true" />
              </button>
            </span>
          )}
          {turn.errorCode && (
            <span className="turn-error-code" title={turn.errorMessage ?? ''}>
              {turn.errorCode}
            </span>
          )}
        </div>

        {expanded ? (
          <div className="assistant-steps">
            {assistantMessages.map((message, index) => {
              const messageCalls = toolCalls.filter(
                (call) => call.messageId === message.id,
              );
              const isFinal = turnFinished && message.id === finalMessage?.id;
              return (
                <div className="assistant-step" key={message.id}>
                  <ReasoningBlock
                    text={message.content.reasoning}
                    streaming={
                      message.status === 'streaming' && !message.content.text
                    }
                  />
                  {message.content.text && (
                    <div
                      className={`assistant-text ${isFinal ? 'final-text' : ''}`}
                    >
                      {isFinal && (
                        <div className="final-label">
                          <Check size={12} aria-hidden="true" />{' '}
                          {t('chat.final')}
                        </div>
                      )}
                      <Markdown>{message.content.text}</Markdown>
                      {message.status === 'streaming' && (
                        <span className="streaming-cursor" aria-hidden="true" />
                      )}
                    </div>
                  )}
                  {messageCalls.map((call) => (
                    <ToolCallCard key={call.id} call={call} />
                  ))}
                  {message.status === 'streaming' &&
                    !message.content.text &&
                    !messageCalls.length && (
                      <div
                        className="thinking-indicator"
                        role="status"
                        aria-label={t('status.running')}
                      >
                        <span className="dot" />
                        <span className="dot" />
                        <span className="dot" />
                      </div>
                    )}
                  {turnFinished &&
                    index === assistantMessages.length - 1 &&
                    message.status === 'complete' && (
                      <div className="message-actions assistant-actions">
                        <button
                          type="button"
                          className="icon-button"
                          title={t('chat.fork')}
                          aria-label={t('chat.fork')}
                          onClick={() => onFork(message.id)}
                        >
                          <GitFork size={14} aria-hidden="true" />
                        </button>
                        <CopyButton text={message.content.text} iconOnly />
                      </div>
                    )}
                </div>
              );
            })}
          </div>
        ) : (
          <div className="assistant-collapsed">
            {finalMessage?.content.reasoning && (
              <ReasoningBlock text={finalMessage.content.reasoning} />
            )}
            {finalMessage?.content.text ? (
              <Markdown>{finalMessage.content.text}</Markdown>
            ) : (
              <p className="muted">
                {turn.errorMessage ?? t(`turn.${turn.status}`)}
              </p>
            )}
            {toolCalls.length > 0 && (
              <div className="collapsed-tools">
                {toolCalls.map((call) => (
                  <ToolCallCard key={call.id} call={call} />
                ))}
              </div>
            )}
            {turnFinished && finalMessage?.status === 'complete' && (
              <div className="message-actions assistant-actions">
                <button
                  type="button"
                  className="icon-button"
                  title={t('chat.fork')}
                  aria-label={t('chat.fork')}
                  onClick={() => onFork(finalMessage.id)}
                >
                  <GitFork size={14} aria-hidden="true" />
                </button>
                <CopyButton text={finalMessage.content.text} iconOnly />
              </div>
            )}
          </div>
        )}
      </div>
    </article>
  );
}
