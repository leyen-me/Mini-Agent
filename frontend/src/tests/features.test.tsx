import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { api } from '../api/client';
import { Attachments } from '../components/Attachments';
import { Composer } from '../components/Composer';
import { DiffView } from '../components/DiffView';
import { makeConfig, makeSession, renderWithUi } from './testUtils';

function composer(onSend = vi.fn().mockResolvedValue(undefined)) {
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
  return onSend;
}

describe('attachments and file changes', () => {
  it('uploads a file and sends its attachment id', async () => {
    vi.spyOn(api, 'upload').mockResolvedValue({
      id: 'upl_1',
      filename: 'note.txt',
      mime: 'text/plain',
      kind: 'text',
      url: '/api/v1/uploads/upl_1/content',
    });
    const onSend = composer();
    const fileInput = document.querySelector(
      'input[type=file]',
    ) as HTMLInputElement;
    const file = new File(['hello'], 'note.txt', { type: 'text/plain' });
    fireEvent.change(fileInput, { target: { files: [file] } });
    await waitFor(() =>
      expect(screen.getByText('note.txt')).toBeInTheDocument(),
    );
    const input = screen.getByTestId('composer-input') as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: '看看附件' } });
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });
    await waitFor(() =>
      expect(onSend).toHaveBeenCalledWith('看看附件', 'auto', ['upl_1']),
    );
    vi.restoreAllMocks();
  });

  it('renders image and file attachments', () => {
    renderWithUi(
      <Attachments
        items={[
          {
            id: 'a',
            filename: 'image.png',
            mime: 'image/png',
            kind: 'image',
            url: '/api/v1/uploads/a/content',
          },
          {
            id: 'b',
            filename: 'report.pdf',
            mime: 'application/pdf',
            kind: 'pdf',
            url: '/api/v1/uploads/b/content',
          },
        ]}
      />,
    );
    expect(screen.getByRole('img', { name: 'image.png' })).toBeInTheDocument();
    expect(screen.getByText('report.pdf')).toBeInTheDocument();
  });

  it('highlights added and removed diff lines', () => {
    const { container } = renderWithUi(
      <DiffView diff={'--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new'} />,
    );
    expect(container.querySelectorAll('.diff-add')).toHaveLength(1);
    expect(container.querySelectorAll('.diff-remove')).toHaveLength(1);
    expect(container.querySelectorAll('.diff-hunk')).toHaveLength(1);
  });
});
