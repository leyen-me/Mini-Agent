import { readFileSync, readdirSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { dictionaries, translate, type Language } from '../lib/i18n';

const LANGUAGES: Language[] = ['zh-CN', 'en-US'];
const sourceRoot = resolve(process.cwd(), 'src');

function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    // Tests may assert on translation keys without rendering them.
    if (entry.name === 'tests') return [];
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return sourceFiles(path);
    return /\.(ts|tsx)$/.test(entry.name) ? [path] : [];
  });
}

/**
 * Keys whose suffix comes from a runtime enum or backend config. Keep these in
 * sync with PermissionConfig in backend/mini_agent/config.py and the enums in
 * src/api/types.ts.
 */
const DYNAMIC_KEYS: Record<string, readonly string[]> = {
  status: ['idle', 'running', 'waiting_user', 'paused', 'compacting', 'error'],
  tool: [
    'validating',
    'awaiting_permission',
    'running',
    'succeeded',
    'failed',
    'denied',
    'cancelled',
    'timeout',
  ],
  turn: ['running', 'completed', 'paused', 'failed', 'cancelled'],
  'chat.interaction.risk': ['low', 'medium', 'high'],
  'chat.task': ['queued', 'running', 'succeeded', 'failed', 'cancelled'],
  'app.connection': ['connecting', 'connected', 'reconnecting', 'offline'],
  'chat.reasoning': [
    'off',
    'low',
    'medium',
    'high',
    'xhigh',
    'max',
    'minimal',
    'ultra',
  ],
  inspector: ['activity', 'tasks', 'files', 'context'],
  'settings.permissions': [
    'read',
    'write',
    'shell',
    'network',
    'interaction',
    'task',
    'mcp',
    'plugin',
    'subagent',
  ],
};

function missingKeys(language: Language, keys: Iterable<string>): string[] {
  return [...keys].filter((key) => !(key in dictionaries[language]));
}

describe('i18n dictionaries', () => {
  it('keeps zh-CN and en-US keys in sync', () => {
    expect(Object.keys(dictionaries['en-US']).sort()).toEqual(
      Object.keys(dictionaries['zh-CN']).sort(),
    );
  });

  it('has no empty translations', () => {
    for (const language of LANGUAGES) {
      for (const [key, value] of Object.entries(dictionaries[language])) {
        expect(value.trim(), `${language} ${key}`).not.toBe('');
      }
    }
  });

  it('defines every statically referenced key', () => {
    const missing = new Set<string>();
    for (const file of sourceFiles(sourceRoot)) {
      const source = readFileSync(file, 'utf8');
      for (const match of source.matchAll(/\bt\(\s*(['"])([^'"]+)\1/g)) {
        const key = match[2];
        if (missingKeys('zh-CN', [key]).length) {
          missing.add(`${relative(sourceRoot, file)}: ${key}`);
        }
      }
    }
    expect([...missing]).toEqual([]);
  });

  it('defines every enum-derived dynamic key', () => {
    const keys = Object.entries(DYNAMIC_KEYS).flatMap(([prefix, values]) =>
      values.map((value) => `${prefix}.${value}`),
    );
    for (const language of LANGUAGES) {
      expect(missingKeys(language, keys), language).toEqual([]);
    }
  });

  it('does not fall back to raw keys', () => {
    for (const language of LANGUAGES) {
      for (const key of Object.keys(dictionaries[language])) {
        expect(translate(language, key), `${language} ${key}`).not.toBe(key);
      }
    }
  });
});
