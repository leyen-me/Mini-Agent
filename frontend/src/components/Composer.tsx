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
import type {
  Attachment,
  CommandInfo,
  FileEntry,
  PublicConfig,
  QueueItem,
  Session,
} from '../api/types';
import { clearDraft, loadDraft, saveDraft } from '../lib/draft';
import { useUi } from '../state/UiContext';
import { QueuePanel } from './QueuePanel';

interface SlashCommand {
  command: string;
  label: string;
  hint: string;
  custom?: CommandInfo;
}

interface Props {
  session: Session | null;
  config: PublicConfig;
  queue: QueueItem[];
  busy: boolean;
  onSend: (
    content: string,
    mode: 'auto' | 'steer' | 'queue',
    attachmentIds?: string[],
  ) => Promise<void>;
  commands?: CommandInfo[];
  onPreviewCommand?: (command: CommandInfo, initialArgs?: string) => void;
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
  onUpdateQueue: (
    id: string,
    body: { content?: string; mode?: 'steer' | 'queue' },
  ) => Promise<void>;
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
  commands = [],
  onPreviewCommand,
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
  const [fileMatches, setFileMatches] = useState<FileEntry[] | null>(null);
  const [delivery, setDelivery] = useState<'queue' | 'steer'>('queue');
  const [sending, setSending] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [uploading, setUploading] = useState(0);
  const [previewAttachment, setPreviewAttachment] = useState<Attachment | null>(
    null,
  );
  const localRef = useRef<HTMLTextAreaElement>(null);
  const ref = textareaRef ?? localRef;
  const menuRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const sessionId = session?.id ?? null;

  useEffect(() => {
    setValue(loadDraft(sessionId));
    setAttachments([]);
    setMenu(null);
  }, [sessionId]);

  useEffect(() => {
    saveDraft(sessionId, value);
  }, [sessionId, value]);

