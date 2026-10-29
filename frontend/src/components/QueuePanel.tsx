import { ArrowDownUp, Pencil, Trash2 } from 'lucide-react';
import { useState } from 'react';
import type { QueueItem } from '../api/types';
import { useUi } from '../state/UiContext';
import { IconButton } from '@/components/common/IconButton';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';

interface Props {
  items: QueueItem[];
  onUpdate: (id: string, body: { content?: string; mode?: 'steer' | 'queue' }) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
}

export function QueuePanel({ items, onUpdate, onDelete }: Props) {
  const { t } = useUi();
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  if (!items.length) return null;
  return (
    <section
      className="queue-panel border border-border bg-background animate-rise"
      aria-label={t('chat.queueTitle')}
    >
      <header className="flex items-center gap-2 border-b border-border px-3 py-2">
        <span className="eyebrow">{t('chat.queueTitle')}</span>
        <span className="font-mono text-[10.5px] text-muted-foreground tabular-nums">
          {String(items.length).padStart(2, '0')}
        </span>
      </header>
      <ul className="divide-y divide-border">
        {items.map((item) => (
          <li key={item.id} className="group flex items-center gap-3 px-3 py-2">
            {editing === item.id ? (
              <div className="flex w-full flex-col gap-2">
                <Textarea
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  rows={2}
                  className="min-h-0 text-[13px]"
                />
                <div className="flex justify-end gap-2">
                  <Button variant="ghost" size="xs" onClick={() => setEditing(null)}>
                    {t('app.cancel')}
                  </Button>
                  <Button
                    size="xs"
                    disabled={!draft.trim() || busy === item.id}
                    onClick={async () => {
                      setBusy(item.id);
                      try {
                        await onUpdate(item.id, { content: draft.trim() });
                        setEditing(null);
                      } finally {
                        setBusy(null);
                      }
                    }}
                  >
                    {t('app.save')}
                  </Button>
                </div>
              </div>
            ) : (
              <>
                <button
                  type="button"
                  className={cn(
                    'inline-flex h-6 shrink-0 items-center gap-1 border px-1.5 font-mono text-[10.5px] tracking-wide uppercase transition-colors',
                    item.mode === 'steer'
                      ? 'border-signal/40 bg-signal-soft text-signal'
                      : 'border-border text-muted-foreground hover:text-foreground',
                  )}
                  title={item.mode === 'steer' ? t('chat.steerHint') : t('chat.queueHint')}
                  onClick={() =>
                    void onUpdate(item.id, { mode: item.mode === 'steer' ? 'queue' : 'steer' })
                  }
                >
                  <ArrowDownUp className="size-3" aria-hidden="true" />
                  {item.mode === 'steer' ? t('chat.steer') : t('chat.queue')}
                </button>
                <span className="min-w-0 flex-1 truncate text-[13px] text-foreground/80">
                  {item.content}
                </span>
                <div className="flex opacity-60 transition-opacity group-hover:opacity-100">
                  <IconButton
                    size="icon-xs"
                    label={t('app.edit')}
                    onClick={() => {
                      setEditing(item.id);
                      setDraft(item.content);
                    }}
                  >
                    <Pencil />
                  </IconButton>
                  <IconButton
                    size="icon-xs"
                    label={t('app.delete')}
                    className="hover:bg-danger-soft hover:text-destructive"
                    onClick={() => void onDelete(item.id)}
                  >
                    <Trash2 />
                  </IconButton>
                </div>
              </>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
