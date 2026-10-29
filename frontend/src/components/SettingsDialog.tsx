import {
  Bell,
  Bot,
  Check,
  Copy,
  Database,
  Download,
  KeyRound,
  Link2,
  RefreshCw,
  Shield,
  SlidersHorizontal,
  Target,
  Trash2,
  X,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type {
  AgentInfo,
  DebugRequest,
  PermissionGrant,
  PublicConfig,
  Session,
  ShareInfo,
  Snapshot,
} from '../api/types';
import type { Language } from '../lib/i18n';
import type { ThemeSetting } from '../lib/theme';
import { formatCost, formatDateTime, formatTokens } from '../lib/i18n';
import { copyText } from '../lib/clipboard';
import { useUi } from '../state/UiContext';
import { Modal } from './Modal';

interface Props {
  open: boolean;
  onClose: () => void;
  config: PublicConfig;
  agents: AgentInfo[];
  session: Session | null;
  snapshot: Snapshot | null;
  onSetTheme: (theme: ThemeSetting) => void;
  onSetLanguage: (language: Language) => void;
  onSetNotifications: (enabled: boolean) => void;
  onSaveDefaults: (defaults: Record<string, unknown>) => Promise<void>;
  onExport: (format: 'markdown' | 'json') => void;
  onToast: (message: string, kind?: 'info' | 'error' | 'success') => void;
  onOpenAdmin?: () => void;
  onSwitchAgent?: (agentId: string) => Promise<void>;
  version?: string;
  initialTab?: Tab;
}

type Tab = 'general' | 'models' | 'agents' | 'permissions' | 'data' | 'about';

export function SettingsDialog({
  open,
  onClose,
  config,
  agents,
  session,
  snapshot,
  onSetTheme,
  onSetLanguage,
  onSetNotifications,
  onSaveDefaults,
  onExport,
  onToast,
  onOpenAdmin,
  onSwitchAgent,
  version,
  initialTab,
}: Props) {
  const { t, language, themeSetting } = useUi();
  const [tab, setTab] = useState<Tab>(initialTab ?? 'general');
  const [defaultModel, setDefaultModel] = useState(config.defaults.model);
  const [fallbackModels, setFallbackModels] = useState(
    (config.defaults as { fallback_models?: string[] }).fallback_models?.join(
      ', ',
    ) ?? '',
  );
  const [visionModel, setVisionModel] = useState(
    (config.defaults as { vision_model?: string }).vision_model ?? '',
  );
  const [compactionModel, setCompactionModel] = useState(
    config.compaction.model ?? '',
  );
  const [useModelSummary, setUseModelSummary] = useState(
    config.compaction.use_model ?? false,
  );
  const [reloadRules, setReloadRules] = useState(
    config.compaction.reload_project_rules ?? true,
  );
  const [sandboxMode, setSandboxMode] = useState(
    config.sandbox?.mode ?? 'auto',
  );
  const [allowNetwork, setAllowNetwork] = useState(
    config.sandbox?.allow_network ?? false,
  );
  const [proxy, setProxy] = useState(config.network?.proxy ?? '');
  const [caBundle, setCaBundle] = useState(config.network?.ca_bundle ?? '');
  const [searchProvider, setSearchProvider] = useState(
    config.search?.provider ?? 'duckduckgo',
  );
  const [searchResults, setSearchResults] = useState(
    String(config.search?.max_results ?? 5),
  );
  const [saving, setSaving] = useState(false);
  const [grants, setGrants] = useState<PermissionGrant[]>([]);
  const [shares, setShares] = useState<ShareInfo[]>([]);
  const [debug, setDebug] = useState<DebugRequest[]>([]);
  const [debugLoading, setDebugLoading] = useState(false);
  const [notificationState, setNotificationState] =
    useState<NotificationPermission>('default');

  useEffect(() => {
    setDefaultModel(config.defaults.model);
  }, [config.defaults.model]);

  useEffect(() => {
    if (!open) return;
    if (initialTab) setTab(initialTab);
    setNotificationState(
      typeof Notification !== 'undefined' ? Notification.permission : 'denied',
    );
    api
      .permissions()
      .then((value) => setGrants(value.grants))
      .catch(() => setGrants([]));
    if (session) {
      api.shares
        .list(session.id)
        .then(setShares)
        .catch(() => setShares([]));
      setDebugLoading(true);
      api
        .debugRequests(session.id)
        .then(setDebug)
        .catch(() => setDebug([]))
        .finally(() => setDebugLoading(false));
    }
  }, [open, session, initialTab]);

  const saveDefaults = async () => {
    setSaving(true);
    try {
      await onSaveDefaults({
        model: defaultModel,
        fallback_models: fallbackModels
          .split(',')
          .map((item) => item.trim())
          .filter(Boolean),
        vision_model: visionModel.trim(),
        compaction_model: compactionModel.trim(),
      });
      await api.patchConfig({
        compaction: {
          use_model: useModelSummary,
          model: compactionModel.trim(),
          reload_project_rules: reloadRules,
        },
        sandbox: { mode: sandboxMode, allow_network: allowNetwork },
        network: { proxy: proxy.trim(), ca_bundle: caBundle.trim() },
        search: {
          provider: searchProvider,
          max_results: Math.max(1, Math.min(20, Number(searchResults) || 5)),
        },
      });
      onToast(t('settings.saved'), 'success');
    } catch (error) {
      onToast((error as Error).message, 'error');
    } finally {
      setSaving(false);
    }
  };

  const createShare = async () => {
    if (!session) return;
    try {
      const share = await api.shares.create(session.id);
      setShares((current) => [share, ...current]);
      const url = `${window.location.origin}/share/${share.token}`;
      await copyText(url);
      onToast(`${t('toast.shareCreated')} · ${url}`, 'success');
    } catch (error) {
      onToast((error as Error).message, 'error');
    }
  };

  const revokeShare = async (share: ShareInfo) => {
    try {
      await api.shares.revoke(share.id);
      setShares((current) =>
        current.map((item) =>
          item.id === share.id ? { ...item, revoked: true } : item,
        ),
      );
      onToast(t('toast.shareRevoked'), 'success');
    } catch (error) {
      onToast((error as Error).message, 'error');
    }
  };

  const revokeGrant = async (grant: PermissionGrant) => {
    try {
      await api.revokeGrant(grant.id);
      setGrants((current) => current.filter((item) => item.id !== grant.id));
      onToast(t('toast.grantRevoked'), 'success');
    } catch (error) {
      onToast((error as Error).message, 'error');
    }
  };

  const requestNotifications = async () => {
    if (typeof Notification === 'undefined') return;
    const permission = await Notification.requestPermission();
    setNotificationState(permission);
    if (permission === 'granted') {
      onSetNotifications(true);
      onToast(t('toast.notificationPermission'), 'success');
    } else {
      onToast(t('toast.notificationDenied'), 'error');
    }
  };

  const copyShare = async (share: ShareInfo) => {
    await copyText(`${window.location.origin}/share/${share.token}`);
    onToast(t('toast.shareCreated'), 'success');
  };

  const tabs: Array<{
    id: Tab;
    label: string;
    icon: typeof SlidersHorizontal;
  }> = [
    { id: 'general', label: t('settings.general'), icon: SlidersHorizontal },
    { id: 'models', label: t('settings.models'), icon: KeyRound },
    { id: 'agents', label: t('settings.agents'), icon: Bot },
    { id: 'permissions', label: t('settings.permissions'), icon: Shield },
    { id: 'data', label: t('settings.data'), icon: Database },
    { id: 'about', label: t('settings.about'), icon: Target },
  ];

  return (
    <Modal
      open={open}
      title={t('settings.title')}
      onClose={onClose}
      wide
      className="settings-modal"
    >
      <div className="settings-layout">
        <nav
          className="settings-tabs"
          role="tablist"
          aria-label={t('settings.title')}
        >
          {tabs.map((item) => {
            const Icon = item.icon;
            return (
              <button
                type="button"
                key={item.id}
                role="tab"
                aria-selected={tab === item.id}
                className={tab === item.id ? 'active' : ''}
                onClick={() => setTab(item.id)}
              >
                <Icon size={15} aria-hidden="true" /> {item.label}
              </button>
            );
          })}
          {onOpenAdmin && (
            <button
              type="button"
              className="settings-admin-link"
              onClick={() => {
                onClose();
                onOpenAdmin();
              }}
            >
              <SlidersHorizontal size={15} aria-hidden="true" />{' '}
              {t('app.admin')}
            </button>
          )}
        </nav>
        <div className="settings-content">
          {tab === 'general' && (
            <section className="settings-section">
              <h3>{t('settings.general')}</h3>
              <div className="form-grid">
                <label>
                  <span>{t('settings.general.language')}</span>
                  <select
                    value={language}
                    onChange={(event) =>
                      onSetLanguage(event.target.value as Language)
                    }
                  >
                    <option value="zh-CN">简体中文</option>
                    <option value="en-US">English</option>
                  </select>
                </label>
                <label>
                  <span>{t('settings.general.theme')}</span>
                  <select
                    value={themeSetting}
                    onChange={(event) =>
                      onSetTheme(event.target.value as ThemeSetting)
                    }
                  >
                    <option value="system">{t('app.theme.system')}</option>
                    <option value="light">{t('app.theme.light')}</option>
                    <option value="dark">{t('app.theme.dark')}</option>
                  </select>
                </label>
              </div>
              <div className="setting-row">
                <div>
                  <strong>{t('settings.general.notifications')}</strong>
                  <p className="muted">
                    {t('settings.general.notificationsHint')}
                  </p>
                </div>
                <div className="inline-actions">
                  {notificationState !== 'granted' && (
                    <button
                      type="button"
                      className="secondary-button"
                      onClick={() => void requestNotifications()}
                    >
                      <Bell size={14} aria-hidden="true" />{' '}
                      {t('settings.notifications.grant')}
                    </button>
                  )}
                  <label className="switch">
                    <input
                      type="checkbox"
                      checked={config.general.notifications}
                      onChange={(event) =>
                        onSetNotifications(event.target.checked)
                      }
                    />
                    <span aria-hidden="true" />
                    <span className="sr-only">
                      {t('settings.general.notifications')}
                    </span>
                  </label>
                  {notificationState === 'granted' && (
                    <span className="ok-chip">
                      <Check size={12} /> {t('settings.notifications.granted')}
                    </span>
                  )}
                </div>
              </div>
              <h4>{t('settings.general.shortcuts')}</h4>
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
                <div>
                  <dt>
                    <kbd>Esc</kbd>
                  </dt>
                  <dd>{t('app.close')}</dd>
                </div>
              </dl>
            </section>
          )}

          {tab === 'models' && (
            <section className="settings-section">
              <div className="settings-section-head">
                <h3>{t('settings.models')}</h3>
                {onOpenAdmin && (
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={onOpenAdmin}
                  >
                    <SlidersHorizontal size={14} aria-hidden="true" />{' '}
                    {t('app.admin')}
                  </button>
                )}
              </div>
              <label className="setting-row">
                <div>
                  <strong>{t('settings.models.default')}</strong>
                  <p className="muted">{t('settings.models.defaultHint')}</p>
                </div>
                <select
                  value={defaultModel}
                  onChange={(event) => setDefaultModel(event.target.value)}
                >
                  {config.models.map((model) => (
                    <option key={model.ref} value={model.ref}>
                      {model.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="setting-row">
                <div>
                  <strong>备用模型链</strong>
                  <p className="muted">
                    主模型不可用时按顺序尝试，逗号分隔 provider/model。
                  </p>
                </div>
                <input
                  value={fallbackModels}
                  onChange={(event) => setFallbackModels(event.target.value)}
                  placeholder="mock/backup"
                />
              </label>
              <label className="setting-row">
                <div>
                  <strong>视觉辅助模型</strong>
                  <p className="muted">
                    当前模型不支持视觉时，read_image 使用该模型描述图片。
                  </p>
                </div>
                <input
                  value={visionModel}
                  onChange={(event) => setVisionModel(event.target.value)}
                  placeholder="provider/vision"
                />
              </label>
              <label className="setting-row">
                <div>
                  <strong>压缩摘要模型</strong>
                  <p className="muted">
                    留空使用默认模型；关闭模型摘要时使用确定性摘要。
                  </p>
                </div>
                <input
                  value={compactionModel}
                  onChange={(event) => setCompactionModel(event.target.value)}
                  placeholder="provider/model"
                />
              </label>
              <div className="setting-row">
                <div>
                  <strong>压缩方式</strong>
                  <p className="muted">
                    压缩后可按当前 AGENTS.md、Skill/MCP 元信息重建 system
                    prompt。
                  </p>
                </div>
                <div className="inline-actions">
                  <label className="checkbox-field">
                    <input
                      type="checkbox"
                      checked={useModelSummary}
                      onChange={(event) =>
                        setUseModelSummary(event.target.checked)
                      }
                    />
                    <span>模型摘要</span>
                  </label>
                  <label className="checkbox-field">
                    <input
                      type="checkbox"
                      checked={reloadRules}
                      onChange={(event) => setReloadRules(event.target.checked)}
                    />
                    <span>重载规则</span>
                  </label>
                </div>
              </div>
              <h4>运行环境</h4>
              <div className="setting-row">
                <div>
                  <strong>Shell 沙箱</strong>
                  <p className="muted">
                    auto 优先使用 sandbox-exec / bwrap /
                    docker；不可用时回退并提示。
                  </p>
                </div>
                <select
                  value={sandboxMode}
                  onChange={(event) => setSandboxMode(event.target.value)}
                >
                  <option value="auto">auto</option>
                  <option value="sandbox-exec">sandbox-exec</option>
                  <option value="bwrap">bwrap</option>
                  <option value="docker">docker</option>
                  <option value="none">none</option>
                </select>
              </div>
              <label className="setting-row">
                <div>
                  <strong>沙箱允许网络</strong>
                  <p className="muted">默认禁止 command 外部网络；按需开启。</p>
                </div>
                <input
                  type="checkbox"
                  checked={allowNetwork}
                  onChange={(event) => setAllowNetwork(event.target.checked)}
                />
              </label>
              <label className="setting-row">
                <div>
                  <strong>HTTP 代理</strong>
                  <p className="muted">
                    模型、搜索、网页抓取与 MCP HTTP 共用。
                  </p>
                </div>
                <input
                  value={proxy}
                  onChange={(event) => setProxy(event.target.value)}
                  placeholder="http://127.0.0.1:7890"
                />
              </label>
              <label className="setting-row">
                <div>
                  <strong>自定义 CA Bundle</strong>
                  <p className="muted">
                    指定 PEM 路径，用于企业代理或私有证书。
                  </p>
                </div>
                <input
                  value={caBundle}
                  onChange={(event) => setCaBundle(event.target.value)}
                  placeholder="/path/to/ca.pem"
                />
              </label>
              <div className="setting-row">
                <div>
                  <strong>联网搜索</strong>
                  <p className="muted">
                    Tavily 需要环境变量 TAVILY_API_KEY；DuckDuckGo 无需密钥。
                  </p>
                </div>
                <div className="inline-actions">
                  <select
                    value={searchProvider}
                    onChange={(event) => setSearchProvider(event.target.value)}
                  >
                    <option value="duckduckgo">DuckDuckGo</option>
                    <option value="tavily">Tavily</option>
                  </select>
                  <input
                    value={searchResults}
                    onChange={(event) => setSearchResults(event.target.value)}
                    aria-label="搜索结果数量"
                    style={{ width: 64 }}
                  />
                </div>
              </div>
              <div className="inline-actions end">
                <button
                  type="button"
                  className="primary-button"
                  disabled={saving}
                  onClick={() => void saveDefaults()}
                >
                  <Check size={14} aria-hidden="true" />{' '}
                  {t('settings.models.reload')}
                </button>
              </div>
              <h4>{t('settings.models.providers')}</h4>
              <p className="muted">{t('settings.models.keyHint')}</p>
              <div className="provider-list">
                {config.providers.map((provider) => (
                  <article key={provider.id} className="provider-card">
                    <header>
                      <strong>{provider.name}</strong>
                      <span
                        className={`ok-chip ${provider.configured ? '' : 'warn-chip'}`}
                      >
                        {provider.configured
                          ? t('settings.models.configured')
                          : t('settings.models.notConfigured')}
                      </span>
                    </header>
                    <p className="muted small">
                      {provider.protocol} · {provider.base_url || 'local'}{' '}
                      {provider.key_hint && `· ${provider.key_hint}`}
                    </p>
                    <ul>
                      {provider.models.map((model) => (
                        <li key={model.id}>
                          <code>
                            {provider.id}/{model.id}
                          </code>
                          <span>
                            {formatTokens(model.context_window)}{' '}
                            {t('chat.context')}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </article>
                ))}
              </div>
              {config.config_path && (
                <p className="muted small">
                  {t('settings.models.rawConfig')}:{' '}
                  <code>{config.config_path}</code>
                </p>
              )}
            </section>
          )}

          {tab === 'agents' && (
            <section className="settings-section">
              <div className="settings-section-head">
                <h3>{t('settings.agents.builtin')}</h3>
                {onOpenAdmin && (
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={onOpenAdmin}
                  >
                    <SlidersHorizontal size={14} aria-hidden="true" />{' '}
                    {t('app.admin')}
                  </button>
                )}
              </div>
              <div className="agent-list">
                {agents.map((agent) => (
                  <article
                    key={agent.id}
                    className={`agent-card ${session?.agentId === agent.id ? 'active' : ''}`}
                  >
                    <header>
                      <Bot size={16} aria-hidden="true" />
                      <strong>{agent.name}</strong>
                      {agent.readOnly && (
                        <span className="ok-chip">
                          {t('settings.agents.readOnly')}
                        </span>
                      )}
                      {session?.agentId === agent.id && (
                        <span className="chip">
                          {t('settings.agents.current')}
                        </span>
                      )}
                    </header>
                    <p className="muted">{agent.description}</p>
                    <p className="muted small">
                      {agent.id === 'plan'
                        ? t('chat.mode.planHint')
                        : t('chat.mode.buildHint')}
                    </p>
                    {session &&
                      onSwitchAgent &&
                      agent.id !== session.agentId && (
                        <div className="inline-actions end">
                          <button
                            type="button"
                            className="secondary-button"
                            onClick={() => void onSwitchAgent(agent.id)}
                          >
                            {t('settings.agents.switch')}
                          </button>
                        </div>
                      )}
                  </article>
                ))}
              </div>
              <p className="muted small">{t('settings.about.guide')}</p>
            </section>
          )}

          {tab === 'permissions' && (
            <section className="settings-section">
              <h3>{t('settings.permissions')}</h3>
              <h4>{t('settings.permissions.defaults')}</h4>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>{t('settings.permissions.tool')}</th>
                    <th>{t('settings.permissions.ask')}</th>
                    <th>{t('settings.permissions.allow')}</th>
                    <th>{t('settings.permissions.deny')}</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(config.permissions).map(
                    ([category, rule]) => (
                      <tr key={category}>
                        <td>{t(`settings.permissions.${category}`)}</td>
                        <td>
                          <span className="policy-chip">{rule.default}</span>
                        </td>
                        <td className="mono small">
                          {rule.allow.join(', ') || '—'}
                        </td>
                        <td className="mono small">
                          {rule.deny.join(', ') || '—'}
                        </td>
                      </tr>
                    ),
                  )}
                </tbody>
              </table>
              <h4>{t('settings.permissions.grants')}</h4>
              {grants.length === 0 && (
                <p className="muted">{t('settings.permissions.noGrants')}</p>
              )}
              {grants.length > 0 && (
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>{t('settings.permissions.tool')}</th>
                      <th>{t('settings.permissions.pattern')}</th>
                      <th>{t('settings.permissions.scope')}</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {grants.map((grant) => (
                      <tr key={grant.id}>
                        <td>
                          <code>{grant.tool}</code>
                        </td>
                        <td className="mono small">{grant.pattern}</td>
                        <td>{grant.scope}</td>
                        <td>
                          <button
                            type="button"
                            className="text-button danger"
                            onClick={() => void revokeGrant(grant)}
                          >
                            <Trash2 size={13} aria-hidden="true" />{' '}
                            {t('settings.permissions.revoke')}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </section>
          )}

          {tab === 'data' && (
            <section className="settings-section">
              <div className="settings-section-head">
                <h3>{t('settings.data')}</h3>
                {onOpenAdmin && (
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={onOpenAdmin}
                  >
                    <SlidersHorizontal size={14} aria-hidden="true" />{' '}
                    {t('app.admin')}
                  </button>
                )}
              </div>
              <div className="setting-row">
                <div>
                  <strong>{t('settings.data.export')}</strong>
                  <p className="muted">{t('settings.data.exportHint')}</p>
                </div>
                <div className="inline-actions">
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() => onExport('markdown')}
                  >
                    <Download size={14} aria-hidden="true" /> Markdown
                  </button>
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() => onExport('json')}
                  >
                    <Download size={14} aria-hidden="true" /> JSON
                  </button>
                </div>
              </div>
              <div className="setting-row">
                <div>
                  <strong>{t('settings.data.share')}</strong>
                  <p className="muted">{t('settings.data.shareExpiry')}</p>
                </div>
                <button
                  type="button"
                  className="primary-button"
                  disabled={!session}
                  onClick={() => void createShare()}
                >
                  <Link2 size={14} aria-hidden="true" />{' '}
                  {t('settings.data.createShare')}
                </button>
              </div>
              {shares.length === 0 ? (
                <p className="muted">{t('settings.data.noShares')}</p>
              ) : (
                <ul className="share-list">
                  {shares.map((share) => (
                    <li
                      key={share.id}
                      className={share.revoked ? 'revoked' : ''}
                    >
                      <Link2 size={14} aria-hidden="true" />
                      <code>/share/{share.token.slice(0, 10)}…</code>
                      <span className="muted small">
                        {share.expiresAt
                          ? formatDateTime(share.expiresAt, language)
                          : '—'}
                      </span>
                      <button
                        type="button"
                        className="icon-button"
                        title={t('app.copy')}
                        onClick={() => void copyShare(share)}
                      >
                        <Copy size={13} aria-hidden="true" />
                      </button>
                      {!share.revoked && (
                        <button
                          type="button"
                          className="icon-button danger"
                          title={t('settings.data.revoke')}
                          onClick={() => void revokeShare(share)}
                        >
                          <X size={13} aria-hidden="true" />
                        </button>
                      )}
                    </li>
                  ))}
                </ul>
              )}
              <h4>{t('settings.data.debug')}</h4>
              <p className="muted small">{t('settings.data.debugHint')}</p>
              {debugLoading && <p className="muted">{t('app.loading')}…</p>}
              {!debugLoading && debug.length === 0 && (
                <p className="muted">{t('settings.data.debugEmpty')}</p>
              )}
              {debug.length > 0 && (
                <div className="table-scroll">
                  <table className="data-table debug-table">
                    <thead>
                      <tr>
                        <th>#</th>
                        <th>prefix</th>
                        <th>{t('chat.usage.cacheHit')}</th>
                        <th>{t('chat.usage.cacheMiss')}</th>
                        <th>retries</th>
                        <th>ms</th>
                      </tr>
                    </thead>
                    <tbody>
                      {debug.map((row) => (
                        <tr key={row.id}>
                          <td>{row.step}</td>
                          <td className="mono small">
                            {row.prefixHash.slice(0, 12)}
                          </td>
                          <td>{row.cacheHitTokens}</td>
                          <td>{row.cacheMissTokens}</td>
                          <td>{row.retries}</td>
                          <td>{row.durationMs}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <h4>{t('settings.data.recovery')}</h4>
              {(!snapshot || !snapshot.session.lastError) && (
                <p className="muted">{t('settings.data.noRecovery')}</p>
              )}
              {snapshot?.session.lastError && (
                <p className="muted">{snapshot.session.lastError}</p>
              )}
              {snapshot && (
                <p className="muted small">
                  {t('chat.usage.cost')}: {formatCost(snapshot.usage.cost ?? 0)}{' '}
                  · {formatTokens(snapshot.usage.contextUsed)} /{' '}
                  {formatTokens(snapshot.usage.contextWindow)}
                </p>
              )}
            </section>
          )}

          {tab === 'about' && (
            <section className="settings-section">
              <h3>{t('settings.about')}</h3>
              <dl className="about-list">
                <div>
                  <dt>{t('settings.about.version')}</dt>
                  <dd>{version ?? '0.2.0'}</dd>
                </div>
                <div>
                  <dt>{t('settings.about.configSource')}</dt>
                  <dd>
                    <code>{config.source}</code>
                  </dd>
                </div>
                <div>
                  <dt>{t('settings.about.configPath')}</dt>
                  <dd>
                    <code>{config.config_path ?? 'built-in'}</code>
                  </dd>
                </div>
                <div>
                  <dt>{t('settings.about.dataDir')}</dt>
                  <dd>
                    <code>{config.data_dir}</code>
                  </dd>
                </div>
                <div>
                  <dt>{t('settings.about.workspaceRoot')}</dt>
                  <dd>
                    <code>{config.workspace_root}</code>
                  </dd>
                </div>
                <div>
                  <dt>{t('chat.model')}</dt>
                  <dd>
                    <code>{session?.model ?? '—'}</code>
                  </dd>
                </div>
              </dl>
              <p className="muted">{t('settings.about.guide')}</p>
              <p className="muted small">
                <RefreshCw size={12} aria-hidden="true" />{' '}
                {t('app.connection.retryHint')}
              </p>
            </section>
          )}
        </div>
      </div>
    </Modal>
  );
}
