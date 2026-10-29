import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { SessionStatusBadge, ToolStatusBadge, TurnStatusBadge } from '../components/StatusBadge';
import { renderWithUi } from './testUtils';

describe('status representations are never colour-only', () => {
  it('renders an icon plus a text label for every session state', () => {
    for (const status of ['idle', 'running', 'waiting_user', 'paused', 'compacting', 'error'] as const) {
      const { unmount } = renderWithUi(<SessionStatusBadge status={status} />);
      const badge = screen.getByRole('status');
      expect(badge).toHaveAttribute('aria-label');
      expect(badge.querySelector('svg')).not.toBeNull();
      expect(badge.textContent?.trim().length).toBeGreaterThan(0);
      unmount();
    }
  });

  it('renders tool and turn statuses with labels', () => {
    renderWithUi(
      <div>
        <ToolStatusBadge status="denied" />
        <TurnStatusBadge status="failed" />
      </div>,
    );
    expect(screen.getByRole('status', { name: '已拒绝' })).toBeInTheDocument();
    expect(screen.getByText('失败')).toBeInTheDocument();
    expect(screen.getAllByRole('status')).toHaveLength(2);
  });
});
