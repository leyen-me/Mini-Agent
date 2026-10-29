import {
  ArrowUp,
  AtSign,
  FileText,
  Paperclip,
  Slash,
  Square,
  X,
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
import { IconButton } from '@/components/common/IconButton';
import { Segmented } from '@/components/common/Segmented';
import { SimpleSelect } from '@/components/common/SimpleSelect';
import { Button } from '@/components/ui/button';
import { Kbd } from '@/components/ui/kbd';
import { cn } from '@/lib/utils';
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
  const { t } = useUi();
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

  const menuItemClass = (active: boolean) =>
    cn(
      'composer-menu-item flex w-full items-center gap-3 px-3 py-2 text-left text-[13px] transition-colors',
      active ? 'bg-accent text-foreground' : 'text-foreground/80',
    );

  return (
    <div className="composer-shell flex w-full max-w-[760px] flex-col gap-2">
      <QueuePanel items={queue} onUpdate={onUpdateQueue} onDelete={onDeleteQueue} />
      <div
        className={cn(
          'composer relative border border-border-strong bg-background transition-[border-color,box-shadow] duration-200',
          'shadow-[0_1px_2px_rgba(0,0,0,0.04),0_12px_32px_-16px_rgba(0,0,0,0.12)] focus-within:border-foreground',
          dragging && 'border-signal',
        )}
      >
        {menu && (
          <div
            className="composer-menu absolute inset-x-0 bottom-[calc(100%+8px)] z-30 max-h-80 overflow-auto border border-border bg-popover shadow-lg animate-rise"
            ref={menuRef}
            role="listbox"
            aria-label={menu === 'slash' ? t('chat.slash') : t('chat.files')}
          >
            <div className="eyebrow flex items-center gap-2 border-b border-border px-3 py-2">
              {menu === 'slash' ? <Slash className="size-3" /> : <AtSign className="size-3" />}
              {menu === 'slash' ? t('chat.slash') : t('chat.files')}
            </div>
            {menu === 'slash' && (
              <>
                {filteredCommands.length === 0 && (
                  <p className="px-3 py-4 text-center text-[12px] text-muted-foreground">
                    {t('palette.noResults')}
                  </p>
                )}
                {filteredCommands.map((item, index) => (
                  <button
                    type="button"
                    key={item.command}
                    className={menuItemClass(index === menuIndex)}
                    role="option"
                    aria-selected={index === menuIndex}
                    onMouseEnter={() => setMenuIndex(index)}
                    onClick={() => runCommand(item.command, item.custom)}
                  >
                    <span className="shrink-0 font-mono text-[12.5px] text-foreground">
                      {item.command}
                    </span>
                    <span className="truncate text-[12.5px] text-muted-foreground">
                      {item.label}
                    </span>
                  </button>
                ))}
              </>
            )}
            {menu === 'files' && (
              <>
                {!files && (
                  <p className="px-3 py-4 text-center text-[12px] text-muted-foreground">
                    {t('app.loading')}…
                  </p>
                )}
                {files && filteredFiles.length === 0 && (
                  <p className="px-3 py-4 text-center text-[12px] text-muted-foreground">
                    {t('chat.noFiles')}
                  </p>
                )}
                {filteredFiles.slice(0, 30).map((file, index) => (
                  <button
                    type="button"
                    key={file.path}
                    className={menuItemClass(index === menuIndex)}
                    role="option"
                    aria-selected={index === menuIndex}
                    onMouseEnter={() => setMenuIndex(index)}
                    onClick={() => insertFile(file)}
                  >
                    <FileText className="size-3.5 shrink-0 text-muted-foreground" />
                    <span className="truncate font-mono text-[12.5px]">{file.path}</span>
                  </button>
                ))}
              </>
            )}
          </div>
        )}

        {(attachments.length > 0 || uploading > 0) && (
          <div
            className="attachment-strip flex flex-wrap gap-2 border-b border-border px-3 py-3"
            aria-label={t('chat.attachments')}
          >
            {attachments.map((attachment) => (
              <div
                className="attachment-chip group/att relative flex max-w-56 items-center gap-2 border border-border bg-muted py-1 pr-1 pl-1"
                key={attachment.id}
              >
                {attachment.kind === 'image' ? (
                  <button
                    type="button"
                    onClick={() => setPreviewAttachment(attachment)}
                    title={attachment.filename}
                  >
                    <img
                      src={attachment.url}
                      alt={attachment.filename}
                      className="size-9 object-cover"
                    />
                  </button>
                ) : (
                  <a
                    className="flex min-w-0 items-center gap-2 pl-1.5 text-[12px]"
                    href={attachment.url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <FileText className="size-3.5 shrink-0 text-muted-foreground" />
                    <span className="truncate">{attachment.filename}</span>
                  </a>
                )}
                <IconButton
                  size="icon-xs"
                  label={`${t('app.remove')} ${attachment.filename}`}
                  onClick={() => removeAttachment(attachment.id)}
                >
                  <X />
                </IconButton>
              </div>
            ))}
            {uploading > 0 && (
              <div className="thinking-dots self-center" aria-label={t('app.loading')}>
                <span />
                <span />
                <span />
              </div>
            )}
          </div>
        )}

        <textarea
          ref={ref}
          value={value}
          rows={1}
          placeholder={
            busy ? t('chat.composer.placeholderRunning') : t('chat.composer.placeholder')
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
          className="block max-h-64 min-h-[56px] w-full resize-none bg-transparent px-4 pt-4 pb-2 text-[15px] leading-relaxed outline-none placeholder:text-muted-foreground max-sm:text-base"
        />
        {dragging && (
          <div className="pointer-events-none absolute inset-0 z-10 grid place-items-center border-2 border-dashed border-signal bg-signal-soft/90 text-[13px] font-medium text-signal">
            {t('chat.upload')}
          </div>
        )}

        <div className="composer-toolbar flex items-center justify-between gap-2 px-2 pt-1 pb-2">
          <div className="flex min-w-0 items-center gap-0.5">
            <input
              ref={fileInputRef}
              type="file"
              hidden
              multiple
              onChange={onFileInput}
              accept="image/*,.pdf,.md,.json,.yaml,.yml,.py,.ts,.tsx,.js,.css,.html,.txt"
            />
            <IconButton label={t('chat.upload')} side="top" onClick={() => fileInputRef.current?.click()}>
              <Paperclip />
            </IconButton>
            <IconButton
              label={t('chat.files')}
              side="top"
              onClick={() => {
                setValue(
                  (current) => `${current}${current.endsWith(' ') || !current ? '' : ' '}@`,
                );
                setFiles(null);
                ref.current?.focus();
              }}
            >
              <AtSign />
            </IconButton>
            <span className="mx-1.5 h-4 w-px bg-border" aria-hidden="true" />
            <Segmented
              ariaLabel={t('app.agent')}
              value={session?.mode === 'plan' ? 'plan' : 'build'}
              onChange={(mode) => void onModeChange(mode)}
              options={[
                { value: 'build', label: t('chat.mode.build'), title: t('chat.mode.buildHint') },
                { value: 'plan', label: t('chat.mode.plan'), title: t('chat.mode.planHint') },
              ]}
            />
          </div>
          <div className="flex min-w-0 items-center gap-1">
            {busy && (
              <Segmented
                className="max-sm:hidden"
                ariaLabel={t('chat.queue')}
                value={delivery}
                onChange={setDelivery}
                options={[
                  { value: 'queue', label: t('chat.queue'), title: t('chat.queueHint') },
                  { value: 'steer', label: t('chat.steer'), title: t('chat.steerHint') },
                ]}
              />
            )}
            <SimpleSelect
              size="sm"
              ariaLabel={t('chat.model')}
              value={session?.model ?? ''}
              disabled={!session || models.length === 0}
              className="model-select h-8 w-auto max-w-44 border-transparent bg-transparent px-2 text-[12.5px] text-muted-foreground hover:bg-accent hover:text-foreground dark:bg-transparent max-sm:max-w-28"
              onValueChange={(next) => {
                const model = models.find((item) => item.ref === next);
                const reasoning = model?.reasoning_levels.includes(session?.reasoningLevel ?? '')
                  ? (session?.reasoningLevel ?? model.reasoning_levels[0] ?? 'off')
                  : (model?.reasoning_levels[0] ?? 'off');
                void onModelChange(next, reasoning);
              }}
              options={models.map((model) => ({ value: model.ref, label: model.name }))}
            />
            {reasoningLevels.length > 1 && (
              <SimpleSelect
                size="sm"
                ariaLabel={t('chat.reasoning')}
                value={session?.reasoningLevel ?? 'off'}
                disabled={!session}
                className="reasoning-select h-8 w-auto border-transparent bg-transparent px-2 text-[12.5px] text-muted-foreground hover:bg-accent hover:text-foreground dark:bg-transparent max-md:hidden"
                onValueChange={(level) => session && void onModelChange(session.model, level)}
                options={reasoningLevels.map((level) => ({
                  value: level,
                  label: t(`chat.reasoning.${level}`),
                }))}
              />
            )}
            {busy && !value.trim() ? (
              <Button
                size="icon-sm"
                variant="destructive"
                className="ml-1"
                onClick={onStop}
                title={t('chat.stop')}
                aria-label={t('chat.stop')}
              >
                <Square className="fill-current" />
              </Button>
            ) : (
              <Button
                size="icon-sm"
                className="send-button ml-1 transition-transform active:scale-95"
                disabled={
                  (!value.trim() && attachments.length === 0) || !canSend || sending || uploading > 0
                }
                onClick={() => void submit()}
                title={busy ? (delivery === 'steer' ? t('chat.steer') : t('chat.queue')) : t('chat.send')}
                aria-label={
                  busy ? (delivery === 'steer' ? t('chat.steer') : t('chat.queue')) : t('chat.send')
                }
              >
                <ArrowUp />
              </Button>
            )}
          </div>
        </div>
        {disabledReason && (
          <div className="flex items-center justify-between gap-3 border-t border-warning/25 bg-warning-soft px-4 py-2.5 text-[12.5px] text-warning">
            <span>{disabledReason}</span>
            <Button variant="outline" size="xs" onClick={onOpenSettings}>
              {t('chat.noModelCta')}
            </Button>
          </div>
        )}
      </div>
      {!disabledReason && (
        <div className="composer-hint flex flex-wrap items-center justify-center gap-x-3 gap-y-1 px-2 text-[11.5px] text-muted-foreground max-sm:hidden">
          <span className="flex items-center gap-1.5">
            <Kbd>Enter</Kbd> {t('chat.hint.send')}
          </span>
          <span className="flex items-center gap-1.5">
            <Kbd>Shift</Kbd>
            <Kbd>Enter</Kbd> {t('chat.hint.newline')}
          </span>
          <span className="flex items-center gap-1.5">
            <Kbd>/</Kbd> {t('chat.hint.commands')}
          </span>
          <span className="flex items-center gap-1.5">
            <Kbd>@</Kbd> {t('chat.hint.files')}
          </span>
          <span className="text-subtle">{t('chat.draftSaved')}</span>
        </div>
      )}
      {previewAttachment && (
        <div
          className="fixed inset-0 z-[300] grid place-items-center bg-black/85 p-8 animate-in fade-in-0"
          role="dialog"
          aria-modal="true"
          onClick={() => setPreviewAttachment(null)}
        >
          <Button
            variant="ghost"
            size="icon"
            className="absolute top-4 right-4 text-white hover:bg-white/10 hover:text-white"
            onClick={() => setPreviewAttachment(null)}
            aria-label={t('app.close')}
          >
            <X />
          </Button>
          <img
            src={previewAttachment.url}
            alt={previewAttachment.filename}
            className="max-h-[90vh] max-w-[96vw]"
          />
        </div>
      )}
    </div>
  );
}
