import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Markdown } from '../components/Markdown';
import { renderWithUi } from './testUtils';

describe('Markdown safety and rendering', () => {
  it('renders GFM tables and code while sanitizing raw HTML and XSS', () => {
    const source = [
      '# Heading',
      '',
      '| A | B |',
      '| - | - |',
      '| 1 | 2 |',
      '',
      '```ts',
      'const answer = 42;',
      '```',
      '',
      '<script>window.__pwned = true</script>',
      '<img src="x" onerror="window.__pwned = true" />',
      '',
      '[safe](https://example.com)',
    ].join('\n');
    const { container } = renderWithUi(<Markdown>{source}</Markdown>);
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Heading');
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(container.querySelector('pre code')?.textContent?.trim()).toBe('const answer = 42;');
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('img')).toBeNull();
    const link = screen.getByRole('link', { name: 'safe' });
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'));
  });
});
