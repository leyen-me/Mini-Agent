import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { GoalDialog } from '../components/GoalDialog';
import type { Goal } from '../api/types';
import { renderWithUi } from './testUtils';

describe('GoalDialog task states', () => {
  it('preserves in_progress when saving without edits', async () => {
    const goal: Goal = {
      id: 'g1',
      objective: 'ship',
      criteria: 'tests',
      status: 'active',
      updatedAt: '',
      tasks: [
        { id: 't1', title: 'first', status: 'in_progress', position: 1 },
        { id: 't2', title: 'second', status: 'completed', position: 2 },
      ],
    };
    const onSave = vi.fn().mockResolvedValue(undefined);
    renderWithUi(
      <GoalDialog open goal={goal} onClose={vi.fn()} onSave={onSave} />,
    );
    expect(
      (screen.getByLabelText(/任务清单/) as HTMLTextAreaElement).value,
    ).toContain('[>] first');
    fireEvent.click(screen.getByRole('button', { name: '保存目标' }));
    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect(onSave.mock.calls[0][0].tasks).toEqual([
      { title: 'first', status: 'in_progress' },
      { title: 'second', status: 'completed' },
    ]);
  });
});
