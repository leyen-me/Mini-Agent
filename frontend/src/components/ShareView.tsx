import { AlertTriangle, Lock, Moon, Sun } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { Snapshot } from '../api/types';
import { formatDateTime, relativeTime } from '../lib/i18n';
import { useUi } from '../state/UiContext';
import { IconButton } from '@/components/common/IconButton';
import { Skeleton } from '@/components/ui/skeleton';
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
    <div className="share-page flex h-dvh flex-col overflow-y-auto bg-background">
      <header className="sticky top-0 z-10 flex h-14 shrink-0 items-center justify-between gap-3 border-b border-border bg-background/85 px-5 backdrop-blur-md">
        <div className="flex items-center gap-2.5">
          <span
            className="grid size-6 place-items-center bg-foreground font-mono text-[12px] font-semibold text-background"
            aria-hidden="true"
          >
            M
          </span>
          <strong className="text-[14px] font-semibold tracking-tight">{t('app.name')}</strong>
        </div>
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center gap-1.5 border border-border px-2 py-0.5 font-mono text-[10.5px] tracking-wider text-muted-foreground uppercase">
            <Lock className="size-3" aria-hidden="true" /> {t('share.readonly')}
          </span>
          <IconButton
            label={t('app.theme')}
            onClick={() => setTheme(themeSetting === 'dark' ? 'light' : 'dark')}
          >
            {resolvedTheme === 'dark' ? <Sun /> : <Moon />}
          </IconButton>
        </div>
      </header>
      <main className="share-content mx-auto w-full max-w-[760px] px-6 pt-16 pb-24">
        {loading && (
          <div className="flex flex-col gap-3" role="status">
            <Skeleton className="h-8 w-2/3" />
            <Skeleton className="h-3 w-1/3" />
          </div>
        )}
        {error && (
          <div className="flex flex-col items-start gap-3" role="alert">
            <span className="eyebrow flex items-center gap-2 text-destructive">
              <AlertTriangle className="size-3.5" aria-hidden="true" /> {t('share.error')}
            </span>
            <p className="text-[15px] text-muted-foreground">{t('share.errorHint')}</p>
            <p className="font-mono text-[12px] text-muted-foreground">{error}</p>
          </div>
        )}
        {snapshot && (
          <>
            <header className="share-title border-b border-border pb-10">
              <span className="eyebrow">{snapshot.workspace?.name}</span>
              <h1 className="mt-4 text-[clamp(28px,4vw,40px)] leading-tight font-semibold tracking-[-0.035em]">
                {snapshot.session.title}
              </h1>
              <p className="mt-4 font-mono text-[12px] text-muted-foreground">
                {formatDateTime(snapshot.session.createdAt, language)} ·{' '}
                {t('share.messages', { n: snapshot.messages.length })} ·{' '}
                {snapshot.share?.expiresAt
                  ? t('share.expires', {
                      date: formatDateTime(snapshot.share.expiresAt, language),
                    })
                  : t('share.noExpiry')}
              </p>
            </header>
            <div className="share-messages mt-12 flex flex-col gap-10">
              {snapshot.messages.map((message) => {
                if (message.role === 'system') {
                  return (
                    <div
                      className="system-notice flex items-center gap-3 text-[12px] text-muted-foreground"
                      key={message.id}
                    >
                      <span className="h-px flex-1 bg-border" />
                      <span>{message.content.text}</span>
                      <span className="h-px flex-1 bg-border" />
                    </div>
                  );
                }
                if (message.role === 'user') {
                  return (
                    <div className="flex justify-end" key={message.id}>
                      <div className="max-w-[85%] border border-border bg-muted px-4 py-3 text-[15px] leading-relaxed whitespace-pre-wrap">
                        {message.content.text}
                      </div>
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
            <footer className="share-footer mt-20 border-t border-border pt-6 font-mono text-[11px] text-muted-foreground">
              {t('share.footer')} · {relativeTime(snapshot.session.updatedAt, language)}
            </footer>
          </>
        )}
      </main>
    </div>
  );
}
