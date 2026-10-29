import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { api } from '../api/client';
import { Composer } from '../components/Composer';
import { makeConfig, makeSession, renderWithUi } from './testUtils';

function renderComposer(overrides: { busy?: boolean; running?: boolean } = {}) {
  const onSend = vi.fn().mockResolvedValue(undefined);
  const session = makeSession({
    status: overrides.running ? 'running' : 'idle',
  });
  renderWithUi(
    <Composer
      session={session}
      config={makeConfig()}
      queue={[]}
      busy={Boolean(overrides.busy || overrides.running)}
      onSend={onSend}
      onStop={vi.fn()}
      onModeChange={vi.fn()}
      onModelChange={vi.fn()}
      onCompact={vi.fn()}
      onExport={vi.fn()}
      onNew={vi.fn()}
      onOpenGoal={vi.fn()}
      onOpenSettings={vi.fn()}
      onShowHelp={vi.fn()}
      onToast={vi.fn()}
      onUpdateQueue={vi.fn()}
      onDeleteQueue={vi.fn()}
    />,
  );
  return { onSend };
}

describe('Composer IME and keyboard behaviour', () => {
  it('does not send while the IME is composing', async () => {
    const { onSend } = renderComposer();
    const input = screen.getByTestId('composer-input') as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: '你好世界' } });
    fireEvent.compositionStart(input);
    fireEvent.keyDown(input, {
      key: 'Enter',
      code: 'Enter',
      keyCode: 229,
      which: 229,
    });
    await waitFor(() => expect(onSend).not.toHaveBeenCalled());
    fireEvent.compositionEnd(input);
    fireEvent.keyDown(input, {
      key: 'Enter',
      code: 'Enter',
      keyCode: 13,
      which: 13,
    });
    await waitFor(() => expect(onSend).toHaveBeenCalledTimes(1));
    expect(onSend).toHaveBeenCalledWith('你好世界', 'auto');
  });

  it('sends on Enter, inserts a newline on Shift+Enter, and never sends while empty', async () => {
    const { onSend } = renderComposer();
    const input = screen.getByTestId('composer-input') as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: 'line one' } });
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter', shiftKey: true });
    await waitFor(() => expect(onSend).not.toHaveBeenCalled());
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });
    await waitFor(() =>
      expect(onSend).toHaveBeenCalledWith('line one', 'auto'),
    );
  });

  it('sends queued or steered messages while a turn is running', async () => {
    const { onSend } = renderComposer({ busy: true, running: true });
    const input = screen.getByTestId('composer-input') as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: '补充说明' } });
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });
    await waitFor(() =>
      expect(onSend).toHaveBeenCalledWith('补充说明', 'queue'),
    );
    fireEvent.click(screen.getByRole('button', { name: '插话' }));
    fireEvent.change(input, { target: { value: '纠偏' } });
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });
    await waitFor(() =>
      expect(onSend).toHaveBeenLastCalledWith('纠偏', 'steer'),
    );
  });
});

describe('custom command input', () => {
  it('opens preview with arguments instead of sending raw slash text', async () => {
    const onSend = vi.fn().mockResolvedValue(undefined);
    const onPreviewCommand = vi.fn();
    renderWithUi(
      <Composer
        session={makeSession()}
        config={makeConfig()}
        queue={[]}
        busy={false}
        onSend={onSend}
        commands={[
          {
            id: 'cmd_1',
            name: 'review',
            description: 'Review',
            promptTemplate: '请审查 $ARGUMENTS',
            agentId: null,
            model: null,
            reasoningLevel: null,
            enabled: true,
            scope: 'system',
            createdAt: '',
            updatedAt: '',
          },
        ]}
        onPreviewCommand={onPreviewCommand}
        onStop={vi.fn()}
        onModeChange={vi.fn()}
        onModelChange={vi.fn()}
        onCompact={vi.fn()}
        onExport={vi.fn()}
        onNew={vi.fn()}
        onOpenGoal={vi.fn()}
        onOpenSettings={vi.fn()}
        onShowHelp={vi.fn()}
        onToast={vi.fn()}
        onUpdateQueue={vi.fn()}
        onDeleteQueue={vi.fn()}
      />,
    );
    const input = screen.getByTestId('composer-input') as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: '/review src/a.ts 安全' } });
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });
    await waitFor(() => expect(onPreviewCommand).toHaveBeenCalledTimes(1));
    expect(onPreviewCommand.mock.calls[0][1]).toBe('src/a.ts 安全');
    expect(onSend).not.toHaveBeenCalled();
  });
});

describe('@ file references', () => {
  it('searches subdirectories instead of only root files', async () => {
    vi.spyOn(api, 'files').mockResolvedValue({
      path: '.',
      isDirectory: true,
      entries: [],
    });
    vi.spyOn(api.workspacesApi, 'search').mockResolvedValue({
      results: [{ path: 'src/App.tsx', line: null, text: null }],
    });
    const onSend = vi.fn().mockResolvedValue(undefined);
    renderWithUi(
      <Composer
        session={makeSession()}
        config={makeConfig()}
        queue={[]}
        busy={false}
        onSend={onSend}
        onStop={vi.fn()}
        onModeChange={vi.fn()}
        onModelChange={vi.fn()}
        onCompact={vi.fn()}
        onExport={vi.fn()}
        onNew={vi.fn()}
        onOpenGoal={vi.fn()}
        onOpenSettings={vi.fn()}
        onShowHelp={vi.fn()}
        onToast={vi.fn()}
        onUpdateQueue={vi.fn()}
        onDeleteQueue={vi.fn()}
      />,
    );
    const input = screen.getByTestId('composer-input') as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: 'see @src' } });
    await waitFor(() =>
      expect(screen.getByText('src/App.tsx')).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByText('src/App.tsx'));
    expect(input.value).toContain('@src/App.tsx');
    vi.restoreAllMocks();
  });
});
