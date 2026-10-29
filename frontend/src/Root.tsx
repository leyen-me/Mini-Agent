import { Component, useEffect, useMemo, useState, type ErrorInfo, type ReactNode } from 'react';
import { App } from './App';
import { ShareView } from './components/ShareView';
import { createTranslator, type Language } from './lib/i18n';
import { applyTheme, loadTheme, resolveTheme, saveTheme, type ResolvedTheme, type ThemeSetting } from './lib/theme';
import { UiContext } from './state/UiContext';

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
        <div className="boot-screen">
          <div className="centered-error">
            <h1>界面渲染失败 / UI crashed</h1>
            <p className="muted">{this.state.error.message}</p>
            <button type="button" className="primary-button" onClick={() => window.location.reload()}>
              重新加载 / Reload
            </button>
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
