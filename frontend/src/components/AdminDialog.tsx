import {
  Bot,
  Boxes,
  Database,
  FileTerminal,
  Plug,
  Server,
  Sparkles,
  Webhook,
  Wrench,
} from 'lucide-react';
import type { AgentInfo, PublicConfig, Session, Workspace } from '../api/types';
import { useUi } from '../state/UiContext';
import { AgentsPanel } from './admin/AgentsPanel';
import { CommandsPanel } from './admin/CommandsPanel';
import { DataPanel } from './admin/DataPanel';
import { HooksPanel } from './admin/HooksPanel';
import { McpPanel } from './admin/McpPanel';
import { PluginsPanel } from './admin/PluginsPanel';
import { ProvidersPanel } from './admin/ProvidersPanel';
import { SkillsPanel } from './admin/SkillsPanel';
import { WebhooksPanel } from './admin/WebhooksPanel';
import { SettingsShell } from '@/components/common/SettingsLayout';
import { Modal } from './Modal';

export type AdminTab =
  | 'providers'
  | 'agents'
  | 'skills'
  | 'mcp'
  | 'commands'
  | 'plugins'
  | 'hooks'
  | 'webhooks'
  | 'data';

interface Props {
  open: boolean;
  tab: AdminTab;
  onTabChange: (tab: AdminTab) => void;
  onClose: () => void;
  config: PublicConfig;
  agents: AgentInfo[];
  workspaces: Workspace[];
  activeWorkspace: string | null;
  session: Session | null;
  onToast: (message: string, kind?: 'info' | 'error' | 'success') => void;
  onReload: () => Promise<void>;
}

const TABS: Array<{
  id: AdminTab;
  label: string;
  en: string;
  icon: typeof Bot;
}> = [
  { id: 'providers', label: '服务商', en: 'Providers', icon: Server },
  { id: 'agents', label: 'Agent', en: 'Agents', icon: Bot },
  { id: 'skills', label: 'Skill', en: 'Skills', icon: Sparkles },
  { id: 'mcp', label: 'MCP', en: 'MCP', icon: Boxes },
  { id: 'commands', label: '命令', en: 'Commands', icon: FileTerminal },
  { id: 'plugins', label: '插件', en: 'Plugins', icon: Plug },
  { id: 'hooks', label: 'Hook', en: 'Hooks', icon: Wrench },
  { id: 'webhooks', label: 'Webhook', en: 'Webhooks', icon: Webhook },
  { id: 'data', label: '数据与运维', en: 'Data & Ops', icon: Database },
];

export function AdminDialog({
  open,
  tab,
  onTabChange,
  onClose,
  config,
  agents,
  workspaces,
  activeWorkspace,
  session,
  onToast,
  onReload,
}: Props) {
  const { language } = useUi();
  const zh = language === 'zh-CN';
  return (
    <Modal
      open={open}
      title={zh ? '管理中心' : 'Admin Center'}
      onClose={onClose}
      wide
      bodyClassName="p-0 overflow-hidden"
    >
      <SettingsShell
        label={zh ? '管理分类' : 'Admin sections'}
        items={TABS.map((item) => ({
          id: item.id,
          label: zh ? item.label : item.en,
          icon: item.icon,
        }))}
        value={tab}
        onChange={onTabChange}
        footer={
          <div className="space-y-1 px-3 font-mono text-[10.5px] text-muted-foreground">
            <div>{config.models.length} models</div>
            <div className="truncate">
              {workspaces.find((item) => item.id === activeWorkspace)?.alias || ''}
            </div>
          </div>
        }
      >
          {tab === 'providers' && (
            <ProvidersPanel onToast={onToast} onReload={onReload} />
          )}
          {tab === 'agents' && (
            <AgentsPanel
              agents={agents}
              onToast={onToast}
              onReload={onReload}
            />
          )}
          {tab === 'skills' && (
            <SkillsPanel
              session={session}
              activeWorkspace={activeWorkspace}
              onToast={onToast}
            />
          )}
          {tab === 'mcp' && <McpPanel session={session} onToast={onToast} />}
          {tab === 'commands' && (
            <CommandsPanel agents={agents} onToast={onToast} />
          )}
          {tab === 'plugins' && (
            <PluginsPanel onToast={onToast} onReload={onReload} />
          )}
          {tab === 'hooks' && <HooksPanel onToast={onToast} />}
          {tab === 'webhooks' && <WebhooksPanel onToast={onToast} />}
          {tab === 'data' && (
            <DataPanel
              workspaces={workspaces}
              onToast={onToast}
              onReload={onReload}
            />
          )}
      </SettingsShell>
    </Modal>
  );
}
