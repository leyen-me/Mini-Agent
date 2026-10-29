import {
  ArrowUp,
  AtSign,
  Brain,
  ChevronDown,
  FileText,
  Paperclip,
  Send,
  Slash,
  Square,
  Wrench,
} from 'lucide-react';
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type ClipboardEvent,
  type DragEvent,
  type KeyboardEvent,
} from 'react';
import { api } from '../api/client';
import type { FileEntry, PublicConfig, QueueItem, Session } from '../api/types';
import { clearDraft, loadDraft, saveDraft } from '../lib/draft';
import { useUi } from '../state/UiContext';
import { QueuePanel } from './QueuePanel';

interface SlashCommand {
  command: string;
  label: string;
  hint: string;
}

interface Props {
  session: Session | null;
  config: PublicConfig;
  queue: QueueItem[];
  busy: boolean;
  onSend: (content: string, mode: 'auto' | 'steer' | 'queue') => Promise<void>;
  onStop: () => void;
  onModeChange: (mode: 'build' | 'plan') => Promise<void>;
  onModelChange: (model: string, reasoning: string) => Promise<void>;
  onCompact: () => void;
  onExport: () => void;
  onNew: () => void;
  onOpenGoal: () => void;
  onOpenSettings: () => void;
  onShowHelp: () => void;
  onToast: (message: string, kind?: 'info' | 'error' | 'success') => void;
  onUpdateQueue: (id: string, body: { content?: string; mode?: 'steer' | 'queue' }) => Promise<void>;
  onDeleteQueue: (id: string) => Promise<void>;
  disabledReason?: string | null;
  textareaRef?: React.RefObject<HTMLTextAreaElement | null>;
  pendingReference?: { path: string; nonce: number } | null;
  onReferenceConsumed?: () => void;
}

