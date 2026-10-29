import { waitFor } from '@testing-library/react';
import { createRef } from 'react';
import { describe, expect, it, vi } from 'vitest';
import type { ChatMessage, Snapshot, Turn } from '../api/types';
import { MessageList } from '../components/MessageList';
import { makeSnapshot, renderWithUi } from './testUtils';

function longSnapshot(): Snapshot {
  const messages: ChatMessage[] = [];
  const turns: Turn[] = [];
  for (let index = 0; index < 45; index += 1) {
    const turnId = `turn_${index}`;
    turns.push({
      id: turnId,
      sessionId: 'sess_test',
      seq: index + 1,
      status: 'completed',
      startedAt: '2026-01-01T00:00:00Z',
      endedAt: '2026-01-01T00:00:01Z',
      errorCode: null,
      errorMessage: null,
      usage: {},
    });
    messages.push(
      {
        id: `user_${index}`,
        sessionId: 'sess_test',
        turnId,
        seq: index * 2 + 1,
        role: 'user',
        content: { text: `question ${index}`, reasoning: '', toolCalls: [] },
        status: 'complete',
        clientMessageId: null,
        createdAt: '2026-01-01T00:00:00Z',
      },
      {
        id: `assistant_${index}`,
        sessionId: 'sess_test',
        turnId,
        seq: index * 2 + 2,
        role: 'assistant',
        content: { text: `answer ${index}`, reasoning: '', toolCalls: [] },
        status: 'complete',
        clientMessageId: null,
        createdAt: '2026-01-01T00:00:01Z',
      },
    );
  }
  return makeSnapshot({ messages, turns });
}

describe('MessageList virtualization', () => {
  it('uses the virtual branch for long histories', () => {
    const ref = createRef<HTMLDivElement>();
    ref.current = document.createElement('div');
    Object.defineProperty(ref.current, 'clientHeight', {
      configurable: true,
      value: 600,
    });
    Object.defineProperty(ref.current, 'scrollHeight', {
      configurable: true,
      value: 12_000,
    });
    ref.current.getBoundingClientRect = () =>
      ({
        top: 0,
        left: 0,
        bottom: 600,
        right: 800,
        width: 800,
        height: 600,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      }) as DOMRect;
    const { container } = renderWithUi(
      <MessageList
        snapshot={longSnapshot()}
        onEditMessage={vi.fn()}
        onFork={vi.fn()}
        scrollRef={ref}
      />,
    );
    return waitFor(() => {
      expect(container.querySelector('.message-list-virtual')).not.toBeNull();
    });
  });
});
