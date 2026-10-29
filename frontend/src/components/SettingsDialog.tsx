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
import { SimpleSelect } from '@/components/common/SimpleSelect';
import {
  GroupLabel,
  PanelHeading,
  RowGroup,
  SettingRow,
  SettingsShell,
} from '@/components/common/SettingsLayout';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Kbd } from '@/components/ui/kbd';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { cn } from '@/lib/utils';
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

  const adminButton = onOpenAdmin && (
    <Button variant="outline" size="sm" onClick={onOpenAdmin}>
      <SlidersHorizontal /> {t('app.admin')}
    </Button>
  );
  const muted = (text: string) => (
    <p className="py-4 text-[13px] text-muted-foreground">{text}</p>
  );
  const shortcuts: Array<[string[], string]> = [
    [['⌘/Ctrl', 'K'], t('app.shortcut.palette')],
    [['⌘/Ctrl', 'N'], t('app.shortcut.new')],
    [['Enter'], t('chat.send')],
    [['Shift', 'Enter'], t('chat.hint.newline')],
    [['Esc'], t('app.close')],
  ];

  return (
    <Modal
      open={open}
      title={t('settings.title')}
      onClose={onClose}
      wide
      bodyClassName="p-0 overflow-hidden"
    >
      <SettingsShell
        label={t('settings.title')}
        items={tabs}
        value={tab}
        onChange={setTab}
        footer={
          onOpenAdmin && (
            <Button
              variant="outline"
              className="w-full justify-start"
              onClick={() => {
                onClose();
                onOpenAdmin();
              }}
            >
              <SlidersHorizontal /> {t('app.admin')}
            </Button>
          )
        }
      >
        {tab === 'general' && (
          <section>
            <PanelHeading title={t('settings.general')} />
            <RowGroup>
              <SettingRow title={t('settings.general.language')}>
                <SimpleSelect
                  ariaLabel={t('settings.general.language')}
                  value={language}
                  onValueChange={(value) => onSetLanguage(value as Language)}
                  options={[
                    { value: 'zh-CN', label: '简体中文' },
                    { value: 'en-US', label: 'English' },
                  ]}
                />
              </SettingRow>
              <SettingRow title={t('settings.general.theme')}>
                <SimpleSelect
                  ariaLabel={t('settings.general.theme')}
                  value={themeSetting}
                  onValueChange={(value) => onSetTheme(value as ThemeSetting)}
                  options={[
                    { value: 'system', label: t('app.theme.system') },
                    { value: 'light', label: t('app.theme.light') },
                    { value: 'dark', label: t('app.theme.dark') },
                  ]}
                />
              </SettingRow>
              <SettingRow
                title={t('settings.general.notifications')}
                description={t('settings.general.notificationsHint')}
              >
                {notificationState !== 'granted' ? (
                  <Button variant="outline" size="sm" onClick={() => void requestNotifications()}>
                    <Bell /> {t('settings.notifications.grant')}
                  </Button>
                ) : (
                  <span className="inline-flex items-center gap-1 font-mono text-[11px] text-success">
                    <Check className="size-3" /> {t('settings.notifications.granted')}
                  </span>
                )}
                <Switch
                  checked={config.general.notifications}
                  onCheckedChange={onSetNotifications}
                  aria-label={t('settings.general.notifications')}
                />
              </SettingRow>
            </RowGroup>
            <GroupLabel>{t('settings.general.shortcuts')}</GroupLabel>
            <dl className="divide-y divide-border border-y border-border">
              {shortcuts.map(([keys, label]) => (
                <div key={label} className="flex items-center justify-between gap-4 py-3 text-[13px]">
                  <dd className="text-foreground/80">{label}</dd>
                  <dt className="flex items-center gap-1">
                    {keys.map((key) => (
                      <Kbd key={key}>{key}</Kbd>
                    ))}
                  </dt>
                </div>
              ))}
            </dl>
          </section>
        )}

        {tab === 'models' && (
          <section>
            <PanelHeading title={t('settings.models')} actions={adminButton} />
            <RowGroup>
              <SettingRow
                title={t('settings.models.default')}
                description={t('settings.models.defaultHint')}
              >
                <SimpleSelect
                  ariaLabel={t('settings.models.default')}
                  value={defaultModel}
                  onValueChange={setDefaultModel}
                  options={config.models.map((model) => ({ value: model.ref, label: model.name }))}
                />
              </SettingRow>
              <SettingRow
                htmlFor="settings-fallback"
                title={t('settings.models.fallback')}
                description={t('settings.models.fallbackHint')}
              >
                <Input
                  id="settings-fallback"
                  value={fallbackModels}
                  onChange={(event) => setFallbackModels(event.target.value)}
                  placeholder="mock/backup"
                  className="font-mono text-[12.5px]"
                />
              </SettingRow>
              <SettingRow
                htmlFor="settings-vision"
                title={t('settings.models.vision')}
                description={t('settings.models.visionHint')}
              >
                <Input
                  id="settings-vision"
                  value={visionModel}
                  onChange={(event) => setVisionModel(event.target.value)}
                  placeholder="provider/vision"
                  className="font-mono text-[12.5px]"
                />
              </SettingRow>
              <SettingRow
                htmlFor="settings-compaction-model"
                title={t('settings.models.compaction')}
                description={t('settings.models.compactionHint')}
              >
                <Input
                  id="settings-compaction-model"
                  value={compactionModel}
                  onChange={(event) => setCompactionModel(event.target.value)}
                  placeholder="provider/model"
                  className="font-mono text-[12.5px]"
                />
              </SettingRow>
              <SettingRow
                title={t('settings.models.compactionMode')}
                description={t('settings.models.compactionModeHint')}
              >
                <div className="flex flex-wrap items-center gap-4">
                  <Label className="gap-2 text-[13px] font-normal">
                    <Checkbox
                      checked={useModelSummary}
                      onCheckedChange={(value) => setUseModelSummary(value === true)}
                    />
                    {t('settings.models.modelSummary')}
                  </Label>
                  <Label className="gap-2 text-[13px] font-normal">
                    <Checkbox
                      checked={reloadRules}
                      onCheckedChange={(value) => setReloadRules(value === true)}
                    />
                    {t('settings.models.reloadRules')}
                  </Label>
                </div>
              </SettingRow>
            </RowGroup>

            <GroupLabel>{t('settings.runtime')}</GroupLabel>
            <RowGroup>
              <SettingRow title={t('settings.runtime.sandbox')} description={t('settings.runtime.sandboxHint')}>
                <SimpleSelect
                  ariaLabel={t('settings.runtime.sandbox')}
                  value={sandboxMode}
                  onValueChange={setSandboxMode}
                  options={['auto', 'sandbox-exec', 'bwrap', 'docker', 'none'].map((value) => ({
                    value,
                    label: <span className="font-mono text-[12.5px]">{value}</span>,
                  }))}
                />
              </SettingRow>
              <SettingRow
                title={t('settings.runtime.network')}
                description={t('settings.runtime.networkHint')}
              >
                <Switch
                  checked={allowNetwork}
                  onCheckedChange={setAllowNetwork}
                  aria-label={t('settings.runtime.network')}
                />
              </SettingRow>
              <SettingRow
                htmlFor="settings-proxy"
                title={t('settings.runtime.proxy')}
                description={t('settings.runtime.proxyHint')}
              >
                <Input
                  id="settings-proxy"
                  value={proxy}
                  onChange={(event) => setProxy(event.target.value)}
                  placeholder="http://127.0.0.1:7890"
                  className="font-mono text-[12.5px]"
                />
              </SettingRow>
              <SettingRow
                htmlFor="settings-ca"
                title={t('settings.runtime.ca')}
                description={t('settings.runtime.caHint')}
              >
                <Input
                  id="settings-ca"
                  value={caBundle}
                  onChange={(event) => setCaBundle(event.target.value)}
                  placeholder="/path/to/ca.pem"
                  className="font-mono text-[12.5px]"
                />
              </SettingRow>
              <SettingRow title={t('settings.runtime.search')} description={t('settings.runtime.searchHint')}>
                <SimpleSelect
                  ariaLabel={t('settings.runtime.search')}
                  value={searchProvider}
                  onValueChange={setSearchProvider}
                  className="flex-1"
                  options={[
                    { value: 'duckduckgo', label: 'DuckDuckGo' },
                    { value: 'tavily', label: 'Tavily' },
                  ]}
                />
                <Input
                  value={searchResults}
                  onChange={(event) => setSearchResults(event.target.value)}
                  aria-label={t('settings.runtime.searchResults')}
                  inputMode="numeric"
                  className="w-16 text-center font-mono"
                />
              </SettingRow>
            </RowGroup>
            <div className="mt-6 flex justify-end">
              <Button disabled={saving} onClick={() => void saveDefaults()}>
                <Check /> {t('settings.models.reload')}
              </Button>
            </div>

            <GroupLabel>{t('settings.models.providers')}</GroupLabel>
            <p className="mb-4 text-[12.5px] text-muted-foreground">{t('settings.models.keyHint')}</p>
            <div className="grid gap-px border border-border bg-border sm:grid-cols-2">
              {config.providers.map((provider) => (
                <article key={provider.id} className="bg-background p-5">
                  <header className="flex items-center gap-2">
                    <strong className="text-[14px] font-semibold">{provider.name}</strong>
                    <span
                      className={cn(
                        'ml-auto border px-1.5 font-mono text-[10px] leading-[18px] uppercase',
                        provider.configured
                          ? 'border-success/30 bg-success-soft text-success'
                          : 'border-destructive/30 bg-danger-soft text-destructive',
                      )}
                    >
                      {provider.configured
                        ? t('settings.models.configured')
                        : t('settings.models.notConfigured')}
                    </span>
                  </header>
                  <p className="mt-1.5 truncate font-mono text-[11.5px] text-muted-foreground">
                    {provider.protocol} · {provider.base_url || 'local'}
                    {provider.key_hint && ` · ${provider.key_hint}`}
                  </p>
                  <ul className="mt-4 space-y-1.5 border-t border-border pt-3">
                    {provider.models.map((model) => (
                      <li key={model.id} className="flex items-center justify-between gap-3 text-[12px]">
                        <code className="truncate font-mono">
                          {provider.id}/{model.id}
                        </code>
                        <span className="shrink-0 font-mono text-muted-foreground">
                          {formatTokens(model.context_window)}
                        </span>
                      </li>
                    ))}
                  </ul>
                </article>
              ))}
            </div>
            {config.config_path && (
              <p className="mt-4 text-[12px] text-muted-foreground">
                {t('settings.models.rawConfig')}:{' '}
                <code className="font-mono">{config.config_path}</code>
              </p>
            )}
          </section>
        )}

        {tab === 'agents' && (
          <section>
            <PanelHeading title={t('settings.agents.builtin')} actions={adminButton} />
            <div className="grid gap-px border border-border bg-border sm:grid-cols-2">
              {agents.map((agent) => {
                const current = session?.agentId === agent.id;
                return (
                  <article
                    key={agent.id}
                    className={cn(
                      'flex flex-col gap-3 bg-background p-5',
                      current && 'shadow-[inset_0_2px_0_var(--foreground)]',
                    )}
                  >
                    <header className="flex items-center gap-2">
                      <Bot className="size-4 text-muted-foreground" aria-hidden="true" />
                      <strong className="text-[14px] font-semibold">{agent.name}</strong>
                      {agent.readOnly && (
                        <span className="border border-border px-1.5 font-mono text-[10px] leading-[18px] text-muted-foreground uppercase">
                          {t('settings.agents.readOnly')}
                        </span>
                      )}
                      {current && (
                        <span className="ml-auto bg-foreground px-1.5 font-mono text-[10px] leading-[18px] text-background uppercase">
                          {t('settings.agents.current')}
                        </span>
                      )}
                    </header>
                    <p className="text-[13px] leading-relaxed text-foreground/80">{agent.description}</p>
                    <p className="text-[12px] text-muted-foreground">
                      {agent.id === 'plan' ? t('chat.mode.planHint') : t('chat.mode.buildHint')}
                    </p>
                    {session && onSwitchAgent && !current && (
                      <Button
                        variant="outline"
                        size="sm"
                        className="mt-auto self-start"
                        onClick={() => void onSwitchAgent(agent.id)}
                      >
                        {t('settings.agents.switch')}
                      </Button>
                    )}
                  </article>
                );
              })}
            </div>
            <p className="mt-4 text-[12px] text-muted-foreground">{t('settings.about.guide')}</p>
          </section>
        )}

        {tab === 'permissions' && (
          <section>
            <PanelHeading title={t('settings.permissions')} />
            <GroupLabel>{t('settings.permissions.defaults')}</GroupLabel>
            <div className="border border-border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t('settings.permissions.tool')}</TableHead>
                    <TableHead>{t('settings.permissions.ask')}</TableHead>
                    <TableHead>{t('settings.permissions.allow')}</TableHead>
                    <TableHead>{t('settings.permissions.deny')}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {Object.entries(config.permissions).map(([category, rule]) => (
                    <TableRow key={category}>
                      <TableCell>{t(`settings.permissions.${category}`)}</TableCell>
                      <TableCell>
                        <span className="border border-border px-1.5 font-mono text-[11px]">
                          {rule.default}
                        </span>
                      </TableCell>
                      <TableCell className="font-mono text-[12px] whitespace-normal">
                        {rule.allow.join(', ') || '—'}
                      </TableCell>
                      <TableCell className="font-mono text-[12px] whitespace-normal">
                        {rule.deny.join(', ') || '—'}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
            <GroupLabel>{t('settings.permissions.grants')}</GroupLabel>
            {grants.length === 0 ? (
              muted(t('settings.permissions.noGrants'))
            ) : (
              <div className="border border-border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>{t('settings.permissions.tool')}</TableHead>
                      <TableHead>{t('settings.permissions.pattern')}</TableHead>
                      <TableHead>{t('settings.permissions.scope')}</TableHead>
                      <TableHead />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {grants.map((grant) => (
                      <TableRow key={grant.id}>
                        <TableCell className="font-mono text-[12px]">{grant.tool}</TableCell>
                        <TableCell className="font-mono text-[12px] whitespace-normal">
                          {grant.pattern}
                        </TableCell>
                        <TableCell>{grant.scope}</TableCell>
                        <TableCell className="text-right">
                          <Button
                            variant="ghost"
                            size="xs"
                            className="text-destructive hover:bg-danger-soft hover:text-destructive"
                            onClick={() => void revokeGrant(grant)}
                          >
                            <Trash2 /> {t('settings.permissions.revoke')}
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </section>
        )}

        {tab === 'data' && (
          <section>
            <PanelHeading title={t('settings.data')} actions={adminButton} />
            <RowGroup>
              <SettingRow title={t('settings.data.export')} description={t('settings.data.exportHint')}>
                <Button variant="outline" size="sm" onClick={() => onExport('markdown')}>
                  <Download /> Markdown
                </Button>
                <Button variant="outline" size="sm" onClick={() => onExport('json')}>
                  <Download /> JSON
                </Button>
              </SettingRow>
              <SettingRow title={t('settings.data.share')} description={t('settings.data.shareExpiry')}>
                <Button size="sm" disabled={!session} onClick={() => void createShare()}>
                  <Link2 /> {t('settings.data.createShare')}
                </Button>
              </SettingRow>
            </RowGroup>
            {shares.length === 0 ? (
              muted(t('settings.data.noShares'))
            ) : (
              <ul className="mt-4 divide-y divide-border border border-border">
                {shares.map((share) => (
                  <li
                    key={share.id}
                    className={cn('flex items-center gap-3 px-4 py-2.5 text-[12.5px]', share.revoked && 'opacity-50')}
                  >
                    <Link2 className="size-3.5 text-muted-foreground" aria-hidden="true" />
                    <code className="font-mono">/share/{share.token.slice(0, 10)}…</code>
                    <span className="ml-auto font-mono text-[11px] text-muted-foreground">
                      {share.expiresAt ? formatDateTime(share.expiresAt, language) : '—'}
                    </span>
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      title={t('app.copy')}
                      aria-label={t('app.copy')}
                      onClick={() => void copyShare(share)}
                    >
                      <Copy />
                    </Button>
                    {!share.revoked && (
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        className="hover:bg-danger-soft hover:text-destructive"
                        title={t('settings.data.revoke')}
                        aria-label={t('settings.data.revoke')}
                        onClick={() => void revokeShare(share)}
                      >
                        <X />
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            )}

            <GroupLabel>{t('settings.data.debug')}</GroupLabel>
            <p className="mb-3 text-[12.5px] text-muted-foreground">{t('settings.data.debugHint')}</p>
            {debugLoading && muted(`${t('app.loading')}…`)}
            {!debugLoading && debug.length === 0 && muted(t('settings.data.debugEmpty'))}
            {debug.length > 0 && (
              <div className="border border-border">
                <Table className="font-mono text-[12px] tabular-nums">
                  <TableHeader>
                    <TableRow>
                      <TableHead>#</TableHead>
                      <TableHead>prefix</TableHead>
                      <TableHead>{t('chat.usage.cacheHit')}</TableHead>
                      <TableHead>{t('chat.usage.cacheMiss')}</TableHead>
                      <TableHead>retries</TableHead>
                      <TableHead>ms</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {debug.map((row) => (
                      <TableRow key={row.id}>
                        <TableCell>{row.step}</TableCell>
                        <TableCell>{row.prefixHash.slice(0, 12)}</TableCell>
                        <TableCell>{row.cacheHitTokens}</TableCell>
                        <TableCell>{row.cacheMissTokens}</TableCell>
                        <TableCell>{row.retries}</TableCell>
                        <TableCell>{row.durationMs}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}

            <GroupLabel>{t('settings.data.recovery')}</GroupLabel>
            {muted(snapshot?.session.lastError ?? t('settings.data.noRecovery'))}
            {snapshot && (
              <p className="font-mono text-[12px] text-muted-foreground">
                {t('chat.usage.cost')}: {formatCost(snapshot.usage.cost ?? 0)} ·{' '}
                {formatTokens(snapshot.usage.contextUsed)} / {formatTokens(snapshot.usage.contextWindow)}
              </p>
            )}
          </section>
        )}

        {tab === 'about' && (
          <section>
            <PanelHeading title={t('settings.about')} />
            <dl className="divide-y divide-border border-y border-border">
              {(
                [
                  [t('settings.about.version'), version ?? '0.2.0'],
                  [t('settings.about.configSource'), config.source],
                  [t('settings.about.configPath'), config.config_path ?? 'built-in'],
                  [t('settings.about.dataDir'), config.data_dir],
                  [t('settings.about.workspaceRoot'), config.workspace_root],
                  [t('chat.model'), session?.model ?? '—'],
                ] as Array<[string, string]>
              ).map(([label, value]) => (
                <div key={label} className="grid gap-1 py-3 sm:grid-cols-[160px_minmax(0,1fr)] sm:gap-6">
                  <dt className="text-[13px] text-muted-foreground">{label}</dt>
                  <dd className="font-mono text-[12.5px] break-all">{value}</dd>
                </div>
              ))}
            </dl>
            <p className="mt-6 text-[13px] leading-relaxed text-muted-foreground">
              {t('settings.about.guide')}
            </p>
            <p className="mt-2 flex items-center gap-1.5 text-[12px] text-muted-foreground">
              <RefreshCw className="size-3" aria-hidden="true" /> {t('app.connection.retryHint')}
            </p>
          </section>
        )}
      </SettingsShell>
    </Modal>
  );
}
