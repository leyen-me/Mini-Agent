function channel(value: number): number {
  const normalized = value / 255;
  return normalized <= 0.03928 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4;
}

export function relativeLuminance(hex: string): number {
  const value = hex.replace('#', '').trim();
  const expanded = value.length === 3 ? value.split('').map((char) => char + char).join('') : value;
  const r = Number.parseInt(expanded.slice(0, 2), 16);
  const g = Number.parseInt(expanded.slice(2, 4), 16);
  const b = Number.parseInt(expanded.slice(4, 6), 16);
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

export function contrastRatio(foreground: string, background: string): number {
  const a = relativeLuminance(foreground);
  const b = relativeLuminance(background);
  const lighter = Math.max(a, b);
  const darker = Math.min(a, b);
  return (lighter + 0.05) / (darker + 0.05);
}

export function extractThemeTokens(css: string, theme: 'light' | 'dark'): Record<string, string> {
  const selector = theme === 'light' ? ':root' : ":root[data-theme='dark']";
  const blockPattern = new RegExp(`${selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*\\{([^}]*)\\}`, 'm');
  const match = blockPattern.exec(css);
  const tokens: Record<string, string> = {};
  if (!match) return tokens;
  for (const line of match[1].split(';')) {
    const separator = line.indexOf(':');
    if (separator === -1) continue;
    const name = line.slice(0, separator).trim();
    const value = line.slice(separator + 1).trim();
    if (name.startsWith('--') && /^#[0-9a-fA-F]{3,8}$/.test(value)) tokens[name] = value;
  }
  return tokens;
}
