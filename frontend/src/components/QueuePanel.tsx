import { ArrowDownUp, Pencil, Trash2 } from 'lucide-react';
import { useState } from 'react';
import type { QueueItem } from '../api/types';
import { useUi } from '../state/UiContext';

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
    <section className="queue-panel" aria-label={t('chat.queueTitle')}>
      <header>
        <span>{t('chat.queueTitle')}</span>
        <span className="count">{items.length}</span>
      </header>
      <ul>
        {items.map((item) => (
          <li key={item.id}>
            {editing === item.id ? (
              <div className="queue-editor">
                <textarea value={draft} onChange={(event) => setDraft(event.target.value)} rows={2} />
                <div className="inline-actions">
                  <button type="button" className="text-button" onClick={() => setEditing(null)}>
                    {t('app.cancel')}
                  </button>
                  <button
                    type="button"
                    className="primary-button small"
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
                  </button>
                </div>
              </div>
            ) : (
              <>
                <button
                  type="button"
                  className={`queue-mode mode-${item.mode}`}
                  title={item.mode === 'steer' ? t('chat.steerHint') : t('chat.queueHint')}
                  onClick={() => void onUpdate(item.id, { mode: item.mode === 'steer' ? 'queue' : 'steer' })}
                >
                  <ArrowDownUp size={12} aria-hidden="true" />
                  {item.mode === 'steer' ? t('chat.steer') : t('chat.queue')}
                </button>
                <span className="queue-content">{item.content}</span>
                <div className="queue-actions">
                  <button
                    type="button"
                    className="icon-button"
                    aria-label={t('app.edit')}
                    title={t('app.edit')}
                    onClick={() => {
                      setEditing(item.id);
                      setDraft(item.content);
                    }}
                  >
                    <Pencil size={13} aria-hidden="true" />
                  </button>
                  <button
                    type="button"
                    className="icon-button danger"
                    aria-label={t('app.delete')}
                    title={t('app.delete')}
                    onClick={() => void onDelete(item.id)}
                  >
                    <Trash2 size={13} aria-hidden="true" />
                  </button>
                </div>
              </>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