export function Composer({
  session,
  config,
  queue,
  busy,
  onSend,
  onStop,
  onModeChange,
  onModelChange,
  onCompact,
  onExport,
  onNew,
  onOpenGoal,
  onOpenSettings,
  onShowHelp,
  onToast,
  onUpdateQueue,
  onDeleteQueue,
  disabledReason,
  textareaRef,
  pendingReference,
  onReferenceConsumed,
}: Props) {
  const { t, language } = useUi();
  const [value, setValue] = useState('');
  const composingRef = useRef(false);
  const [menu, setMenu] = useState<'slash' | 'files' | null>(null);
  const [menuIndex, setMenuIndex] = useState(0);
  const [files, setFiles] = useState<FileEntry[] | null>(null);
  const [delivery, setDelivery] = useState<'queue' | 'steer'>('queue');
  const [sending, setSending] = useState(false);
  const [dragging, setDragging] = useState(false);
  const localRef = useRef<HTMLTextAreaElement>(null);
  const ref = textareaRef ?? localRef;
  const menuRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const sessionId = session?.id ?? null;

  useEffect(() => {
    setValue(loadDraft(sessionId));
    setMenu(null);
  }, [sessionId]);

  useEffect(() => {
    saveDraft(sessionId, value);
  }, [sessionId, value]);

  useEffect(() => {
    if (!pendingReference) return;
    setValue((current) => `${current}${current && !current.endsWith(' ') ? ' ' : ''}@${pendingReference.path} `);
    onReferenceConsumed?.();
    ref.current?.focus();
  }, [pendingReference, onReferenceConsumed, ref]);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    element.style.height = 'auto';
    element.style.height = `${Math.min(240, element.scrollHeight)}px`;
  }, [value, ref]);

  useEffect(() => {
    if (menu !== 'files' || files !== null || !session) return;
    let cancelled = false;
    api
      .files(session.workspaceId, '.')
      .then((listing) => {
        if (!cancelled) setFiles(listing.entries.filter((entry) => entry.type === 'file'));
      })
      .catch(() => {
        if (!cancelled) setFiles([]);
      });
    return () => {
      cancelled = true;
    };
  }, [menu, files, session]);

  useEffect(() => {
    if (!menu) return;
    const handler = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) setMenu(null);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [menu]);

  const slashCommands: SlashCommand[] = useMemo(
    () => [
      { command: '/compact', label: t('chat.compactNow'), hint: t('chat.compaction') },
      { command: '/export', label: t('chat.exportJson'), hint: t('chat.export') },
      { command: '/new', label: t('app.newSession'), hint: t('palette.newSession') },
      { command: '/model', label: t('chat.model'), hint: t('settings.models.default') },
      { command: '/agent', label: t('chat.mode.plan'), hint: t('chat.mode.build') },
      { command: '/goal', label: t('chat.goal.set'), hint: t('chat.goal.title') },
      { command: '/help', label: t('app.shortcuts'), hint: t('palette.hint') },
    ],
    [t],
  );

  const currentQuery = useMemo(() => {
    if (!value.startsWith('/') || value.includes('\n') || value.includes(' ')) return null;
    return value.slice(1).toLowerCase();
  }, [value]);

  const fileQuery = useMemo(() => {
    const match = /(?:^|\s)@([^\s@]*)$/.exec(value);
    if (!match) return null;
    return match[1].toLowerCase();
  }, [value]);

  useEffect(() => {
    if (currentQuery !== null) {
      setMenu('slash');
      setMenuIndex(0);
    } else if (fileQuery !== null) {
      setMenu('files');
      setMenuIndex(0);
    } else {
      setMenu(null);
    }
  }, [currentQuery, fileQuery]);

  const filteredCommands = slashCommands.filter(
    (item) => !currentQuery || item.command.slice(1).includes(currentQuery),
  );
  const filteredFiles = (files ?? []).filter(
    (file) => !fileQuery || file.path.toLowerCase().includes(fileQuery) || file.name.toLowerCase().includes(fileQuery),
  );
  const canSend = Boolean(session && config.model_configured && !disabledReason);

  const insertFile = (file: FileEntry) => {
    const next = value.replace(/(?:^|\s)@([^\s@]*)$/, (match) => `${match.startsWith(' ') ? ' ' : ''}@${file.path} `);
    setValue(next);
    setMenu(null);
    ref.current?.focus();
  };

  const runCommand = (command: string) => {
    setMenu(null);
    setValue('');
    if (command === '/compact') onCompact();
    else if (command === '/export') onExport();
    else if (command === '/new') onNew();
    else if (command === '/model') onOpenSettings();
    else if (command === '/agent') void onModeChange(session?.mode === 'plan' ? 'build' : 'plan');
    else if (command === '/goal') onOpenGoal();
    else if (command === '/help') onShowHelp();
  };

  const submit = async (mode: 'auto' | 'steer' | 'queue' = 'auto') => {
    const content = value.trim();
    if (!content || !canSend || sending) return;
    setSending(true);
    // Clear before the request: when the first message flips the empty layout, the
    // composer is remounted and must not restore a stale draft while the request is
    // still in flight.
    setValue('');
    clearDraft(sessionId);
    try {
      await onSend(content, busy ? delivery : mode);
    } catch {
      setValue(content);
      saveDraft(sessionId, content);
      // App surfaces the error; restore the draft so the user can retry.
    } finally {
      setSending(false);
    }
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    const native = event.nativeEvent as KeyboardEvent['nativeEvent'] & { isComposing?: boolean; keyCode?: number };
    if (menu) {
      const items = menu === 'slash' ? filteredCommands : filteredFiles;
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        setMenuIndex((index) => Math.min(items.length - 1, index + 1));
        return;
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault();
        setMenuIndex((index) => Math.max(0, index - 1));
        return;
      }
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        if (menu === 'slash' && items[menuIndex]) runCommand((items[menuIndex] as SlashCommand).command);
        if (menu === 'files' && items[menuIndex]) insertFile(items[menuIndex] as FileEntry);
        return;
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        setMenu(null);
        return;
      }
    }
    if (event.key === 'Enter' && !event.shiftKey) {
      if (composingRef.current || native.isComposing || native.keyCode === 229) {
        return;
      }
      event.preventDefault();
      void submit();
    }
  };

  const attachFile = async (file: File) => {
    if (file.size > 1_000_000) {
      onToast(t('chat.attachUnsupported'), 'error');
      return;
    }
    try {
      const text = await file.text();
      if (text.includes('\u0000')) {
        onToast(t('chat.attachUnsupported'), 'error');
        return;
      }
      const block = `\n\n--- ${file.name} ---\n\`\`\`\n${text}\n\`\`\``;
      setValue((current) => current + block);
      onToast(t('chat.attached', { name: file.name }), 'success');
    } catch {
      onToast(t('chat.attachUnsupported'), 'error');
    }
  };

  const onFileInput = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) void attachFile(file);
    event.target.value = '';
  };

  const onPaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    if (Array.from(event.clipboardData.files).some((file) => file.type.startsWith('image/'))) {
      onToast(t('chat.pasteUnsupported'), 'error');
    }
  };

  const onDrop = (event: DragEvent<HTMLTextAreaElement>) => {
    event.preventDefault();
    setDragging(false);
    const file = event.dataTransfer.files?.[0];
    if (file) void attachFile(file);
  };

  const models = config.models;
  const activeModel = models.find((model) => model.ref === session?.model);
  const reasoningLevels = activeModel?.reasoning_levels ?? ['off'];

  return (
    <div className="composer-shell">
      <QueuePanel items={queue} onUpdate={onUpdateQueue} onDelete={onDeleteQueue} />
      <div className={`composer ${dragging ? 'is-dragging' : ''}`}>
        {menu === 'slash' && (
          <div className="composer-menu" ref={menuRef} role="listbox" aria-label={t('chat.slash')}>
            <div className="composer-menu-label">{t('chat.slash')}</div>
            {filteredCommands.length === 0 && <div className="composer-menu-empty">{t('palette.noResults')}</div>}
            {filteredCommands.map((item, index) => (
              <button
                type="button"
                key={item.command}
                className={`composer-menu-item ${index === menuIndex ? 'active' : ''}`}
                role="option"
                aria-selected={index === menuIndex}
                onMouseEnter={() => setMenuIndex(index)}
                onClick={() => runCommand(item.command)}
              >
                <Slash size={14} aria-hidden="true" />
                <span className="menu-command">{item.command}</span>
                <span className="menu-label">{item.label}</span>
              </button>
            ))}
          </div>
        )}
        {menu === 'files' && (
          <div className="composer-menu" ref={menuRef} role="listbox" aria-label={t('chat.files')}>
            <div className="composer-menu-label">
              <AtSign size={13} aria-hidden="true" /> {t('chat.files')}
            </div>
            {!files && <div className="composer-menu-empty">{t('app.loading')}…</div>}
            {files && filteredFiles.length === 0 && (
              <div className="composer-menu-empty">{t('chat.noFiles')}</div>
            )}
            {filteredFiles.slice(0, 30).map((file, index) => (
              <button
                type="button"
                key={file.path}
                className={`composer-menu-item ${index === menuIndex ? 'active' : ''}`}
                role="option"
                aria-selected={index === menuIndex}
                onMouseEnter={() => setMenuIndex(index)}
                onClick={() => insertFile(file)}
              >
                <FileText size={14} aria-hidden="true" />
                <span className="menu-command">{file.path}</span>
              </button>
            ))}
          </div>
        )}
        <textarea
          ref={ref}
          value={value}
          rows={1}
          placeholder={busy ? t('chat.composer.placeholderRunning') : t('chat.composer.placeholder')}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={onKeyDown}
          onCompositionStart={() => {
            composingRef.current = true;
          }}
          onCompositionEnd={() => {
            composingRef.current = false;
          }}
          onPaste={onPaste}
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          aria-label={t('chat.composer.placeholder')}
          data-testid="composer-input"
        />
        {dragging && <div className="drop-overlay">{t('chat.upload')}</div>}
        <div className="composer-toolbar">
          <div className="composer-toolbar-left">
            <input ref={fileInputRef} type="file" hidden onChange={onFileInput} accept="text/*,.md,.json,.yaml,.yml,.py,.ts,.tsx,.js,.css,.html,.txt" />
            <button
              type="button"
              className="icon-button"
              title={t('chat.upload')}
              aria-label={t('chat.upload')}
              onClick={() => fileInputRef.current?.click()}
            >
              <Paperclip size={16} aria-hidden="true" />
            </button>
            <button
              type="button"
              className="icon-button"
              title={t('chat.files')}
              aria-label={t('chat.files')}
              onClick={() => {
                setValue((current) => `${current}${current.endsWith(' ') || !current ? '' : ' '}@`);
                setFiles(null);
                ref.current?.focus();
              }}
            >
              <AtSign size={16} aria-hidden="true" />
            </button>
            <div className="mode-switch" role="group" aria-label={t('app.agent')}>
              <button
                type="button"
                className={session?.mode !== 'plan' ? 'active' : ''}
                title={t('chat.mode.buildHint')}
                onClick={() => void onModeChange('build')}
              >
                <Wrench size={13} aria-hidden="true" /> {t('chat.mode.build')}
              </button>
              <button
                type="button"
                className={session?.mode === 'plan' ? 'active' : ''}
                title={t('chat.mode.planHint')}
                onClick={() => void onModeChange('plan')}
              >
                <Brain size={13} aria-hidden="true" /> {t('chat.mode.plan')}
              </button>
            </div>
          </div>
          <div className="composer-toolbar-right">
            {busy && (
              <div className="delivery-switch" role="group" aria-label={t('chat.queue')}>
                <button
                  type="button"
                  className={delivery === 'queue' ? 'active' : ''}
                  title={t('chat.queueHint')}
                  onClick={() => setDelivery('queue')}
                >
                  {t('chat.queue')}
                </button>
                <button
                  type="button"
                  className={delivery === 'steer' ? 'active' : ''}
                  title={t('chat.steerHint')}
                  onClick={() => setDelivery('steer')}
                >
                  {t('chat.steer')}
                </button>
              </div>
            )}
            <label className="select-label model-select">
              <span className="sr-only">{t('chat.model')}</span>
              <select
                value={session?.model ?? ''}
                disabled={!session || models.length === 0}
                aria-label={t('chat.model')}
                onChange={(event) => {
                  const next = models.find((model) => model.ref === event.target.value);
                  const reasoning = next?.reasoning_levels.includes(session?.reasoningLevel ?? '')
                    ? session?.reasoningLevel ?? next.reasoning_levels[0] ?? 'off'
                    : next?.reasoning_levels[0] ?? 'off';
                  void onModelChange(event.target.value, reasoning);
                }}
              >
                {models.map((model) => (
                  <option key={model.ref} value={model.ref}>
                    {model.name}
                  </option>
                ))}
              </select>
              <ChevronDown size={12} aria-hidden="true" />
            </label>
            <label className="select-label reasoning-select">
              <span className="sr-only">{t('chat.reasoning')}</span>
              <select
                value={session?.reasoningLevel ?? 'off'}
                disabled={!session || reasoningLevels.length <= 1}
                aria-label={t('chat.reasoning')}
                onChange={(event) =>
                  session && void onModelChange(session.model, event.target.value)
                }
              >
                {reasoningLevels.map((level) => (
                  <option key={level} value={level}>
                    {t(`chat.reasoning.${level}`)}
                  </option>
                ))}
              </select>
              <ChevronDown size={12} aria-hidden="true" />
            </label>
            {busy && !value.trim() ? (
              <button
                type="button"
                className="send-button stop-button"
                onClick={onStop}
                title={t('chat.stop')}
                aria-label={t('chat.stop')}
              >
                <Square size={15} aria-hidden="true" />
              </button>
            ) : (
              <button
                type="button"
                className="send-button"
                disabled={!value.trim() || !canSend || sending}
                onClick={() => void submit()}
                title={busy ? (delivery === 'steer' ? t('chat.steer') : t('chat.queue')) : t('chat.send')}
                aria-label={busy ? (delivery === 'steer' ? t('chat.steer') : t('chat.queue')) : t('chat.send')}
              >
                {busy ? <ArrowUp size={16} aria-hidden="true" /> : <Send size={15} aria-hidden="true" />}
              </button>
            )}
          </div>
        </div>
        {disabledReason && (
          <div className="composer-disabled">
            <span>{disabledReason}</span>
            <button type="button" className="text-button" onClick={onOpenSettings}>
              {t('chat.noModelCta')}
            </button>
          </div>
        )}
        {!disabledReason && <div className="composer-hint">{t('chat.draftSaved')} · {language === 'zh-CN' ? 'Enter 发送 / Shift+Enter 换行' : 'Enter to send / Shift+Enter for newline'}</div>}
      </div>
    </div>
  );
}
