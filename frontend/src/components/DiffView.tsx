import { useMemo } from 'react';
import { useUi } from '../state/UiContext';

export function DiffView({ diff }: { diff: string }) {
  const { t } = useUi();
  const lines = useMemo(() => diff.split('\n').slice(0, 500), [diff]);
  return (
    <div className="diff-view" role="region" aria-label={t('chat.diff')}>
      {lines.map((line, index) => {
        const kind =
          line.startsWith('+++') || line.startsWith('---')
            ? 'meta'
            : line.startsWith('+')
              ? 'add'
              : line.startsWith('-')
                ? 'remove'
                : line.startsWith('@@')
                  ? 'hunk'
                  : 'context';
        return (
          <div
            className={`diff-line diff-${kind}`}
            key={`${index}-${line.slice(0, 8)}`}
          >
            {line || ' '}
          </div>
        );
      })}
    </div>
  );
}
