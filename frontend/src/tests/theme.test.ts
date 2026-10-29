import { beforeEach, describe, expect, it, vi } from 'vitest';
import { loadTheme, resolveTheme, saveTheme } from '../lib/theme';

describe('theme resolution', () => {
  beforeEach(() => {
    localStorage.clear();
    window.matchMedia = vi.fn().mockReturnValue({
      matches: true,
      media: '(prefers-color-scheme: dark)',
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }) as unknown as typeof window.matchMedia;
  });

  it('follows the system preference and persists explicit choices', () => {
    expect(resolveTheme('system')).toBe('dark');
    expect(resolveTheme('light')).toBe('light');
    saveTheme('dark');
    expect(loadTheme()).toBe('dark');
    localStorage.removeItem('mini-agent-theme');
    expect(loadTheme()).toBe('system');
  });
});
