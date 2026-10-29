import { AlertTriangle, Lock, Moon, Sun } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { Snapshot } from '../api/types';
import { formatDateTime, relativeTime } from '../lib/i18n';
import { useUi } from '../state/UiContext';
import { Markdown } from './Markdown';

interface SharedSnapshot extends Snapshot {
  share: { expiresAt: string | null; createdAt: string };
}

export function ShareView({ token }: { token: string }) {
  const { t, language, themeSetting, resolvedTheme, setTheme } = useUi();
  const [snapshot, setSnapshot] = useState<SharedSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    api.shares
      .get(token)
      .then((value) => {
        if (!cancelled) setSnapshot(value);
      })
      .catch((caught) => {
        if (!cancelled) setError((caught as Error).message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  return (
    <div className="share-page">
      <header className="share-header">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">M</div>
          <div className="brand-text">
            <strong>{t('app.name')}</strong>
            <span>{t('share.readonly')}</span>
          </div>
        </div>
        <div className="inline-actions">
          <span className="readonly-chip"><Lock size={12} aria-hidden="true" /> {t('share.readonly')}</span>
          <button
            type="button"
            className="icon-button"
            onClick={() => setTheme(themeSetting === 'dark' ? 'light' : 'dark')}
            aria-label={t('app.theme')}
          >
            {resolvedTheme === 'dark' ? <Sun size={17} aria-hidden="true" /> : <Moon size={17} aria-hidden="true" />}
          </button>
        </div>
      </header>
      <main className="share-content">
        {loading && <div className="loading-screen"><div className="skeleton wide" /><div className="skeleton" /></div>}
        {error && (
          <div className="centered-error">
            <AlertTriangle size={28} aria-hidden="true" />
            <h1>{t('share.error')}</h1>
            <p className="muted">{t('share.errorHint')}</p>
            <p className="muted small">{error}</p>
          </div>
        )}
        {snapshot && (
          <>
            <header className="share-title">
              <h1>{snapshot.session.title}</h1>
              <p className="muted">
                {snapshot.workspace?.name} · {formatDateTime(snapshot.session.createdAt, language)} ·{' '}
                {t('share.messages', { n: snapshot.messages.length })}
              </p>
              <p className="muted small">
                {snapshot.share?.expiresAt ? t('share.expires', { date: formatDateTime(snapshot.share.expiresAt, language) }) : t('share.noExpiry')}
              </p>
            </header>
            <div className="share-messages">
              {snapshot.messages.map((message) => {
                if (message.role === 'system') {
                  return <div className="system-notice" key={message.id}><span>{message.content.text}</span></div>;
                }
                if (message.role === 'user') {
                  return (
                    <div className="message-row user-row" key={message.id}>
                      <div className="message-bubble user-bubble">{message.content.text}</div>
                    </div>
                  );
                }
                if (message.role === 'assistant' && message.content.text) {
                  return (
                    <div className="assistant-panel" key={message.id}>
                      <Markdown>{message.content.text}</Markdown>
                    </div>
                  );
                }
                return null;
              })}
            </div>
            <footer className="share-footer">
              {t('share.footer')} · {relativeTime(snapshot.session.updatedAt, language)}
            </footer>
          </>
        )}
      </main>
    </div>
  );
}

