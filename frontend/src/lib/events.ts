import type {
  ChatMessage,
  Interaction,
  QueueItem,
  Session,
  Snapshot,
  StreamEvent,
  TaskInfo,
  ToolCall,
  Turn,
  UsageTotals,
} from '../api/types';

function upsertById<T extends { id: string }>(items: T[], incoming: T): T[] {
  const index = items.findIndex((item) => item.id === incoming.id);
  if (index === -1) return [...items, incoming];
  const next = items.slice();
  next[index] = { ...items[index], ...incoming };
  return next;
}

function messageWithDelta(
  messages: ChatMessage[],
  messageId: string,
  field: 'text' | 'reasoning',
  text: string,
): ChatMessage[] {
  return messages.map((message) =>
    message.id === messageId
      ? {
          ...message,
          content: { ...message.content, [field]: message.content[field] + text },
        }
      : message,
  );
}

function upsertTurn(turns: Turn[], incoming: Partial<Turn> & { id: string }): Turn[] {
  const index = turns.findIndex((turn) => turn.id === incoming.id);
  if (index === -1) {
    return [
      ...turns,
      {
        id: incoming.id,
        sessionId: incoming.sessionId ?? '',
        seq: incoming.seq ?? turns.length + 1,
        status: incoming.status ?? 'running',
        startedAt: incoming.startedAt ?? new Date().toISOString(),
        endedAt: incoming.endedAt ?? null,
        errorCode: incoming.errorCode ?? null,
        errorMessage: incoming.errorMessage ?? null,
        usage: incoming.usage ?? {},
      },
    ];
  }
  const next = turns.slice();
  next[index] = { ...turns[index], ...incoming };
  return next;
}

/** Pure event fold used by the UI and by unit tests. */
export function applyEvent(snapshot: Snapshot, event: StreamEvent): Snapshot {
  const type = event.type;
  switch (type) {
    case 'session.status': {
      const status = event.status as Session['status'] | undefined;
      if (!status) return snapshot;
      return {
        ...snapshot,
        session: {
          ...snapshot.session,
          status,
          lastError: (event.message as string | undefined) ?? snapshot.session.lastError,
        },
        active: !['idle', 'error', 'paused'].includes(status),
      };
    }
    case 'session.updated': {
      const session = event.session as Partial<Session> | undefined;
      if (!session) return snapshot;
      return { ...snapshot, session: { ...snapshot.session, ...session } };
    }
    case 'session.recovered': {
      return { ...snapshot, session: { ...snapshot.session, status: 'error' } };
    }
    case 'message.created':
    case 'message.updated': {
      const message = event.message as ChatMessage | undefined;
      if (!message) return snapshot;
      const messages = upsertById(snapshot.messages, message).sort((a, b) => a.seq - b.seq);
      return { ...snapshot, messages };
    }
    case 'content.delta': {
      const messageId = event.messageId as string;
      const text = event.text as string;
      if (!messageId || !text) return snapshot;
      return { ...snapshot, messages: messageWithDelta(snapshot.messages, messageId, 'text', text) };
    }
    case 'reasoning.delta': {
      const messageId = event.messageId as string;
      const text = event.text as string;
      if (!messageId || !text) return snapshot;
      return {
        ...snapshot,
        messages: messageWithDelta(snapshot.messages, messageId, 'reasoning', text),
      };
    }
    case 'message.reset': {
      const messageId = event.messageId as string;
      return {
        ...snapshot,
        messages: snapshot.messages.map((message) =>
          message.id === messageId
            ? { ...message, content: { text: '', reasoning: '', toolCalls: [] } }
            : message,
        ),
      };
    }
    case 'message.removed': {
      const messageId = event.messageId as string;
      return { ...snapshot, messages: snapshot.messages.filter((message) => message.id !== messageId) };
    }
    case 'tool.started':
    case 'tool.updated':
    case 'tool.completed': {
      const toolCall = event.toolCall as ToolCall | undefined;
      if (!toolCall) return snapshot;
      return { ...snapshot, toolCalls: upsertById(snapshot.toolCalls, toolCall) };
    }
    case 'turn.started': {
      const turn = event.turn as Partial<Turn> | undefined;
      if (!turn?.id) return snapshot;
      return { ...snapshot, turns: upsertTurn(snapshot.turns, turn as Turn) };
    }
    case 'turn.completed':
    case 'turn.failed':
    case 'turn.paused':
    case 'turn.cancelled': {
      const turnId = event.turnId as string;
      if (!turnId) return snapshot;
      const statusMap: Record<string, Turn['status']> = {
        'turn.completed': 'completed',
        'turn.failed': 'failed',
        'turn.paused': 'paused',
        'turn.cancelled': 'cancelled',
      };
      const turns = upsertTurn(snapshot.turns, {
        id: turnId,
        status: statusMap[type],
        endedAt: (event.endedAt as string | undefined) ?? new Date().toISOString(),
        errorMessage: (event.message as string | undefined) ?? null,
        errorCode: (event.code as string | undefined) ?? null,
      });
      return { ...snapshot, turns };
    }
    case 'queue.updated': {
      const queue = event.queue as QueueItem[] | undefined;
      return queue ? { ...snapshot, queue } : snapshot;
    }
    case 'task.updated': {
      const task = event.task as Partial<TaskInfo> & { id: string };
      if (!task?.id) return snapshot;
      return { ...snapshot, tasks: upsertById(snapshot.tasks, task as TaskInfo) };
    }
    case 'interaction.required': {
      const interaction = event.interaction as Interaction | undefined;
      if (!interaction) return snapshot;
      return { ...snapshot, interactions: upsertById(snapshot.interactions, interaction) };
    }
    case 'interaction.resolved': {
      const interaction = event.interaction as Interaction | undefined;
      if (!interaction) return snapshot;
      return {
        ...snapshot,
        interactions: snapshot.interactions.filter((item) => item.id !== interaction.id),
      };
    }
    case 'usage.updated': {
      const usage = event.usage as UsageTotals | undefined;
      if (!usage) return snapshot;
      return { ...snapshot, usage: { ...snapshot.usage, ...usage } };
    }
    case 'compaction.created': {
      const compaction = event.compaction as Snapshot['compaction'];
      return compaction ? { ...snapshot, compaction } : snapshot;
    }
    case 'goal.updated': {
      const goal = event.goal as Snapshot['goal'];
      return goal ? { ...snapshot, goal } : snapshot;
    }
    default:
      return snapshot;
  }
}

export function pendingInteractions(snapshot: Snapshot | null): Interaction[] {
  return snapshot?.interactions ?? [];
}
