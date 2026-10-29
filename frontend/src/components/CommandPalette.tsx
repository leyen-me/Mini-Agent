import {
  Archive,
  Download,
  Moon,
  Plus,
  Search,
  Settings,
  Square,
  TerminalSquare,
  Play,
  MessageSquare,
  Target,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import type { Session } from '../api/types';
import { useUi } from '../state/UiContext';

interface Props {
  open: boolean;
  sessions: Session[];
  onClose: () => void;
  onSelectSession: (id: string) => void;
  onNewSession: () => void;
  onOpenSettings: () => void;
  onToggleTheme: () => void;
  onStop: () => void;
  onResume: () => void;
  onCompact: () => void;
  onExport: () => void;
  onFocusComposer: () => void;
}

interface Item {
  id: string;
  label: string;
  group: string;
  icon: typeof Search;
  run: () => void;
}

export function CommandPalette({
  open,
  sessions,
  onClose,
  onSelectSession,
  onNewSession,
  onOpenSettings,
  onToggleTheme,
  onStop,
  onResume,
  onCompact,
  onExport,
  onFocusComposer,
}: Props) {
  const { t } = useUi();
  const [query, setQuery] = useState('');
  const [index, setIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setQuery('');
      setIndex(0);
      window.setTimeout(() => inputRef.current?.focus(), 30);
    }
  }, [open]);

  const actions: Item[] = useMemo(
    () => [
      { id: 'new', label: t('palette.newSession'), group: t('palette.actions'), icon: Plus, run: onNewSession },
      { id: 'settings', label: t('palette.openSettings'), group: t('palette.actions'), icon: Settings, run: onOpenSettings },
      { id: 'theme', label: t('palette.toggleTheme'), group: t('palette.actions'), icon: Moon, run: onToggleTheme },
      { id: 'stop', label: t('palette.stop'), group: t('palette.actions'), icon: Square, run: onStop },
      { id: 'resume', label: t('palette.resume'), group: t('palette.actions'), icon: Play, run: onResume },
      { id: 'compact', label: t('palette.compact'), group: t('palette.actions'), icon: Archive, run: onCompact },
      { id: 'export', label: t('palette.exportJson'), group: t('palette.actions'), icon: Download, run: onExport },
      { id: 'focus', label: t('palette.focusComposer'), group: t('palette.navigation'), icon: TerminalSquare, run: onFocusComposer },
    ],
    [t, onNewSession, onOpenSettings, onToggleTheme, onStop, onResume, onCompact, onExport, onFocusComposer],
  );

  const items = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    const actionItems = actions.filter(
      (item) => !normalized || item.label.toLowerCase().includes(normalized) || item.id.includes(normalized),
    );
    const sessionItems: Item[] = sessions
      .filter((session) => !normalized || session.title.toLowerCase().includes(normalized))
      .slice(0, 8)
      .map((session) => ({
        id: `session-${session.id}`,
        label: session.title || session.id,
        group: t('palette.sessions'),
        icon: MessageSquare,
        run: () => onSelectSession(session.id),
      }));
    return [...actionItems, ...sessionItems];
  }, [actions, query, sessions, t, onSelectSession]);

  useEffect(() => {
    setIndex(0);
  }, [query]);

  if (!open) return null;
  const grouped = items.reduce<Record<string, Item[]>>((result, item) => {
    (result[item.group] ??= []).push(item);
    return result;
  }, {});
  const execute = (item: Item | undefined) => {
    if (!item) return;
    item.run();
    onClose();
  };
  return (
    <div className="palette-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="palette" role="dialog" aria-modal="true" aria-label={t('app.commandPalette')}>
        <div className="palette-search">
          <Search size={17} aria-hidden="true" />
          <input
            ref={inputRef}
            value={query}
            placeholder={t('palette.placeholder')}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'ArrowDown') {
                event.preventDefault();
                setIndex((value) => Math.min(items.length - 1, value + 1));
              } else if (event.key === 'ArrowUp') {
                event.preventDefault();
                setIndex((value) => Math.max(0, value - 1));
              } else if (event.key === 'Enter') {
                event.preventDefault();
                execute(items[index]);
              } else if (event.key === 'Escape') {
                onClose();
              }
            }}
            aria-label={t('palette.placeholder')}
          />
          <kbd>Esc</kbd>
        </div>
        <div className="palette-list" role="listbox">
          {items.length === 0 && <div className="palette-empty">{t('palette.noResults')}</div>}
          {Object.entries(grouped).map(([group, groupItems]) => (
            <div key={group}>
              <div className="palette-group">{group}</div>
              {groupItems.map((item) => {
                const Icon = item.icon;
                const active = items.indexOf(item) === index;
                return (
                  <button
                    type="button"
                    key={item.id}
                    role="option"
                    aria-selected={active}
                    className={`palette-item ${active ? 'active' : ''}`}
                    onMouseEnter={() => setIndex(items.indexOf(item))}
                    onClick={() => execute(item)}
                  >
                    <Icon size={15} aria-hidden="true" />
                    <span>{item.label}</span>
                    {active && <span className="palette-enter">↵</span>}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
        <footer className="palette-footer">
          <span>{t('palette.hint')}</span>
          <span><Target size={12} aria-hidden="true" /> Mini Agent</span>
        </footer>
      </div>
    </div>
  );
}
