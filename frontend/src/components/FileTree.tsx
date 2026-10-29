import {
  ChevronDown,
  ChevronRight,
  FileText,
  Folder,
  FolderOpen,
  RefreshCw,
  Search,
} from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import type { FileEntry } from '../api/types';
import { useUi } from '../state/UiContext';
import { IconButton } from '@/components/common/IconButton';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';

interface Props {
  workspaceId: string;
  onReference: (path: string) => void;
}

export function FileTree({ workspaceId, onReference }: Props) {
  const { t } = useUi();
  const [root, setRoot] = useState<FileEntry[] | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [children, setChildren] = useState<Record<string, FileEntry[]>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [query, setQuery] = useState('');
  const [mode, setMode] = useState<'content' | 'name'>('content');
  const [results, setResults] = useState<
    Array<{ path: string; line: number | null; text: string | null }>
  >([]);
  const [searching, setSearching] = useState(false);

  const load = useCallback(
    async (path: string) => {
      setLoading(true);
      setError(false);
      try {
        const listing = await api.files(workspaceId, path);
        if (path === '.') setRoot(listing.entries);
        else
          setChildren((current) => ({ ...current, [path]: listing.entries }));
      } catch {
        setError(true);
      } finally {
        setLoading(false);
      }
    },
    [workspaceId],
  );

  useEffect(() => {
    setRoot(null);
    setChildren({});
    setExpanded(new Set());
    void load('.');
  }, [workspaceId, load]);

  useEffect(() => {
    const needle = query.trim();
    if (!needle) {
      setResults([]);
      return;
    }
    let cancelled = false;
    setSearching(true);
    const timer = window.setTimeout(() => {
      api.workspacesApi
        .search(workspaceId, needle, mode)
        .then((data) => {
          if (!cancelled) setResults(data.results);
        })
        .catch(() => {
          if (!cancelled) setResults([]);
        })
        .finally(() => {
          if (!cancelled) setSearching(false);
        });
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [query, mode, workspaceId]);

  const toggle = async (path: string) => {
    const next = new Set(expanded);
    if (next.has(path)) {
      next.delete(path);
      setExpanded(next);
      return;
    }
    next.add(path);
    setExpanded(next);
    if (!children[path]) await load(path);
  };

  const rowClass =
    'tree-row flex h-7 w-full min-w-0 items-center gap-1.5 pr-3 text-left font-mono text-[12px] text-foreground/80 transition-colors hover:bg-accent hover:text-foreground [&_svg]:size-3.5 [&_svg]:shrink-0 [&_svg]:text-muted-foreground';

  const renderEntries = (entries: FileEntry[], depth = 0) =>
    entries.map((entry) => {
      if (entry.type === 'file') {
        return (
          <button
            key={entry.path}
            type="button"
            className={cn(rowClass, 'tree-file')}
            style={{ paddingLeft: 26 + depth * 14 }}
            title={entry.path}
            onClick={() => onReference(entry.path)}
          >
            <FileText aria-hidden="true" />
            <span className="truncate">{entry.name}</span>
          </button>
        );
      }
      const open = expanded.has(entry.path);
      return (
        <div key={entry.path}>
          <button
            type="button"
            className={cn(rowClass, 'tree-dir')}
            style={{ paddingLeft: 10 + depth * 14 }}
            title={entry.path}
            onClick={() => void toggle(entry.path)}
            aria-expanded={open}
          >
            {open ? <ChevronDown aria-hidden="true" /> : <ChevronRight aria-hidden="true" />}
            {open ? <FolderOpen aria-hidden="true" /> : <Folder aria-hidden="true" />}
            <span className="truncate">{entry.name}</span>
          </button>
          {open &&
            children[entry.path] &&
            renderEntries(children[entry.path], depth + 1)}
        </div>
      );
    });

  const empty = (text: string, tone?: 'error') => (
    <p
      className={cn(
        'px-4 py-6 text-center text-xs text-muted-foreground',
        tone === 'error' && 'text-destructive',
      )}
    >
      {text}
    </p>
  );

  return (
    <div className="file-tree flex min-h-0 flex-1 flex-col">
      <div className="file-search flex items-center gap-2 border-b border-border px-3 py-2.5">
        <div className="relative min-w-0 flex-1">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t('files.search')}
            aria-label={t('files.search')}
            className="h-8 bg-background pl-8 text-[13px]"
          />
        </div>
        <Button
          variant="outline"
          size="sm"
          className="h-8 px-2 font-mono text-[11px]"
          aria-pressed={mode === 'content'}
          onClick={() => setMode(mode === 'content' ? 'name' : 'content')}
          title={mode === 'content' ? t('files.searchContent') : t('files.searchName')}
        >
          {mode === 'content' ? t('files.content') : t('files.name')}
        </Button>
        <IconButton label={t('files.refresh')} onClick={() => void load('.')}>
          <RefreshCw className={cn(loading && 'animate-spin')} />
        </IconButton>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto py-1.5">
        {query.trim() ? (
          <div className="file-search-results">
            {searching && empty(t('files.loading'))}
            {!searching && results.length === 0 && empty(t('files.noResults'))}
            {results.map((result, index) => (
              <button
                key={`${result.path}-${result.line}-${index}`}
                type="button"
                className="search-result flex w-full flex-col items-start gap-0.5 border-b border-border px-4 py-2 text-left transition-colors last:border-b-0 hover:bg-accent"
                title={result.path}
                onClick={() => onReference(result.path)}
              >
                <span className="flex max-w-full items-center gap-1.5 font-mono text-[12px] text-foreground">
                  <FileText className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
                  <span className="truncate">
                    {result.path}
                    {result.line ? `:${result.line}` : ''}
                  </span>
                </span>
                {result.text && (
                  <span className="max-w-full truncate pl-5 font-mono text-[11px] text-muted-foreground">
                    {result.text}
                  </span>
                )}
              </button>
            ))}
          </div>
        ) : (
          <>
            {!root && !error && empty(t('files.loading'))}
            {error && empty(t('app.error'), 'error')}
            {root && root.length === 0 && empty(t('files.empty'))}
            {root && renderEntries(root)}
          </>
        )}
      </div>
      <p className="shrink-0 border-t border-border px-4 py-2.5 text-[11.5px] text-muted-foreground">
        {t('files.insert')}
      </p>
    </div>
  );
}
