import { createContext, useContext } from 'react';
import type { Language, Translator } from '../lib/i18n';
import type { ResolvedTheme, ThemeSetting } from '../lib/theme';

export interface UiContextValue {
  language: Language;
  t: Translator;
  themeSetting: ThemeSetting;
  resolvedTheme: ResolvedTheme;
  setLanguage: (language: Language) => void;
  setTheme: (theme: ThemeSetting) => void;
}

export const UiContext = createContext<UiContextValue | null>(null);

export function useUi(): UiContextValue {
  const value = useContext(UiContext);
  if (!value) throw new Error('useUi must be used inside UiContext');
  return value;
}
