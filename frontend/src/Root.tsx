import { Component, useEffect, useMemo, useState, type ErrorInfo, type ReactNode } from 'react';
import { App } from './App';
import { ShareView } from './components/ShareView';
import { createTranslator, type Language } from './lib/i18n';
import { applyTheme, loadTheme, resolveTheme, saveTheme, type ResolvedTheme, type ThemeSetting } from './lib/theme';
import { UiContext } from './state/UiContext';
import { Button } from '@/components/ui/button';

function loadLanguage(): Language {
  try {
    const stored = localStorage.getItem('mini-agent-language');
    if (stored === 'zh-CN' || stored === 'en-US') return stored;
  } catch {
    // ignore
  }
  return typeof navigator !== 'undefined' && navigator.language.toLowerCase().startsWith('zh') ? 'zh-CN' : 'en-US';
}

class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Mini Agent UI crashed', error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="grid h-dvh place-items-center bg-background p-6">
          <div className="flex max-w-md flex-col items-start gap-4">
            <span className="font-mono text-[10.5px] tracking-[0.12em] text-destructive uppercase">
              界面渲染失败 / UI crashed
            </span>
            <p className="font-mono text-[13px] text-muted-foreground">{this.state.error.message}</p>
            <Button onClick={() => window.location.reload()}>重新加载 / Reload</Button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

export function Root() {
  const [language, setLanguageState] = useState<Language>(loadLanguage);
  const [themeSetting, setThemeSettingState] = useState<ThemeSetting>(loadTheme);
  const [resolvedTheme, setResolvedTheme] = useState<ResolvedTheme>(() => resolveTheme(loadTheme()));

  useEffect(() => {
    const resolved = applyTheme(themeSetting);
    setResolvedTheme(resolved);
    saveTheme(themeSetting);
    if (themeSetting !== 'system') return;
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const handler = () => setResolvedTheme(applyTheme('system'));
    media.addEventListener('change', handler);
    return () => media.removeEventListener('change', handler);
  }, [themeSetting]);

  const setLanguage = (next: Language) => {
    setLanguageState(next);
    try {
      localStorage.setItem('mini-agent-language', next);
    } catch {
      // ignore
    }
  };
  const setTheme = (next: ThemeSetting) => setThemeSettingState(next);

  const t = useMemo(() => createTranslator(language), [language]);
  const value = useMemo(
    () => ({ language, t, themeSetting, resolvedTheme, setLanguage, setTheme }),
    [language, t, themeSetting, resolvedTheme],
  );

  const shareMatch = /^\/share\/([^/]+)\/?$/.exec(window.location.pathname);
  return (
    <UiContext.Provider value={value}>
      <ErrorBoundary>{shareMatch ? <ShareView token={decodeURIComponent(shareMatch[1])} /> : <App />}</ErrorBoundary>
    </UiContext.Provider>
  );
}
