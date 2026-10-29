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
        ['--text', '--surface', 7],
        ['--text', '--bg', 7],
        ['--text-soft', '--surface', 4.5],
        ['--muted', '--surface', 4.5],
        ['--code-text', '--code-bg', 7],
        ['--accent', '--surface', 3],
        ['--danger', '--danger-soft', 3],
      ];
      for (const [foreground, background, minimum] of pairs) {
        const ratio = contrastRatio(tokens[foreground], tokens[background]);
        expect(ratio, `${theme} ${foreground} on ${background} = ${ratio.toFixed(2)}`).toBeGreaterThanOrEqual(minimum);
      }
    });
  }
});
