import { FileText } from 'lucide-react';
import type { Attachment } from '../api/types';
import { useUi } from '../state/UiContext';

export function Attachments({ items }: { items?: Attachment[] }) {
  const { t } = useUi();
  if (!items?.length) return null;
  return (
    <div
      className="message-attachments mt-2 flex flex-wrap gap-2"
      aria-label={t('chat.attachments')}
    >
      {items.map((item) =>
        item.kind === 'image' ? (
          <a
            key={item.id}
            className="block border border-border bg-muted"
            href={item.url}
            target="_blank"
            rel="noreferrer"
            title={item.filename}
          >
            <img
              src={item.url}
              alt={item.filename}
              loading="lazy"
              className="block max-h-64 max-w-[min(320px,70vw)] object-contain"
            />
          </a>
        ) : (
          <a
            key={item.id}
            className="inline-flex items-center gap-2 border border-border bg-background px-2.5 py-1.5 text-[12.5px] text-foreground transition-colors hover:border-border-strong"
            href={item.url}
            target="_blank"
            rel="noreferrer"
          >
            <FileText className="size-3.5 text-muted-foreground" aria-hidden="true" />
            <span className="max-w-56 truncate">{item.filename}</span>
            <span className="font-mono text-[10px] text-muted-foreground">
              {item.kind.toUpperCase()}
            </span>
          </a>
        ),
      )}
    </div>
  );
}
