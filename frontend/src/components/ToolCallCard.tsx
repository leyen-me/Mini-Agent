import { ArrowUpRight, ChevronRight } from 'lucide-react';
import { useState } from 'react';
import type { ToolCall } from '../api/types';
import { summarizeToolCall, toolCallDuration, toolIcon } from '../lib/tools';
import { useInspector } from '../state/InspectorContext';
import { cn } from '@/lib/utils';
import { ToolCallDetail } from './Inspector';
import { RiskBadge, ToolStatusBadge } from './StatusBadge';

export function ToolCallCard({ call }: { call: ToolCall }) {
  const inspector = useInspector();
  const [open, setOpen] = useState(false);
  const Icon = toolIcon(call.name);
  const elapsed = toolCallDuration(call);
  const selected = inspector?.selectedToolCallId === call.id;
  const expanded = !inspector && open;

  return (
    <div
      className={cn(
        `tool-card tool-card-${call.status} border border-border bg-background transition-colors`,
        'hover:border-border-strong',
        call.status === 'awaiting_permission' && 'border-warning/50',
        ['failed', 'denied', 'timeout'].includes(call.status) && 'border-destructive/35',
        selected && 'border-foreground hover:border-foreground',
      )}
    >
      <button
        type="button"
        onClick={() => (inspector ? inspector.focusToolCall(call.id) : setOpen((v) => !v))}
        aria-expanded={inspector ? undefined : expanded}
        aria-pressed={inspector ? selected : undefined}
        className="group flex h-10 w-full min-w-0 items-center gap-3 px-3 text-left"
      >
        <Icon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
        <span className="shrink-0 font-mono text-[12.5px] font-medium">{call.name}</span>
        <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-muted-foreground">
          {summarizeToolCall(call)}
        </span>
        <span className="flex shrink-0 items-center gap-3">
          {call.risk !== 'low' && <RiskBadge risk={call.risk} />}
          {elapsed && (
            <span className="hidden font-mono text-[11px] text-subtle tabular-nums sm:inline">
              {elapsed}
            </span>
          )}
          <ToolStatusBadge status={call.status} />
          {inspector ? (
            <ArrowUpRight
              className="size-3.5 text-subtle transition-colors group-hover:text-foreground"
              aria-hidden="true"
            />
          ) : (
            <ChevronRight
              className={cn('size-3.5 text-subtle transition-transform', expanded && 'rotate-90')}
              aria-hidden="true"
            />
          )}
        </span>
      </button>
      {expanded && <ToolCallDetail call={call} />}
    </div>
  );
}
