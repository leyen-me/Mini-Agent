import { screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MermaidBlock } from '../components/MermaidBlock';
import { renderWithUi } from './testUtils';

vi.mock('mermaid', () => ({
  default: {
    initialize: vi.fn(),
    render: vi.fn(async () => ({
      svg: '<svg data-testid="mock-mermaid"><text>ok</text></svg>',
    })),
  },
}));

describe('Mermaid rendering', () => {
  it('renders SVG returned by mermaid', async () => {
    const { container } = renderWithUi(<MermaidBlock code="graph TD; A-->B" />);
    await waitFor(() =>
      expect(
        container.querySelector('[data-testid="mock-mermaid"]'),
      ).not.toBeNull(),
    );
    expect(screen.getByText('ok')).toBeInTheDocument();
  });
});
