import { describe, expect, it } from 'vitest';
import { applyEvent } from '../lib/events';
import type { StreamEvent } from '../api/types';
import { makeSnapshot } from './testUtils';

const message = {
  id: 'msg_1',
  sessionId: 'sess_test',
  turnId: 'turn_1',
  seq: 1,
  role: 'assistant' as const,
  content: { text: '', reasoning: '', toolCalls: [] },
  status: 'streaming' as const,
  clientMessageId: null,
  createdAt: '2026-01-01T00:00:00Z',
};

describe('event folding', () => {
  it('appends deltas, resets discarded attempts and applies tool updates', () => {
    let snapshot = makeSnapshot({ messages: [message] });
    snapshot = applyEvent(snapshot, { id: 2, sessionId: 'sess_test', type: 'content.delta', messageId: 'msg_1', text: 'hello' } as StreamEvent);
    snapshot = applyEvent(snapshot, { id: 3, sessionId: 'sess_test', type: 'reasoning.delta', messageId: 'msg_1', text: 'thinking' } as StreamEvent);
    expect(snapshot.messages[0].content).toMatchObject({ text: 'hello', reasoning: 'thinking' });
    snapshot = applyEvent(snapshot, { id: 4, sessionId: 'sess_test', type: 'message.reset', messageId: 'msg_1' } as StreamEvent);
    expect(snapshot.messages[0].content).toMatchObject({ text: '', reasoning: '' });
    snapshot = applyEvent(snapshot, {
      id: 5,
      sessionId: 'sess_test',
      type: 'tool.completed',
      toolCall: {
        id: 'call_1',
        sessionId: 'sess_test',
        turnId: 'turn_1',
        messageId: 'msg_1',
        name: 'shell',
        input: { command: 'echo hi' },
        output: { code: 0, message: 'ok', data: {}, truncated: false, artifacts: [] },
        status: 'succeeded',
        risk: 'high',
        errorCode: null,
        startedAt: '2026-01-01T00:00:00Z',
        endedAt: '2026-01-01T00:00:01Z',
      },
    } as StreamEvent);
    expect(snapshot.toolCalls[0].status).toBe('succeeded');
  });

  it('removes resolved interactions and updates session status', () => {
    const interaction = {
      id: 'int_1',
      sessionId: 'sess_test',
      turnId: 'turn_1',
      type: 'permission' as const,
      payload: {},
      status: 'pending' as const,
      answer: null,
      createdAt: '2026-01-01T00:00:00Z',
    };
    let snapshot = makeSnapshot({ interactions: [interaction] });
    snapshot = applyEvent(snapshot, {
      id: 6,
      sessionId: 'sess_test',
      type: 'interaction.resolved',
      interaction: { ...interaction, status: 'answered' },
    } as StreamEvent);
    expect(snapshot.interactions).toHaveLength(0);
    snapshot = applyEvent(snapshot, { id: 7, sessionId: 'sess_test', type: 'session.status', status: 'idle' } as StreamEvent);
    expect(snapshot.session.status).toBe('idle');
    expect(snapshot.active).toBe(false);
  });
});
