import { FileText } from 'lucide-react';
import type { Attachment } from '../api/types';
import { useUi } from '../state/UiContext';

export function Attachments({ items }: { items?: Attachment[] }) {
  const { t } = useUi();
  if (!items?.length) return null;
  return (
    <div className="message-attachments" aria-label={t('chat.attachments')}>
      {items.map((item) =>
        item.kind === 'image' ? (
          <a
            key={item.id}
            className="message-image"
            href={item.url}
            target="_blank"
            rel="noreferrer"
            title={item.filename}
          >
            <img src={item.url} alt={item.filename} loading="lazy" />
          </a>
        ) : (
          <a
            key={item.id}
            className="message-file"
            href={item.url}
            target="_blank"
            rel="noreferrer"
          >
            <FileText size={15} aria-hidden="true" />
            <span>{item.filename}</span>
            <span className="message-file-kind">{item.kind.toUpperCase()}</span>
          </a>
        ),
      )}
    </div>
  );
}
