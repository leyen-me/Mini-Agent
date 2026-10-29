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
import { cn } from '@/lib/utils';

type Tone = 'neutral' | 'live' | 'ok' | 'warn' | 'bad';

const toneText: Record<Tone, string> = {
  neutral: 'text-muted-foreground',
  live: 'text-signal',
  ok: 'text-success',
  warn: 'text-warning',
  bad: 'text-destructive',
};

const sessionMeta: Record<SessionStatus, [typeof Circle, Tone]> = {
  idle: [Circle, 'neutral'],
  running: [Loader2, 'live'],
  waiting_user: [ShieldAlert, 'warn'],
  paused: [Pause, 'warn'],
  compacting: [RefreshCw, 'live'],
  error: [AlertTriangle, 'bad'],
};

export function SessionStatusBadge({
  status,
  compact = false,
}: {
  status: SessionStatus;
  compact?: boolean;
}) {
  const { t } = useUi();
  const [Icon, tone] = sessionMeta[status] ?? [Circle, 'neutral'];
  const spinning = status === 'running' || status === 'compacting';
  return (
    <span
      className={cn(
        'status-badge inline-flex items-center gap-1.5 whitespace-nowrap font-mono text-[11px]',
        toneText[tone],
      )}
      role="status"
      aria-label={t(`status.${status}`)}
      title={t(`status.${status}`)}
    >
      <Icon
        className={cn(compact ? 'size-3' : 'size-3.5', spinning && 'animate-spin')}
        aria-hidden="true"
      />
      <span>{t(`status.${status}`)}</span>
    </span>
  );
}

const toolMeta: Record<ToolStatus, [typeof Circle, Tone]> = {
  validating: [Loader2, 'live'],
  awaiting_permission: [ShieldAlert, 'warn'],
  running: [Loader2, 'live'],
  succeeded: [CheckCircle2, 'ok'],
  failed: [XCircle, 'bad'],
  denied: [Ban, 'bad'],
  cancelled: [Ban, 'neutral'],
  timeout: [Timer, 'bad'],
};

export function ToolStatusBadge({ status }: { status: ToolStatus }) {
  const { t } = useUi();
  const [Icon, tone] = toolMeta[status] ?? [Wrench, 'neutral'];
  const spinning = status === 'running' || status === 'validating';
  return (
    <span
      className={cn(
        `tool-status tool-${status} inline-flex items-center gap-1 whitespace-nowrap text-[11.5px] font-medium`,
        toneText[tone],
      )}
      role="status"
      aria-label={t(`tool.${status}`)}
    >
      <Icon
        className={cn('size-3.5', spinning && 'animate-spin')}
        aria-hidden="true"
      />
      <span>{t(`tool.${status}`)}</span>
    </span>
  );
}

const turnMeta: Record<TurnStatus, [typeof Circle, Tone]> = {
  running: [Loader2, 'live'],
  completed: [CheckCircle2, 'ok'],
  paused: [Pause, 'warn'],
  failed: [XCircle, 'bad'],
  cancelled: [Ban, 'neutral'],
};

export function TurnStatusBadge({ status }: { status: TurnStatus }) {
  const { t } = useUi();
  const [Icon, tone] = turnMeta[status] ?? [Circle, 'neutral'];
  return (
    <span
      className={cn(
        `turn-status turn-${status} inline-flex items-center gap-1.5 whitespace-nowrap text-[12px]`,
        status === 'completed' ? 'text-muted-foreground' : toneText[tone],
      )}
      role="status"
    >
      <Icon
        className={cn(
          'size-3.5',
          status === 'completed' && 'text-success',
          status === 'running' && 'animate-spin',
        )}
        aria-hidden="true"
      />
      <span>{t(`turn.${status}`)}</span>
    </span>
  );
}

const riskTone: Record<'low' | 'medium' | 'high', string> = {
  high: 'border-destructive/25 bg-danger-soft text-destructive',
  medium: 'border-warning/25 bg-warning-soft text-warning',
  low: 'border-success/25 bg-success-soft text-success',
};

export function RiskBadge({ risk }: { risk: 'low' | 'medium' | 'high' }) {
  const { t } = useUi();
  const Icon =
    risk === 'high' ? AlertTriangle : risk === 'medium' ? ShieldAlert : CheckCircle2;
  return (
    <span
      className={cn(
        `risk-badge risk-${risk} inline-flex items-center gap-1 border px-1.5 py-px font-mono text-[10.5px] whitespace-nowrap uppercase tracking-wide`,
        riskTone[risk],
      )}
    >
      <Icon className="size-3" aria-hidden="true" />
      <span>
        {t('chat.interaction.risk')}: {t(`chat.interaction.risk.${risk}`)}
      </span>
    </span>
  );
}