  useEffect(() => {
    if (!pendingReference) return;
    setValue(
      (current) =>
        `${current}${current && !current.endsWith(' ') ? ' ' : ''}@${pendingReference.path} `,
    );
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
        if (!cancelled)
          setFiles(listing.entries.filter((entry) => entry.type === 'file'));
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
      if (menuRef.current && !menuRef.current.contains(event.target as Node))
        setMenu(null);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [menu]);

  const slashCommands: SlashCommand[] = useMemo(
    () => [
      {
        command: '/compact',
        label: t('chat.compactNow'),
        hint: t('chat.compaction'),
      },
      {
        command: '/export',
        label: t('chat.exportJson'),
        hint: t('chat.export'),
      },
      {
        command: '/new',
        label: t('app.newSession'),
        hint: t('palette.newSession'),
      },
      {
        command: '/model',
        label: t('chat.model'),
        hint: t('settings.models.default'),
      },
      {
        command: '/agent',
        label: t('chat.mode.plan'),
        hint: t('chat.mode.build'),
      },
      {
        command: '/goal',
        label: t('chat.goal.set'),
        hint: t('chat.goal.title'),
      },
      { command: '/help', label: t('app.shortcuts'), hint: t('palette.hint') },
      ...commands.map((item) => ({
        command: `/${item.name}`,
        label: item.description || item.name,
        hint: item.scope.startsWith('plugin:')
          ? item.scope
          : t('chat.customCommand'),
        custom: item,
      })),
    ],
    [commands, t],
  );

  const currentQuery = useMemo(() => {
    if (!value.startsWith('/') || value.includes('\n') || value.includes(' '))
      return null;
    return value.slice(1).toLowerCase();
  }, [value]);

  const fileQuery = useMemo(() => {
    const match = /(?:^|\s)@([^\s@]*)$/.exec(value);
    if (!match) return null;
    return match[1].toLowerCase();
  }, [value]);

  useEffect(() => {
    if (!fileQuery || !session) {
      setFileMatches(null);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      api.workspacesApi
        .search(session.workspaceId, fileQuery, 'name')
        .then((result) => {
          if (cancelled) return;
          setFileMatches(
            result.results.slice(0, 50).map((item) => ({
              name: item.path.split('/').pop() ?? item.path,
              path: item.path,
              type: 'file' as const,
              size: null,
            })),
          );
        })
        .catch(() => {
          if (!cancelled) setFileMatches([]);
        });
    }, 180);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [fileQuery, session]);

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
  const filteredFiles = fileQuery
    ? (fileMatches ?? [])
    : (files ?? []).filter(
        (file) =>
          !fileQuery ||
          file.path.toLowerCase().includes(fileQuery) ||
          file.name.toLowerCase().includes(fileQuery),
      );
  const canSend = Boolean(
    session && config.model_configured && !disabledReason,
  );

  const insertFile = (file: FileEntry) => {
    const next = value.replace(
      /(?:^|\s)@([^\s@]*)$/,
      (match) => `${match.startsWith(' ') ? ' ' : ''}@${file.path} `,
    );
    setValue(next);
    setMenu(null);
    ref.current?.focus();
  };

  const runCommand = (command: string, custom?: CommandInfo) => {
    setMenu(null);
    if (custom) {
      setValue('');
      onPreviewCommand?.(custom);
      return;
    }
    setValue('');
    if (command === '/compact') onCompact();
    else if (command === '/export') onExport();
    else if (command === '/new') onNew();
    else if (command === '/model') onOpenSettings();
    else if (command === '/agent')
      void onModeChange(session?.mode === 'plan' ? 'build' : 'plan');
    else if (command === '/goal') onOpenGoal();
    else if (command === '/help') onShowHelp();
  };

  const submit = async (mode: 'auto' | 'steer' | 'queue' = 'auto') => {
    const content = value.trim();
    if ((!content && attachments.length === 0) || !canSend || sending) return;
    if (content.startsWith('/')) {
      const command = commands.find(
        (item) =>
          content === `/${item.name}` || content.startsWith(`/${item.name} `),
      );
      if (command) {
        onPreviewCommand?.(
          command,
          content.slice(command.name.length + 1).trim(),
        );
        return;
      }
    }
    setSending(true);
    // Clear before the request: when the first message flips the empty layout, the
    // composer is remounted and must not restore a stale draft while the request is
    // still in flight.
    setValue('');
    clearDraft(sessionId);
    try {
      if (attachments.length) {
        await onSend(
          content,
          busy ? delivery : mode,
          attachments.map((item) => item.id),
        );
      } else {
        await onSend(content, busy ? delivery : mode);
      }
      setAttachments([]);
    } catch {
      setValue(content);
      saveDraft(sessionId, content);
      // App surfaces the error; restore the draft so the user can retry.
    } finally {
      setSending(false);
    }
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    const native = event.nativeEvent as KeyboardEvent['nativeEvent'] & {
      isComposing?: boolean;
      keyCode?: number;
    };
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
        if (menu === 'slash' && items[menuIndex]) {
          const item = items[menuIndex] as SlashCommand;
          runCommand(item.command, item.custom);
        }
        if (menu === 'files' && items[menuIndex])
          insertFile(items[menuIndex] as FileEntry);
        return;
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        setMenu(null);
        return;
      }
    }
    if (event.key === 'Enter' && !event.shiftKey) {
      if (
        composingRef.current ||
        native.isComposing ||
        native.keyCode === 229
      ) {
        return;
      }
      event.preventDefault();
      void submit();
    }
  };

  const uploadFiles = async (files: File[]) => {
    if (!files.length) return;
    setUploading((count) => count + files.length);
    const uploaded: Attachment[] = [];
    for (const file of files) {
      try {
        const attachment = await api.upload(file, sessionId ?? undefined);
        uploaded.push(attachment);
      } catch (error) {
        onToast(
          error instanceof Error ? error.message : t('chat.attachUnsupported'),
          'error',
        );
      } finally {
        setUploading((count) => Math.max(0, count - 1));
      }
    }
    if (uploaded.length) {
      setAttachments((current) => [...current, ...uploaded]);
      onToast(
        t('chat.attached', {
          name: uploaded.map((item) => item.filename).join(', '),
        }),
        'success',
      );
    }
  };

  const onFileInput = (event: ChangeEvent<HTMLInputElement>) => {
    void uploadFiles(Array.from(event.target.files ?? []));
    event.target.value = '';
  };

  const onPaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const files = Array.from(event.clipboardData.files);
    if (files.length) {
      event.preventDefault();
      void uploadFiles(files);
    }
  };

  const onDrop = (event: DragEvent<HTMLTextAreaElement>) => {
    event.preventDefault();
    setDragging(false);
    void uploadFiles(Array.from(event.dataTransfer.files ?? []));
  };

  const removeAttachment = (id: string) => {
    setAttachments((current) => current.filter((item) => item.id !== id));
  };

  const models = config.models;
  const activeModel = models.find((model) => model.ref === session?.model);
  const reasoningLevels = activeModel?.reasoning_levels ?? ['off'];

  return (
    <div className="composer-shell">
      <QueuePanel
        items={queue}
        onUpdate={onUpdateQueue}
        onDelete={onDeleteQueue}
      />
      <div className={`composer ${dragging ? 'is-dragging' : ''}`}>
        {menu === 'slash' && (
          <div
            className="composer-menu"
            ref={menuRef}
            role="listbox"
            aria-label={t('chat.slash')}
          >
            <div className="composer-menu-label">{t('chat.slash')}</div>
            {filteredCommands.length === 0 && (
              <div className="composer-menu-empty">
                {t('palette.noResults')}
              </div>
            )}
            {filteredCommands.map((item, index) => (
              <button
                type="button"
                key={item.command}
                className={`composer-menu-item ${index === menuIndex ? 'active' : ''}`}
                role="option"
                aria-selected={index === menuIndex}
                onMouseEnter={() => setMenuIndex(index)}
                onClick={() => runCommand(item.command, item.custom)}
              >
                <Slash size={14} aria-hidden="true" />
                <span className="menu-command">{item.command}</span>
                <span className="menu-label">{item.label}</span>
              </button>
            ))}
          </div>
        )}
        {menu === 'files' && (
          <div
            className="composer-menu"
            ref={menuRef}
            role="listbox"
            aria-label={t('chat.files')}
          >
            <div className="composer-menu-label">
              <AtSign size={13} aria-hidden="true" /> {t('chat.files')}
            </div>
            {!files && (
              <div className="composer-menu-empty">{t('app.loading')}…</div>
            )}
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
        {(attachments.length > 0 || uploading > 0) && (
          <div className="attachment-strip" aria-label={t('chat.attachments')}>
            {attachments.map((attachment) => (
              <div className="attachment-chip" key={attachment.id}>
                {attachment.kind === 'image' ? (
                  <button
                    type="button"
                    className="attachment-thumb"
                    onClick={() => setPreviewAttachment(attachment)}
                    title={attachment.filename}
                  >
                    <img src={attachment.url} alt={attachment.filename} />
                  </button>
                ) : (
                  <a
                    className="attachment-file"
                    href={attachment.url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <FileText size={15} aria-hidden="true" />
                    <span>{attachment.filename}</span>
                  </a>
                )}
                <button
                  type="button"
                  className="attachment-remove"
                  onClick={() => removeAttachment(attachment.id)}
                  aria-label={`${t('app.remove')} ${attachment.filename}`}
                >
                  ×
                </button>
              </div>
            ))}
            {uploading > 0 && <div className="attachment-uploading">…</div>}
          </div>
        )}
        <textarea
          ref={ref}
          value={value}
          rows={1}
          placeholder={
            busy
              ? t('chat.composer.placeholderRunning')
              : t('chat.composer.placeholder')
          }
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
            <input
              ref={fileInputRef}
              type="file"
              hidden
              multiple
              onChange={onFileInput}
              accept="image/*,.pdf,.md,.json,.yaml,.yml,.py,.ts,.tsx,.js,.css,.html,.txt"
            />
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
                setValue(
                  (current) =>
                    `${current}${current.endsWith(' ') || !current ? '' : ' '}@`,
                );
                setFiles(null);
                ref.current?.focus();
              }}
            >
              <AtSign size={16} aria-hidden="true" />
            </button>
            <div
              className="mode-switch"
              role="group"
              aria-label={t('app.agent')}
            >
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
              <div
                className="delivery-switch"
                role="group"
                aria-label={t('chat.queue')}
              >
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
                  const next = models.find(
                    (model) => model.ref === event.target.value,
                  );
                  const reasoning = next?.reasoning_levels.includes(
                    session?.reasoningLevel ?? '',
                  )
                    ? (session?.reasoningLevel ??
                      next.reasoning_levels[0] ??
                      'off')
                    : (next?.reasoning_levels[0] ?? 'off');
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
                  session &&
                  void onModelChange(session.model, event.target.value)
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
                disabled={
                  (!value.trim() && attachments.length === 0) ||
                  !canSend ||
                  sending ||
                  uploading > 0
                }
                onClick={() => void submit()}
                title={
                  busy
                    ? delivery === 'steer'
                      ? t('chat.steer')
                      : t('chat.queue')
                    : t('chat.send')
                }
                aria-label={
                  busy
                    ? delivery === 'steer'
                      ? t('chat.steer')
                      : t('chat.queue')
                    : t('chat.send')
                }
              >
                {busy ? (
                  <ArrowUp size={16} aria-hidden="true" />
                ) : (
                  <Send size={15} aria-hidden="true" />
                )}
              </button>
            )}
          </div>
        </div>
        {disabledReason && (
          <div className="composer-disabled">
            <span>{disabledReason}</span>
            <button
              type="button"
              className="text-button"
              onClick={onOpenSettings}
            >
              {t('chat.noModelCta')}
            </button>
          </div>
        )}
        {!disabledReason && (
          <div className="composer-hint">
            {t('chat.draftSaved')} ·{' '}
            {language === 'zh-CN'
              ? 'Enter 发送 / Shift+Enter 换行'
              : 'Enter to send / Shift+Enter for newline'}
          </div>
        )}
      </div>
      {previewAttachment && (
        <div
          className="image-preview-backdrop"
          role="dialog"
          aria-modal="true"
          onClick={() => setPreviewAttachment(null)}
        >
          <button
            type="button"
            className="image-preview-close"
            onClick={() => setPreviewAttachment(null)}
            aria-label={t('app.close')}
          >
            ×
          </button>
          <img src={previewAttachment.url} alt={previewAttachment.filename} />
        </div>
      )}
    </div>
  );
}
