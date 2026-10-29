import {
  AlertTriangle,
  CheckCircle2,
  Info,
  Plus,
  Settings,
  X,
} from 'lucide-react';
import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react';
import { api, errorMessage } from './api/client';
import type {
  AgentInfo,
  BootstrapData,
  ChatMessage,
  CommandInfo,
  Session,
  Snapshot,
  StreamEvent,
  StreamStatus,
} from './api/types';
import { ChatView } from './components/ChatView';
import type { AdminTab } from './components/AdminDialog';
import { CommandPalette } from './components/CommandPalette';
import { CommandPreview } from './components/CommandPreview';

const AdminDialog = lazy(() =>
  import('./components/AdminDialog').then((module) => ({
    default: module.AdminDialog,
  })),
);
import { Composer } from './components/Composer';
import { GoalDialog } from './components/GoalDialog';
import { Modal } from './components/Modal';
import { SettingsDialog } from './components/SettingsDialog';
import { Sidebar } from './components/Sidebar';
import { useSessionStream } from './hooks/useSessionStream';
import { copyText } from './lib/clipboard';
import { newClientId } from './lib/ids';
import type { Language } from './lib/i18n';
import type { ThemeSetting } from './lib/theme';
import { applyEvent } from './lib/events';
import { useUi } from './state/UiContext';

interface Toast {
  id: number;
  message: string;
  kind: 'info' | 'error' | 'success';
}

type SettingsTab =
  'general' | 'models' | 'agents' | 'permissions' | 'data' | 'about';

function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() =>
    typeof window !== 'undefined' ? window.matchMedia(query).matches : false,
  );
  useEffect(() => {
    const media = window.matchMedia(query);
    const handler = () => setMatches(media.matches);
    handler();
    media.addEventListener('change', handler);
    return () => media.removeEventListener('change', handler);
  }, [query]);
  return matches;
}

function loadSessionId(): string | null {
  try {
    return localStorage.getItem('mini-agent-session');
  } catch {
    return null;
  }
}

function persistSessionId(id: string | null): void {
  try {
    if (id) localStorage.setItem('mini-agent-session', id);
    else localStorage.removeItem('mini-agent-session');
  } catch {
    // ignore
  }
}

function loadNumber(key: string, fallback: number): number {
  try {
    const value = Number(localStorage.getItem(key));
    return Number.isFinite(value) && value > 0 ? value : fallback;
  } catch {
    return fallback;
  }
}

function loadBoolean(key: string, fallback: boolean): boolean {
  try {
    const value = localStorage.getItem(key);
    return value === null ? fallback : value === 'true';
  } catch {
    return fallback;
  }
}

