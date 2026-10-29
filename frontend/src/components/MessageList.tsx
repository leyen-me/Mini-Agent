import { AlertTriangle, Archive, Info } from 'lucide-react';
import type { ChatMessage, Snapshot } from '../api/types';
import { formatTokens } from '../lib/i18n';
import { useUi } from '../state/UiContext';
import { CopyButton } from './CopyButton';
import { Markdown } from './Markdown';
import { TurnView } from './TurnView';

interface Props {
  snapshot: Snapshot;
  onEditMessage: (message: ChatMessage) => void;
  onFork: (messageId: string) => void;
}

function SystemNotice({ message }: { message: ChatMessage }) {
  const { t } = useUi();
  const isError = /失败|错误|error|failed|重启|recovered/i.test(message.content.text);
  const Icon = isError ? AlertTriangle : Info;
  return (
    <div className={`system-notice ${isError ? 'notice-warn' : ''}`} role="note">
      <Icon size={14} aria-hidden="true" />
      <span>{message.content.text}</span>
      <span className="sr-only">{t('app.system')}</span>
    </div>
  );
}

function CompactionCard({ snapshot }: { snapshot: Snapshot }) {
  const { t } = useUi();
  if (!snapshot.compaction) return null;
  return (
    <details className="compaction-card">
      <summary>
        <Archive size={15} aria-hidden="true" />
        <span>{t('chat.compaction')}</span>
        <span className="compaction-meta">
          {t('chat.tokensBefore')} {formatTokens(snapshot.compaction.tokensBefore)} → {t('chat.tokensAfter')}{' '}
          {formatTokens(snapshot.compaction.tokensAfter)}
        </span>
      </summary>
      <div className="compaction-body">
        <div className="compaction-actions">
          <span>{t('chat.covered', { n: snapshot.compaction.coveredUntilSeq })}</span>
          <CopyButton text={snapshot.compaction.summary} iconOnly />
        </div>
        <pre>{snapshot.compaction.summary}</pre>
      </div>
    </details>
  );
}

export function MessageList({ snapshot, onEditMessage, onFork }: Props) {
  const { t } = useUi();
  const turns = new Map(snapshot.turns.map((turn) => [turn.id, turn]));
  const messagesByTurn = new Map<string, ChatMessage[]>();
  for (const message of snapshot.messages) {
    if (!message.turnId || (message.role !== 'user' && message.role !== 'assistant')) continue;
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
    if (message.turnId && turns.has(message.turnId) && !renderedTurns.has(message.turnId)) {
      const turn = turns.get(message.turnId)!;
      const turnMessages = messagesByTurn.get(message.turnId) ?? [];
      if (turnMessages.length) {
        renderedTurns.add(message.turnId);
        blocks.push(
          <TurnView
            key={turn.id}
            turn={turn}
            messages={turnMessages}
            toolCalls={snapshot.toolCalls.filter((call) => call.turnId === turn.id)}
            onEditMessage={onEditMessage}
            onFork={onFork}
          />,
        );
      }
      continue;
    }
    if (!message.turnId) {
      if (message.role === 'user') {
        blocks.push(
          <div className="message-row user-row" key={message.id}>
            <div className="message-bubble user-bubble">{message.content.text}</div>
          </div>,
        );
      } else if (message.role === 'assistant' && message.content.text) {
        blocks.push(
          <div className="assistant-panel standalone" key={message.id}>
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
      />,
    );
  }

  return (
    <div className="message-list">
      <CompactionCard snapshot={snapshot} />
      {blocks}
      {snapshot.messages.length === 0 && <p className="muted">{t('app.noSessionsHint')}</p>}
    </div>
  );
}
