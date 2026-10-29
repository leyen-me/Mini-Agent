import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { contrastRatio, extractThemeTokens } from '../lib/contrast';

const css = readFileSync(resolve(__dirname, '../styles.css'), 'utf8');

describe('light and dark theme contrast', () => {
  for (const theme of ['light', 'dark'] as const) {
    it(`${theme} theme meets WCAG AA for reading and code surfaces`, () => {
      const tokens = extractThemeTokens(css, theme);
      expect(Object.keys(tokens).length).toBeGreaterThan(10);
      const pairs: Array<[string, string, number]> = [
        ['--foreground', '--background', 7],
        ['--foreground', '--card', 7],
        ['--foreground', '--sidebar', 7],
        ['--muted-foreground', '--background', 4.5],
        ['--muted-foreground', '--sidebar', 4.5],
        ['--muted-foreground', '--muted', 4.5],
        ['--primary-foreground', '--primary', 7],
        ['--code-foreground', '--code-bg', 7],
        ['--signal', '--background', 3],
        ['--destructive', '--danger-soft', 3],
        ['--warning', '--warning-soft', 4.5],
        ['--success', '--success-soft', 4.5],
      ];
      for (const [foreground, background, minimum] of pairs) {
        const ratio = contrastRatio(tokens[foreground], tokens[background]);
        expect(ratio, `${theme} ${foreground} on ${background} = ${ratio.toFixed(2)}`).toBeGreaterThanOrEqual(minimum);
      }
    });
  }
});