export function App() {
  const { t, language, setLanguage, themeSetting, setTheme } = useUi();
  const isMobile = useMediaQuery('(max-width: 900px)');
  const [boot, setBoot] = useState<BootstrapData | null>(null);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [bootError, setBootError] = useState<string | null>(null);
  const [fatalError, setFatalError] = useState<string | null>(null);
  const [streamStatus, setStreamStatus] = useState<StreamStatus>('offline');
  const [retryNotice, setRetryNotice] = useState<string | null>(null);
  const [errorDismissed, setErrorDismissed] = useState(false);
  const [activeWorkspaceId, setActiveWorkspaceId] = useState<string | null>(
    null,
  );
  const [workspaces, setWorkspaces] = useState<BootstrapData['workspaces']>([]);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() =>
    loadBoolean('mini-agent-sidebar-collapsed', false),
  );
  const [sidebarWidth, setSidebarWidth] = useState(() =>
    loadNumber('mini-agent-sidebar-width', 284),
  );
  const [mobileOpen, setMobileOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsTab, setSettingsTab] = useState<SettingsTab>('general');
  const [adminOpen, setAdminOpen] = useState(false);
  const [adminTab, setAdminTab] = useState<AdminTab>('providers');
  const [commands, setCommands] = useState<CommandInfo[]>([]);
  const [previewCommand, setPreviewCommand] = useState<CommandInfo | null>(
    null,
  );
  const [previewCommandArgs, setPreviewCommandArgs] = useState('');
  const [goalOpen, setGoalOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [info, setInfo] = useState<{ title: string; body: string } | null>(
    null,
  );
  const [editingMessage, setEditingMessage] = useState<ChatMessage | null>(
    null,
  );
  const [editDraft, setEditDraft] = useState('');
  const [editBusy, setEditBusy] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [pendingReference, setPendingReference] = useState<{
    path: string;
    nonce: number;
  } | null>(null);
  const toastId = useRef(0);
  const booted = useRef(false);
  const selectedIdRef = useRef<string | null>(null);
  const snapshotRef = useRef<Snapshot | null>(null);
  const refreshTimer = useRef<number | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const searchRef = useRef('');

  useEffect(() => {
    selectedIdRef.current = selectedId;
  }, [selectedId]);
  useEffect(() => {
    snapshotRef.current = snapshot;
  }, [snapshot]);
  useEffect(() => {
    persistSessionId(selectedId);
    if (snapshot?.session.title)
      document.title = `${snapshot.session.title} · Mini Agent`;
  }, [selectedId, snapshot?.session.title]);

  const pushToast = useCallback(
    (message: string, kind: Toast['kind'] = 'info') => {
      const id = (toastId.current += 1);
      setToasts((current) => [...current, { id, message, kind }]);
      window.setTimeout(
        () => setToasts((current) => current.filter((item) => item.id !== id)),
        4800,
      );
    },
    [],
  );

  const refreshSessions = useCallback(async () => {
    try {
      const list = await api.sessions({
        workspaceId: activeWorkspaceId ?? undefined,
        search: searchRef.current || undefined,
        limit: 200,
      });
      setSessions(list);
    } catch {
      // Keep the previous list; the SSE/snapshot state still drives the UI.
    }
  }, [activeWorkspaceId]);

  const refreshSnapshot = useCallback(async (): Promise<number> => {
    const id = selectedIdRef.current;
    if (!id) return 0;
    try {
      const next = await api.snapshot(id);
      if (selectedIdRef.current === id) {
        setSnapshot(next);
        setFatalError(null);
      }
      return next.lastEventId;
    } catch (error) {
      if (selectedIdRef.current === id) setFatalError(errorMessage(error));
      return 0;
    }
  }, []);

  const scheduleRefresh = useCallback(() => {
    if (refreshTimer.current) window.clearTimeout(refreshTimer.current);
    refreshTimer.current = window.setTimeout(() => {
      void refreshSnapshot();
      void refreshSessions();
    }, 80);
  }, [refreshSnapshot, refreshSessions]);

  const notifyDesktop = useCallback(
    (title: string, body: string) => {
      const config = boot?.config;
      if (!config?.general.notifications || typeof Notification === 'undefined')
        return;
      if (Notification.permission !== 'granted' || !document.hidden) return;
      try {
        new Notification(title, { body, icon: '/favicon.svg' });
      } catch {
        // Notification construction can fail on some embedded browsers.
      }
    },
    [boot?.config],
  );

  const handleEvent = useCallback(
    (event: StreamEvent) => {
      if (
        event.sessionId &&
        selectedIdRef.current &&
        event.sessionId !== selectedIdRef.current
      ) {
        if (
          event.type === 'session.updated' ||
          event.type === 'session.status'
        ) {
          setSessions((current) =>
            current.map((session) =>
              session.id === event.sessionId
                ? {
                    ...session,
                    ...(event.session as Partial<Session> | undefined),
                    ...(event.status
                      ? { status: event.status as Session['status'] }
                      : {}),
                  }
                : session,
            ),
          );
        }
        return;
      }
      setSnapshot((current) =>
        current && current.session.id === event.sessionId
          ? applyEvent(current, event)
          : current,
      );
      if (event.type === 'session.status' || event.type === 'session.updated') {
        setSessions((current) =>
          current.map((session) =>
            session.id === event.sessionId
              ? {
                  ...session,
                  ...(event.session as Partial<Session> | undefined),
                  ...(event.status
                    ? { status: event.status as Session['status'] }
                    : {}),
                }
              : session,
          ),
        );
        if (event.status === 'error') setErrorDismissed(false);
        if (event.status === 'waiting_user') {
          notifyDesktop(
            t('chat.interaction.ask'),
            snapshotRef.current?.session.title ?? t('app.name'),
          );
        }
      }
      if (event.type === 'turn.completed') {
        notifyDesktop(
          t('turn.completed'),
          snapshotRef.current?.session.title ?? t('app.name'),
        );
        scheduleRefresh();
      }
      if (
        [
          'turn.failed',
          'turn.paused',
          'turn.cancelled',
          'compaction.created',
          'session.recovered',
        ].includes(event.type)
      ) {
        scheduleRefresh();
      }
      if (event.type === 'interaction.required') {
        const interaction = event.interaction as { type?: string } | undefined;
        notifyDesktop(
          interaction?.type === 'permission'
            ? t('chat.interaction.permission')
            : t('chat.interaction.ask'),
          snapshotRef.current?.session.title ?? t('app.name'),
        );
      }
      if (event.type === 'model.retry') {
        setRetryNotice(t('chat.retryHint', { n: Number(event.attempt ?? 1) }));
      }
      if (event.type === 'model.fallback') {
        setRetryNotice(String(event.message ?? '已切换备用模型'));
        pushToast(String(event.message ?? '已切换备用模型'), 'info');
      }
      if (event.type === 'error') {
        const message = String(event.message ?? t('app.error'));
        const raw = event.rawCode ? ` (${String(event.rawCode)})` : '';
        pushToast(`${message}${raw}`, 'error');
      }
      if (event.type === 'session.recovered') {
        setErrorDismissed(false);
      }
    },
    [notifyDesktop, pushToast, scheduleRefresh, t],
  );

  useSessionStream({
    sessionId: selectedId,
    enabled: Boolean(selectedId && snapshot?.session.id === selectedId),
    initialLastEventId:
      snapshot?.session.id === selectedId ? snapshot.lastEventId : 0,
    onEvent: handleEvent,
    refreshSnapshot,
    onStatus: setStreamStatus,
    onFatal: (message) => setFatalError(message),
  });

  const bootstrap = useCallback(async () => {
    setLoading(true);
    setBootError(null);
    try {
      const data = await api.bootstrap();
      setBoot(data);
      setWorkspaces(data.workspaces);
      setSessions(data.sessions);
      setActiveWorkspaceId(
        (current) => current ?? data.workspaces[0]?.id ?? null,
      );
      const storedLanguage = localStorage.getItem('mini-agent-language');
      if (!storedLanguage && data.config.general.language !== language) {
        setLanguage(data.config.general.language as Language);
      }
      const storedTheme = localStorage.getItem('mini-agent-theme');
      if (!storedTheme && data.config.general.theme !== themeSetting) {
        setTheme(data.config.general.theme as ThemeSetting);
      }
      const stored = loadSessionId();
      const candidate =
        data.sessions.find((session) => session.id === stored) ??
        data.sessions[0];
      if (candidate) {
        setSelectedId(candidate.id);
        setActiveWorkspaceId(candidate.workspaceId);
      } else if (data.config.model_configured && data.workspaces.length) {
        const created = await api.createSession({
          workspaceId: data.workspaces[0].id,
          model: data.config.defaults.model,
          mode: data.config.defaults.mode,
          reasoningLevel: data.config.defaults.reasoning_level,
        });
        setSessions([created.session]);
        setSelectedId(created.session.id);
      } else {
        setLoading(false);
      }
    } catch (error) {
      setBootError(errorMessage(error));
      setLoading(false);
    }
  }, [language, setLanguage, setTheme, themeSetting]);

  useEffect(() => {
    if (booted.current) return;
    booted.current = true;
    void bootstrap();
  }, [bootstrap]);

  useEffect(() => {
    if (!boot) return;
    api.commands
      .list()
      .then(setCommands)
      .catch(() => setCommands([]));
  }, [boot]);

  useEffect(() => {
    if (!selectedId) {
      setSnapshot(null);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setFatalError(null);
    setErrorDismissed(false);
    setRetryNotice(null);
    api
      .snapshot(selectedId, controller.signal)
      .then((next) => {
        if (controller.signal.aborted) return;
        setSnapshot(next);
        setLoading(false);
      })
      .catch((error) => {
        if (controller.signal.aborted) return;
        setFatalError(errorMessage(error));
        setLoading(false);
      });
    return () => controller.abort();
  }, [selectedId]);

  useEffect(() => {
    if (snapshot?.session.id === selectedId) setErrorDismissed(false);
  }, [snapshot?.session.status, snapshot?.session.id, selectedId]);

  useEffect(() => {
    if (!isMobile) setMobileOpen(false);
  }, [isMobile]);

  useEffect(() => {
    localStorage.setItem(
      'mini-agent-sidebar-collapsed',
      String(sidebarCollapsed),
    );
  }, [sidebarCollapsed]);
  useEffect(() => {
    localStorage.setItem('mini-agent-sidebar-width', String(sidebarWidth));
  }, [sidebarWidth]);

  const selectSession = useCallback(
    (id: string) => {
      const target = sessions.find((session) => session.id === id);
      if (target) setActiveWorkspaceId(target.workspaceId);
      setSelectedId(id);
      setMobileOpen(false);
      setPaletteOpen(false);
    },
    [sessions],
  );

  const changeWorkspace = useCallback(
    async (workspaceId: string) => {
      setActiveWorkspaceId(workspaceId);
      setMobileOpen(false);
      try {
        const list = await api.sessions({ workspaceId, limit: 200 });
        setSessions(list);
        const next = list[0] ?? null;
        setSelectedId(next?.id ?? null);
        if (!next) setSnapshot(null);
      } catch (error) {
        pushToast(errorMessage(error), 'error');
      }
    },
    [pushToast],
  );

  const createSession = useCallback(async () => {
    if (!boot) return;
    if (!boot.config.model_configured) {
      setSettingsTab('models');
      setSettingsOpen(true);
      return;
    }
    try {
      const created = await api.createSession({
        workspaceId: activeWorkspaceId ?? boot.workspaces[0]?.id,
        model: boot.config.defaults.model,
        mode: boot.config.defaults.mode,
        reasoningLevel: boot.config.defaults.reasoning_level,
      });
      setSessions((current) => [created.session, ...current]);
      setSelectedId(created.session.id);
      setMobileOpen(false);
      pushToast(t('toast.sessionCreated'), 'success');
    } catch (error) {
      pushToast(errorMessage(error), 'error');
    }
  }, [boot, activeWorkspaceId, pushToast, t]);

  const handleSearch = useCallback(
    async (query: string) => {
      searchRef.current = query;
      try {
        const list = await api.sessions({
          workspaceId: activeWorkspaceId ?? undefined,
          search: query || undefined,
          limit: 200,
        });
        setSessions(list);
      } catch {
        // Keep current list on transient failure.
      }
    },
    [activeWorkspaceId],
  );

  const currentSession =
    snapshot?.session ??
    sessions.find((session) => session.id === selectedId) ??
    null;
  const visibleSessions = activeWorkspaceId
    ? sessions.filter((session) => session.workspaceId === activeWorkspaceId)
    : sessions;
  const currentWorkspace =
    snapshot?.workspace ??
    boot?.workspaces.find(
      (workspace) => workspace.id === currentSession?.workspaceId,
    ) ??
    null;
  const currentAgent: AgentInfo | null =
    snapshot?.agent ??
    boot?.agents.find((agent) => agent.id === currentSession?.agentId) ??
    null;
  const busy = currentSession
    ? ['running', 'waiting_user', 'compacting'].includes(currentSession.status)
    : false;

  const sendMessage = useCallback(
    async (
      content: string,
      mode: 'auto' | 'steer' | 'queue' = 'auto',
      attachmentIds?: string[],
    ) => {
      if (!currentSession) throw new Error(t('app.noSessionsHint'));
      try {
        const result = await api.sendMessage(currentSession.id, {
          content,
          mode,
          attachmentIds,
          clientMessageId: newClientId(),
        });
        setSessions((current) =>
          current.map((session) =>
            session.id === currentSession.id
              ? {
                  ...session,
                  status: 'running',
                  updatedAt: new Date().toISOString(),
                }
              : session,
          ),
        );
        if (result.queued) {
          pushToast(
            mode === 'steer' ? t('toast.steered') : t('toast.queued'),
            'success',
          );
          scheduleRefresh();
        } else {
          window.setTimeout(() => void refreshSnapshot(), 60);
        }
      } catch (error) {
        pushToast(errorMessage(error), 'error');
        throw error;
      }
    },
    [currentSession, pushToast, refreshSnapshot, scheduleRefresh, t],
  );

  const switchAgent = useCallback(
    async (agentId: string) => {
      if (!currentSession) return;
      try {
        const updated = await api.updateSession(currentSession.id, { agentId });
        setSnapshot((current) =>
          current ? { ...current, session: updated.session } : current,
        );
        pushToast(t('settings.saved'), 'success');
      } catch (error) {
        pushToast(errorMessage(error), 'error');
      }
    },
    [currentSession, pushToast, t],
  );

  const deleteSession = useCallback(
    async (session: Session) => {
      if (
        !window.confirm(
          t('app.deleteConfirm', { title: session.title || session.id }),
        )
      )
        return;
      try {
        await api.deleteSession(session.id);
        setSessions((current) =>
          current.filter((item) => item.id !== session.id),
        );
        if (selectedId === session.id) {
          const next = sessions.find((item) => item.id !== session.id) ?? null;
          setSelectedId(next?.id ?? null);
          if (!next) setSnapshot(null);
        }
        pushToast(t('app.deleted'), 'success');
      } catch (error) {
        pushToast(errorMessage(error), 'error');
      }
    },
    [pushToast, selectedId, sessions, t],
  );

  const undoFiles = useCallback(
    async (turnId: string) => {
      if (!currentSession) return;
      try {
        const result = await api.checkpoints.undo(currentSession.id, turnId);
        pushToast(
          result.restored.length
            ? t('chat.filesUndone', { n: result.restored.length })
            : t('chat.noFileChanges'),
          result.restored.length ? 'success' : 'info',
        );
        scheduleRefresh();
      } catch (error) {
        pushToast(errorMessage(error), 'error');
      }
    },
    [currentSession, pushToast, scheduleRefresh, t],
  );

  const redoFiles = useCallback(
    async (turnId: string) => {
      if (!currentSession) return;
      try {
        const result = await api.checkpoints.redo(currentSession.id, turnId);
        pushToast(
          result.restored.length
            ? t('chat.filesRedone', { n: result.restored.length })
            : t('chat.noFileChanges'),
          result.restored.length ? 'success' : 'info',
        );
        scheduleRefresh();
      } catch (error) {
        pushToast(errorMessage(error), 'error');
      }
    },
    [currentSession, pushToast, scheduleRefresh, t],
  );

  const openCommandPreview = useCallback(
    (command: CommandInfo, initialArgs = '') => {
      setPreviewCommandArgs(initialArgs);
      setPreviewCommand(command);
    },
    [],
  );

  const runCustomCommand = useCallback(
    async (command: CommandInfo, prompt: string) => {
      if (!currentSession) return;
      if (command.model || command.agentId || command.reasoningLevel) {
        const updated = await api.updateSession(currentSession.id, {
          model: command.model ?? undefined,
          agentId: command.agentId ?? undefined,
          reasoningLevel: command.reasoningLevel ?? undefined,
        });
        setSnapshot((current) =>
          current ? { ...current, session: updated.session } : current,
        );
      }
      await sendMessage(prompt, 'auto');
      pushToast(t('chat.commandStarted'), 'success');
    },
    [currentSession, pushToast, sendMessage, t],
  );

  const stop = useCallback(async () => {
    if (!currentSession) return;
    try {
      await api.pause(currentSession.id);
      pushToast(t('toast.paused'), 'success');
      scheduleRefresh();
    } catch (error) {
      pushToast(errorMessage(error), 'error');
    }
  }, [currentSession, pushToast, scheduleRefresh, t]);

  const resume = useCallback(async () => {
    if (!currentSession) return;
    try {
      await api.resume(currentSession.id);
      pushToast(t('toast.resumed'), 'success');
      scheduleRefresh();
    } catch (error) {
      pushToast(errorMessage(error), 'error');
    }
  }, [currentSession, pushToast, scheduleRefresh, t]);

  const answerInteraction = useCallback(
    async (interactionId: string, answer: Record<string, unknown>) => {
      if (!currentSession) return;
      try {
        await api.answer(currentSession.id, interactionId, answer);
        scheduleRefresh();
      } catch (error) {
        pushToast(errorMessage(error), 'error');
        throw error;
      }
    },
    [currentSession, pushToast, scheduleRefresh],
  );

  const patchSession = useCallback(
    async (body: Parameters<typeof api.updateSession>[1]) => {
      if (!currentSession) return;
      try {
        const result = await api.updateSession(currentSession.id, body);
        setSessions((current) =>
          current.map((session) =>
            session.id === result.session.id ? result.session : session,
          ),
        );
        setSnapshot((current) =>
          current && current.session.id === result.session.id
            ? { ...current, session: result.session }
            : current,
        );
      } catch (error) {
        pushToast(errorMessage(error), 'error');
        throw error;
      }
    },
    [currentSession, pushToast],
  );

  const renameSession = useCallback(
    async (title: string) => {
      await patchSession({ title });
    },
    [patchSession],
  );

  const changeMode = useCallback(
    async (mode: 'build' | 'plan') => {
      if (!currentSession || currentSession.mode === mode) return;
      await patchSession({ mode });
    },
    [currentSession, patchSession],
  );

  const changeModel = useCallback(
    async (model: string, reasoning: string) => {
      if (!currentSession) return;
      await patchSession({ model, reasoningLevel: reasoning });
      pushToast(t('settings.saved'), 'success');
    },
    [currentSession, patchSession, pushToast, t],
  );

  const pinSession = useCallback(
    async (session: Session, pinned: boolean) => {
      try {
        const result = await api.updateSession(session.id, { pinned });
        setSessions((current) =>
          current.map((item) =>
            item.id === result.session.id ? result.session : item,
          ),
        );
        pushToast(t('toast.pinned'), 'success');
      } catch (error) {
        pushToast(errorMessage(error), 'error');
      }
    },
    [pushToast, t],
  );

  const archiveSession = useCallback(
    async (session: Session) => {
      try {
        await api.updateSession(session.id, { archived: true });
        setSessions((current) =>
          current.filter((item) => item.id !== session.id),
        );
        pushToast(t('toast.archived'), 'success');
        if (session.id === selectedIdRef.current) {
          const next = sessions.find((item) => item.id !== session.id) ?? null;
          setSelectedId(next?.id ?? null);
          setSnapshot(null);
        }
      } catch (error) {
        pushToast(errorMessage(error), 'error');
      }
    },
    [pushToast, sessions, t],
  );

  const forkSession = useCallback(
    async (messageId: string) => {
      if (!currentSession) return;
      try {
        const result = await api.fork(currentSession.id, messageId);
        setSessions((current) => [result.session, ...current]);
        setSelectedId(result.session.id);
        pushToast(t('toast.forked'), 'success');
      } catch (error) {
        pushToast(errorMessage(error), 'error');
      }
    },
    [currentSession, pushToast, t],
  );

  const openEdit = useCallback((message: ChatMessage) => {
    setEditingMessage(message);
    setEditDraft(message.content.text);
  }, []);

  const saveEdit = useCallback(async () => {
    if (!currentSession || !editingMessage) return;
    setEditBusy(true);
    try {
      const result = await api.editMessage(
        currentSession.id,
        editingMessage.id,
        editDraft.trim(),
      );
      setSessions((current) => [result.session, ...current]);
      setSelectedId(result.session.id);
      setEditingMessage(null);
      pushToast(t('chat.editDone'), 'success');
    } catch (error) {
      pushToast(errorMessage(error), 'error');
    } finally {
      setEditBusy(false);
    }
  }, [currentSession, editingMessage, editDraft, pushToast, t]);

  const compact = useCallback(async () => {
    if (!currentSession) return;
    try {
      await api.compact(currentSession.id);
      pushToast(t('toast.compacted'), 'success');
      scheduleRefresh();
    } catch (error) {
      pushToast(errorMessage(error), 'error');
    }
  }, [currentSession, pushToast, scheduleRefresh, t]);

  const exportSession = useCallback(
    async (format: 'markdown' | 'json') => {
      if (!currentSession) return;
      try {
        await api.exports.download(currentSession.id, format, {
          includeReasoning: true,
          includeTools: true,
        });
        pushToast(t('toast.exported'), 'success');
      } catch (error) {
        pushToast(errorMessage(error), 'error');
      }
    },
    [currentSession, pushToast, t],
  );

  const createShare = useCallback(async () => {
    if (!currentSession) return;
    try {
      const share = await api.shares.create(currentSession.id);
      const url = `${window.location.origin}/share/${share.token}`;
      await copyText(url);
      pushToast(`${t('toast.shareCreated')} · ${url}`, 'success');
    } catch (error) {
      pushToast(errorMessage(error), 'error');
    }
  }, [currentSession, pushToast, t]);

  const saveGoal = useCallback(
    async (body: {
      objective: string;
      criteria: string;
      tasks: Array<{
        title: string;
        status: 'pending' | 'in_progress' | 'completed' | 'blocked';
      }>;
    }) => {
      if (!currentSession) return;
      const result = await api.goals.update(currentSession.id, body);
      setSnapshot((current) =>
        current ? { ...current, goal: result.goal } : current,
      );
      pushToast(t('toast.goalSaved'), 'success');
    },
    [currentSession, pushToast, t],
  );

  const saveDefaults = useCallback(
    async (defaults: Record<string, unknown>) => {
      const updated = await api.patchConfig({ defaults });
      setBoot((current) =>
        current ? { ...current, config: updated } : current,
      );
    },
    [],
  );

  const changeTheme = useCallback(
    (theme: ThemeSetting) => {
      setTheme(theme);
      if (boot) {
        void api.patchConfig({ general: { theme } }).catch(() => undefined);
        setBoot((current) =>
          current
            ? {
                ...current,
                config: {
                  ...current.config,
                  general: { ...current.config.general, theme },
                },
              }
            : current,
        );
      }
    },
    [boot, setTheme],
  );

  const changeLanguage = useCallback(
    (next: Language) => {
      setLanguage(next);
      if (boot) {
        void api
          .patchConfig({ general: { language: next } })
          .catch(() => undefined);
        setBoot((current) =>
          current
            ? {
                ...current,
                config: {
                  ...current.config,
                  general: { ...current.config.general, language: next },
                },
              }
            : current,
        );
      }
    },
    [boot, setLanguage],
  );

  const changeNotifications = useCallback(
    (enabled: boolean) => {
      if (!boot) return;
      setBoot((current) =>
        current
          ? {
              ...current,
              config: {
                ...current.config,
                general: { ...current.config.general, notifications: enabled },
              },
            }
          : current,
      );
      void api
        .patchConfig({ general: { notifications: enabled } })
        .catch(() => undefined);
    },
    [boot],
  );

  const updateQueue = useCallback(
    async (
      id: string,
      body: { content?: string; mode?: 'steer' | 'queue' },
    ) => {
      if (!currentSession) return;
      await api.updateQueue(currentSession.id, id, body);
      scheduleRefresh();
    },
    [currentSession, scheduleRefresh],
  );

  const deleteQueue = useCallback(
    async (id: string) => {
      if (!currentSession) return;
      await api.deleteQueue(currentSession.id, id);
      scheduleRefresh();
    },
    [currentSession, scheduleRefresh],
  );

  const focusComposer = useCallback(() => {
    textareaRef.current?.focus();
  }, []);

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      const meta = event.metaKey || event.ctrlKey;
      if (meta && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setPaletteOpen((value) => !value);
      } else if (meta && event.key.toLowerCase() === 'n') {
        event.preventDefault();
        void createSession();
      } else if (event.key === 'Escape') {
        setPaletteOpen(false);
        setMobileOpen(false);
        setInfo(null);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [createSession]);

  const uiConfig = boot?.config;
  if (!uiConfig) {
    return (
      <div className="boot-screen">
        {bootError ? (
          <div className="centered-error">
            <AlertTriangle size={28} aria-hidden="true" />
            <h1>{t('app.error')}</h1>
            <p className="muted">{bootError}</p>
            <button
              type="button"
              className="primary-button"
              onClick={() => void bootstrap()}
            >
              {t('app.retry')}
            </button>
          </div>
        ) : (
          <div className="loading-screen" role="status" aria-live="polite">
            <div className="boot-mark">M</div>
            <div className="skeleton wide" />
            <div className="skeleton" />
            <span className="sr-only">{t('app.loading')}</span>
          </div>
        )}
      </div>
    );
  }

  if (!currentSession && !uiConfig.model_configured) {
    return (
      <div className="setup-screen">
        <div className="setup-card">
          <div className="brand-mark large" aria-hidden="true">
            M
          </div>
          <h1>{t('chat.noModel')}</h1>
          <p className="muted">{t('chat.noModelHint')}</p>
          <button
            type="button"
            className="primary-button"
            onClick={() => {
              setSettingsTab('models');
              setSettingsOpen(true);
            }}
          >
            <Settings size={15} aria-hidden="true" /> {t('chat.noModelCta')}
          </button>
          <p className="muted small">{t('settings.about.guide')}</p>
        </div>
        <SettingsDialog
          open={settingsOpen}
          onClose={() => setSettingsOpen(false)}
          config={uiConfig}
          agents={boot?.agents ?? []}
          session={null}
          snapshot={null}
          initialTab={settingsTab}
          onSetTheme={changeTheme}
          onSetLanguage={changeLanguage}
          onSetNotifications={changeNotifications}
          onSaveDefaults={saveDefaults}
          onExport={() => undefined}
          onToast={pushToast}
          onOpenAdmin={() => {
            setAdminTab('providers');
            setAdminOpen(true);
          }}
          onSwitchAgent={switchAgent}
          version={boot?.version}
        />
        <Toasts
          toasts={toasts}
          onDismiss={(id) =>
            setToasts((current) => current.filter((item) => item.id !== id))
          }
        />
      </div>
    );
  }

  if (!currentSession) {
    return (
      <div className="setup-screen">
        <div className="setup-card">
          <div className="brand-mark large" aria-hidden="true">
            M
          </div>
          <h1>{t('app.noSessions')}</h1>
          <p className="muted">
            {loading ? t('app.loading') : t('app.noSessionsHint')}
          </p>
          <button
            type="button"
            className="primary-button"
            onClick={() => void createSession()}
          >
            <Plus size={15} aria-hidden="true" /> {t('app.newSession')}
          </button>
        </div>
        <Toasts
          toasts={toasts}
          onDismiss={(id) =>
            setToasts((current) => current.filter((item) => item.id !== id))
          }
        />
      </div>
    );
  }

  const composer = (
    <Composer
      session={currentSession}
      config={uiConfig}
      queue={snapshot?.queue ?? []}
      busy={busy}
      onSend={sendMessage}
      commands={commands}
      onPreviewCommand={openCommandPreview}
      onStop={() => void stop()}
      onModeChange={changeMode}
      onModelChange={changeModel}
      onCompact={() => void compact()}
      onExport={() => void exportSession('json')}
      onNew={() => void createSession()}
      onOpenGoal={() => setGoalOpen(true)}
      onOpenSettings={() => {
        setSettingsTab('models');
        setSettingsOpen(true);
      }}
      onShowHelp={() => setHelpOpen(true)}
      onToast={pushToast}
      onUpdateQueue={updateQueue}
      onDeleteQueue={deleteQueue}
      disabledReason={!uiConfig.model_configured ? t('chat.noModelHint') : null}
      textareaRef={textareaRef}
      pendingReference={pendingReference}
      onReferenceConsumed={() => setPendingReference(null)}
    />
  );

  return (
    <div
      className={`app-shell ${sidebarCollapsed ? 'sidebar-collapsed' : ''} ${mobileOpen ? 'mobile-open' : ''}`}
    >
      <div className="sidebar-frame" aria-hidden={isMobile && !mobileOpen}>
        <Sidebar
          sessions={visibleSessions}
          selectedId={selectedId}
          workspaces={workspaces}
          activeWorkspaceId={activeWorkspaceId}
          collapsed={!isMobile && sidebarCollapsed}
          width={sidebarWidth}
          mobile={isMobile}
          onSelect={selectSession}
          onNewSession={() => void createSession()}
          onOpenSettings={() => {
            setSettingsTab('general');
            setSettingsOpen(true);
          }}
          onOpenSkills={() => {
            setAdminTab('skills');
            setAdminOpen(true);
          }}
          onOpenMcp={() => {
            setAdminTab('mcp');
            setAdminOpen(true);
          }}
          onOpenAgents={() => {
            setAdminTab('agents');
            setAdminOpen(true);
          }}
          onOpenAdmin={() => {
            setAdminTab('providers');
            setAdminOpen(true);
          }}
          onDeleteSession={(session) => void deleteSession(session)}
          onOpenPalette={() => setPaletteOpen(true)}
          onSearch={handleSearch}
          onWorkspaceChange={(id) => void changeWorkspace(id)}
          onPin={(session, pinned) => void pinSession(session, pinned)}
          onArchive={(session) => void archiveSession(session)}
          onToggleCollapse={() => setSidebarCollapsed((value) => !value)}
          onWidthChange={setSidebarWidth}
          onReferenceFile={(path) =>
            setPendingReference({ path, nonce: Date.now() })
          }
        />
      </div>
      {isMobile && mobileOpen && (
        <button
          type="button"
          className="sidebar-backdrop"
          aria-label={t('app.close')}
          onClick={() => setMobileOpen(false)}
        />
      )}
      <ChatView
        session={currentSession}
        snapshot={snapshot}
        config={uiConfig}
        workspace={currentWorkspace}
        agent={currentAgent}
        loading={loading}
        fatalError={fatalError}
        streamStatus={streamStatus}
        retryNotice={retryNotice}
        interactions={(snapshot?.interactions ?? []).filter(
          (interaction) => interaction.status === 'pending',
        )}
        composer={composer}
        mobile={isMobile}
        sidebarCollapsed={sidebarCollapsed}
        onCycleTheme={() =>
          changeTheme(
            themeSetting === 'dark'
              ? 'light'
              : themeSetting === 'light'
                ? 'system'
                : 'dark',
          )
        }
        onToggleSidebar={() =>
          isMobile
            ? setMobileOpen((value) => !value)
            : setSidebarCollapsed((value) => !value)
        }
        onRename={async (title) => {
          await renameSession(title);
        }}
        onAnswer={answerInteraction}
        onResume={() => void resume()}
        onRetry={() => void bootstrap()}
        onOpenSettings={() => {
          setSettingsTab('general');
          setSettingsOpen(true);
        }}
        onOpenGoal={() => setGoalOpen(true)}
        onOpenShare={() => void createShare()}
        onExport={(format) => void exportSession(format)}
        onCompact={() => void compact()}
        onEditMessage={openEdit}
        onFork={(messageId) => void forkSession(messageId)}
        onUndo={(turnId) => void undoFiles(turnId)}
        onRedo={(turnId) => void redoFiles(turnId)}
        onSelectSession={selectSession}
        onSuggestion={(prompt) => void sendMessage(prompt)}
        onDismissRetry={() => setRetryNotice(null)}
        onDismissError={() => setErrorDismissed(true)}
        errorDismissed={errorDismissed}
      />

      <SettingsDialog
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        config={uiConfig}
        agents={boot?.agents ?? []}
        session={currentSession}
        snapshot={snapshot}
        initialTab={settingsTab}
        onSetTheme={changeTheme}
        onSetLanguage={changeLanguage}
        onSetNotifications={changeNotifications}
        onSaveDefaults={saveDefaults}
        onExport={(format) => void exportSession(format)}
        onToast={pushToast}
        onOpenAdmin={() => {
          setAdminTab('providers');
          setAdminOpen(true);
        }}
        onSwitchAgent={switchAgent}
        version={boot?.version}
      />

      <Suspense fallback={null}>
        <AdminDialog
          open={adminOpen}
          tab={adminTab}
          onTabChange={setAdminTab}
          onClose={() => setAdminOpen(false)}
          config={uiConfig}
          agents={boot?.agents ?? []}
          workspaces={workspaces}
          activeWorkspace={activeWorkspaceId ?? currentWorkspace?.id ?? null}
          session={currentSession}
          onToast={pushToast}
          onReload={async () => {
            const [data, commandList] = await Promise.all([
              api.bootstrap(),
              api.commands.list(),
            ]);
            setBoot(data);
            setWorkspaces(data.workspaces);
            setCommands(commandList);
            void refreshSessions();
            void refreshSnapshot();
          }}
        />
      </Suspense>

      <CommandPreview
        command={previewCommand}
        initialArgs={previewCommandArgs}
        onClose={() => {
          setPreviewCommand(null);
          setPreviewCommandArgs('');
        }}
        onRun={runCustomCommand}
        onToast={pushToast}
      />

      <GoalDialog
        open={goalOpen}
        goal={snapshot?.goal ?? null}
        onClose={() => setGoalOpen(false)}
        onSave={saveGoal}
      />

      <CommandPalette
        open={paletteOpen}
        sessions={sessions}
        onClose={() => setPaletteOpen(false)}
        onSelectSession={selectSession}
        onNewSession={() => void createSession()}
        onOpenSettings={() => {
          setSettingsTab('general');
          setSettingsOpen(true);
        }}
        onToggleTheme={() =>
          changeTheme(themeSetting === 'dark' ? 'light' : 'dark')
        }
        onStop={() => void stop()}
        onResume={() => void resume()}
        onCompact={() => void compact()}
        onExport={() => void exportSession('json')}
        onFocusComposer={focusComposer}
      />

      <Modal
        open={helpOpen}
        title={t('app.shortcuts')}
        onClose={() => setHelpOpen(false)}
      >
        <dl className="shortcut-list">
          <div>
            <dt>
              <kbd>⌘/Ctrl</kbd> + <kbd>K</kbd>
            </dt>
            <dd>{t('app.shortcut.palette')}</dd>
          </div>
          <div>
            <dt>
              <kbd>⌘/Ctrl</kbd> + <kbd>N</kbd>
            </dt>
            <dd>{t('app.shortcut.new')}</dd>
          </div>
          <div>
            <dt>
              <kbd>Enter</kbd>
            </dt>
            <dd>{t('chat.send')}</dd>
          </div>
          <div>
            <dt>
              <kbd>Shift</kbd> + <kbd>Enter</kbd>
            </dt>
            <dd>{t('chat.composer.placeholder')}</dd>
          </div>
        </dl>
        <p className="muted small">{t('palette.hint')}</p>
      </Modal>

      <Modal
        open={info !== null}
        title={info?.title ?? ''}
        onClose={() => setInfo(null)}
      >
        <p className="muted">{info?.body}</p>
      </Modal>

      <Modal
        open={editingMessage !== null}
        title={t('chat.editTitle')}
        onClose={() => setEditingMessage(null)}
        footer={
          <>
            <button
              type="button"
              className="text-button"
              onClick={() => setEditingMessage(null)}
            >
              {t('app.cancel')}
            </button>
            <button
              type="button"
              className="primary-button"
              disabled={editBusy || !editDraft.trim()}
              onClick={() => void saveEdit()}
            >
              {t('app.save')}
            </button>
          </>
        }
      >
        <p className="muted small">{t('chat.editHint')}</p>
        <textarea
          className="text-input"
          rows={8}
          value={editDraft}
          onChange={(event) => setEditDraft(event.target.value)}
        />
      </Modal>

      <Toasts
        toasts={toasts}
        onDismiss={(id) =>
          setToasts((current) => current.filter((item) => item.id !== id))
        }
      />
    </div>
  );
}

function Toasts({
  toasts,
  onDismiss,
}: {
  toasts: Toast[];
  onDismiss: (id: number) => void;
}) {
  return (
    <div className="toast-stack" role="region" aria-label="Notifications">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className={`toast toast-${toast.kind}`}
          role="status"
        >
          {toast.kind === 'error' ? (
            <AlertTriangle size={15} aria-hidden="true" />
          ) : toast.kind === 'success' ? (
            <CheckCircle2 size={15} aria-hidden="true" />
          ) : (
            <Info size={15} aria-hidden="true" />
          )}
          <span>{toast.message}</span>
          <button
            type="button"
            className="icon-button"
            onClick={() => onDismiss(toast.id)}
            aria-label="Dismiss"
          >
            <X size={13} aria-hidden="true" />
          </button>
        </div>
      ))}
    </div>
  );
}
