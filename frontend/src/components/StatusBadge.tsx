import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  Circle,
  Loader2,
  Pause,
  RefreshCw,
  ShieldAlert,
  Timer,
  Wrench,
  XCircle,
} from 'lucide-react';
import type { SessionStatus, ToolStatus, TurnStatus } from '../api/types';
import { useUi } from '../state/UiContext';

const sessionIcons: Record<SessionStatus, typeof Circle> = {
  idle: Circle,
  running: Loader2,
  waiting_user: ShieldAlert,
  paused: Pause,
  compacting: RefreshCw,
  error: AlertTriangle,
};

export function SessionStatusBadge({ status, compact = false }: { status: SessionStatus; compact?: boolean }) {
  const { t } = useUi();
  const Icon = sessionIcons[status] ?? Circle;
  const spinning = status === 'running' || status === 'compacting';
  return (
    <span className={`status-badge status-${status}`} role="status" aria-label={t(`status.${status}`)} title={t(`status.${status}`)}>
      <Icon size={compact ? 12 : 14} aria-hidden="true" className={spinning ? 'spin' : undefined} />
      <span className="status-label">{t(`status.${status}`)}</span>
    </span>
  );
}

const toolIcons: Record<ToolStatus, typeof Circle> = {
  validating: Loader2,
  awaiting_permission: ShieldAlert,
  running: Loader2,
  succeeded: CheckCircle2,
  failed: XCircle,
  denied: Ban,
  cancelled: Ban,
  timeout: Timer,
};

export function ToolStatusBadge({ status }: { status: ToolStatus }) {
  const { t } = useUi();
  const Icon = toolIcons[status] ?? Wrench;
  const spinning = status === 'running' || status === 'validating';
  return (
    <span className={`tool-status tool-${status}`} role="status" aria-label={t(`tool.${status}`)}>
      <Icon size={13} aria-hidden="true" className={spinning ? 'spin' : undefined} />
      <span>{t(`tool.${status}`)}</span>
    </span>
  );
}

const turnIcons: Record<TurnStatus, typeof Circle> = {
  running: Loader2,
  completed: CheckCircle2,
  paused: Pause,
  failed: XCircle,
  cancelled: Ban,
};

export function TurnStatusBadge({ status }: { status: TurnStatus }) {
  const { t } = useUi();
  const Icon = turnIcons[status] ?? Circle;
  return (
    <span className={`turn-status turn-${status}`} role="status">
      <Icon size={12} aria-hidden="true" className={status === 'running' ? 'spin' : undefined} />
      <span>{t(`turn.${status}`)}</span>
    </span>
  );
}

export function RiskBadge({ risk }: { risk: 'low' | 'medium' | 'high' }) {
  const { t } = useUi();
  const Icon = risk === 'high' ? AlertTriangle : risk === 'medium' ? ShieldAlert : CheckCircle2;
  return (
    <span className={`risk-badge risk-${risk}`}>
      <Icon size={12} aria-hidden="true" />
      <span>
        {t('chat.interaction.risk')}: {t(`chat.interaction.risk.${risk}`)}
      </span>
    </span>
  );
}
