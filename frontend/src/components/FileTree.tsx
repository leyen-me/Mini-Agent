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

  const renderEntries = (entries: FileEntry[], depth = 0) =>
    entries.map((entry) => {
      if (entry.type === 'file') {
        return (
          <button
            key={entry.path}
            type="button"
            className="tree-row tree-file"
            style={{ paddingLeft: 10 + depth * 14 }}
            title={entry.path}
            onClick={() => onReference(entry.path)}
          >
            <FileText size={13} aria-hidden="true" />
            <span>{entry.name}</span>
          </button>
        );
      }
      const open = expanded.has(entry.path);
      return (
        <div key={entry.path}>
          <button
            type="button"
            className="tree-row tree-dir"
            style={{ paddingLeft: 10 + depth * 14 }}
            title={entry.path}
            onClick={() => void toggle(entry.path)}
            aria-expanded={open}
          >
            {open ? (
              <ChevronDown size={12} aria-hidden="true" />
            ) : (
              <ChevronRight size={12} aria-hidden="true" />
            )}
            {open ? (
              <FolderOpen size={13} aria-hidden="true" />
            ) : (
              <Folder size={13} aria-hidden="true" />
            )}
            <span>{entry.name}</span>
          </button>
          {open &&
            children[entry.path] &&
            renderEntries(children[entry.path], depth + 1)}
        </div>
      );
    });

  return (
    <div className="file-tree">
      <div className="file-tree-header">
        <span>{t('files.title')}</span>
        <button
          type="button"
          className="icon-button"
          title={t('files.refresh')}
          aria-label={t('files.refresh')}
          onClick={() => void load('.')}
        >
          <RefreshCw
            size={13}
            aria-hidden="true"
            className={loading ? 'spin' : undefined}
          />
        </button>
      </div>
      <div className="file-search">
        <Search size={13} aria-hidden="true" />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={t('files.search')}
          aria-label={t('files.search')}
        />
        <button
          type="button"
          className={mode === 'content' ? 'active' : ''}
          onClick={() => setMode(mode === 'content' ? 'name' : 'content')}
          title={
            mode === 'content'
              ? t('files.searchContent')
              : t('files.searchName')
          }
        >
          {mode === 'content' ? t('files.content') : t('files.name')}
        </button>
      </div>
      {query.trim() ? (
        <div className="file-search-results">
          {searching && <div className="tree-empty">{t('files.loading')}</div>}
          {!searching && results.length === 0 && (
            <div className="tree-empty">{t('files.noResults')}</div>
          )}
          {results.map((result, index) => (
            <button
              key={`${result.path}-${result.line}-${index}`}
              type="button"
              className="tree-row search-result"
              title={result.path}
              onClick={() => onReference(result.path)}
            >
              <FileText size={13} aria-hidden="true" />
              <span className="search-result-path">
                {result.path}
                {result.line ? `:${result.line}` : ''}
              </span>
              {result.text && (
                <span className="search-result-text">{result.text}</span>
              )}
            </button>
          ))}
        </div>
      ) : (
        <>
          {!root && <div className="tree-empty">{t('files.loading')}</div>}
          {error && <div className="tree-empty error">{t('app.error')}</div>}
          {root && root.length === 0 && (
            <div className="tree-empty">{t('files.empty')}</div>
          )}
          {root && renderEntries(root)}
        </>
      )}
      <p className="tree-hint">{t('files.insert')}</p>
    </div>
  );
}
