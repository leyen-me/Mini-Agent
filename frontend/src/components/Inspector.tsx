import {
  CheckCircle2,
  ChevronRight,
  Circle,
  GitBranch,
  Loader2,
  OctagonX,
  PanelRightClose,
  Pencil,
  Shrink,
  Target,
} from 'lucide-react';
import { useEffect, useMemo, useRef, type ReactNode } from 'react';
import type { GoalTask, Snapshot, TaskInfo, ToolCall } from '../api/types';
import { formatCost, formatTokens } from '../lib/i18n';
import {
  summarizeToolCall,
  toolCallDiff,
  toolCallDuration,
  toolIcon,
} from '../lib/tools';
import type { InspectorTab } from '../state/InspectorContext';
import { useUi } from '../state/UiContext';
import { IconButton } from '@/components/common/IconButton';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { cn } from '@/lib/utils';
import { CopyButton } from './CopyButton';
import { DiffView } from './DiffView';
import { FileTree } from './FileTree';
import { SessionStatusBadge, ToolStatusBadge } from './StatusBadge';

interface Props {
  snapshot: Snapshot | null;
  workspaceId: string | null;
  tab: InspectorTab;
  selectedToolCallId: string | null;
  onTabChange: (tab: InspectorTab) => void;
  onSelectToolCall: (id: string | null) => void;
  onClose?: () => void;
  onReferenceFile: (path: string) => void;
  onOpenGoal: () => void;
  onCompact: () => void;
  onSelectSession: (id: string) => void;
}

const TABS: InspectorTab[] = ['activity', 'tasks', 'files', 'context'];

export function Inspector({
  snapshot,
  workspaceId,
  tab,
  selectedToolCallId,
  onTabChange,
  onSelectToolCall,
  onClose,
  onReferenceFile,
  onOpenGoal,
  onCompact,
  onSelectSession,
}: Props) {
  const { t } = useUi();
  const toolCalls = useMemo(
    () => [...(snapshot?.toolCalls ?? [])].reverse(),
    [snapshot?.toolCalls],
  );
  const runningTasks =
    snapshot?.tasks.filter((task) => task.status === 'running' || task.status === 'queued')
      .length ?? 0;

  return (
    <aside
      className="inspector flex h-full min-h-0 w-full flex-col bg-sidebar"
      aria-label={t('inspector.title')}
    >
      <Tabs
        value={tab}
        onValueChange={(value) => onTabChange(value as InspectorTab)}
        className="flex min-h-0 flex-1 flex-col gap-0"
      >
        <div className="flex h-14 shrink-0 items-center gap-2 border-b border-border pr-2 pl-4">
          <TabsList variant="line" className="h-full gap-4 p-0">
            {TABS.map((value) => (
              <TabsTrigger
                key={value}
                value={value}
                className="h-full flex-none px-0 text-[13px] after:!bottom-0"
              >
                {t(`inspector.${value}`)}
                {value === 'activity' && toolCalls.length > 0 && (
                  <Count>{toolCalls.length}</Count>
                )}
                {value === 'tasks' && runningTasks > 0 && <Count live>{runningTasks}</Count>}
              </TabsTrigger>
            ))}
          </TabsList>
          {onClose && (
            <IconButton
              label={t('inspector.close')}
              className="ml-auto"
              onClick={onClose}
            >
              <PanelRightClose />
            </IconButton>
          )}
        </div>

        <TabsContent value="activity" className="min-h-0 flex-1 overflow-y-auto">
          <ActivityList
            calls={toolCalls}
            selectedId={selectedToolCallId}
            onSelect={onSelectToolCall}
          />
        </TabsContent>

        <TabsContent value="tasks" className="min-h-0 flex-1 overflow-y-auto">
          <TasksPanel
            snapshot={snapshot}
            onOpenGoal={onOpenGoal}
            onSelectSession={onSelectSession}
          />
        </TabsContent>

        <TabsContent value="files" className="flex min-h-0 flex-1 flex-col">
          {workspaceId && (
            <FileTree workspaceId={workspaceId} onReference={onReferenceFile} />
          )}
        </TabsContent>

        <TabsContent value="context" className="min-h-0 flex-1 overflow-y-auto">
          <ContextPanel snapshot={snapshot} onCompact={onCompact} />
        </TabsContent>
      </Tabs>
    </aside>
  );
}

function Count({ children, live = false }: { children: ReactNode; live?: boolean }) {
  return (
    <span
      className={cn(
        'ml-1 font-mono text-[10.5px] tabular-nums text-muted-foreground',
        live && 'text-signal',
      )}
    >
      {children}
    </span>
  );
}

function SectionLabel({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex items-center justify-between px-5 pt-5 pb-3">
      <span className="eyebrow">{children}</span>
      {action}
    </div>
  );
}

function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="mx-5 my-5 border border-dashed border-border-strong px-5 py-8 text-center">
      <p className="text-[13px] font-medium">{title}</p>
      {hint && <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">{hint}</p>}
    </div>
  );
}

/* ------------------------------------------------------------------ activity */

function ActivityList({
  calls,
  selectedId,
  onSelect,
}: {
  calls: ToolCall[];
  selectedId: string | null;
  onSelect: (id: string | null) => void;
}) {
  const { t } = useUi();
  const selectedRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    selectedRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [selectedId]);

  if (!calls.length) {
    return (
      <EmptyState title={t('inspector.noActivity')} hint={t('inspector.noActivityHint')} />
    );
  }
  return (
    <ol className="divide-y divide-border border-b border-border">
      {calls.map((call, index) => {
        const open = call.id === selectedId;
        const Icon = toolIcon(call.name);
        return (
          <li key={call.id}>
            <div ref={open ? selectedRef : undefined}>
              <button
                type="button"
                aria-expanded={open}
                onClick={() => onSelect(open ? null : call.id)}
                className={cn(
                  'grid w-full grid-cols-[auto_1fr_auto] items-center gap-x-3 gap-y-1 px-5 py-3 text-left transition-colors hover:bg-accent',
                  open && 'bg-background shadow-[inset_2px_0_0_var(--foreground)] hover:bg-background',
                )}
              >
                <span className="font-mono text-[10.5px] text-subtle tabular-nums">
                  {String(calls.length - index).padStart(2, '0')}
                </span>
                <span className="flex min-w-0 items-center gap-2">
                  <Icon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
                  <span className="truncate font-mono text-[12.5px] font-medium">{call.name}</span>
                </span>
                <ToolStatusBadge status={call.status} />
                <span />
                <span className="col-span-2 truncate font-mono text-[11.5px] text-muted-foreground">
                  {summarizeToolCall(call)}
                </span>
              </button>
              {open && <ToolCallDetail call={call} />}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

export function ToolCallDetail({ call }: { call: ToolCall }) {
  const { t } = useUi();
  const diff = toolCallDiff(call);
  const input = JSON.stringify(call.input, null, 2);
  const output = call.output ? JSON.stringify(call.output, null, 2) : '';
  const elapsed = toolCallDuration(call);
  return (
    <div className="space-y-4 border-t border-border bg-background px-5 py-4 animate-in fade-in-0">
      <dl className="grid grid-cols-3 gap-px border border-border bg-border text-[12px]">
        <Meta label={t('chat.tool')} value={call.name} mono />
        <Meta label={t('chat.toolDuration')} value={elapsed ?? '—'} mono />
        <Meta label={t('chat.interaction.risk')} value={t(`chat.interaction.risk.${call.risk}`)} />
      </dl>
      <CodePane title={t('chat.toolInput')} copyLabel={t('chat.toolCopyInput')} text={input} />
      <CodePane
        title={t('chat.toolOutput')}
        copyLabel={t('chat.toolCopyOutput')}
        text={output}
        empty={t('chat.toolNoOutput')}
      />
      {diff && (
        <section className="tool-diff space-y-2">
          <header className="flex items-center justify-between">
            <span className="eyebrow">{t('chat.diff')}</span>
            <CopyButton text={diff} label={t('chat.diff')} iconOnly />
          </header>
          <DiffView diff={diff} />
        </section>
      )}
      {call.output?.truncated && (
        <p className="text-xs text-muted-foreground">{call.output.artifacts.join(', ')}</p>
      )}
      {call.errorCode && (
        <p className="font-mono text-xs text-destructive">{call.errorCode}</p>
      )}
    </div>
  );
}

function Meta({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="min-w-0 bg-background px-3 py-2">
      <dt className="eyebrow text-[9.5px]">{label}</dt>
      <dd className={cn('mt-1 truncate', mono && 'font-mono')}>{value}</dd>
    </div>
  );
}

function CodePane({
  title,
  copyLabel,
  text,
  empty,
}: {
  title: string;
  copyLabel: string;
  text: string;
  empty?: string;
}) {
  return (
    <section className="space-y-2">
      <header className="flex items-center justify-between">
        <span className="eyebrow">{title}</span>
        {text && <CopyButton text={text} label={copyLabel} iconOnly />}
      </header>
      <pre className="max-h-72 overflow-auto border border-code-border bg-code px-3.5 py-3 font-mono text-[11.5px] leading-relaxed break-words whitespace-pre-wrap text-code-foreground">
        {text || empty}
      </pre>
    </section>
  );
}

/* --------------------------------------------------------------------- tasks */

const goalTaskIcon: Record<GoalTask['status'], [typeof Circle, string]> = {
  completed: [CheckCircle2, 'text-success'],
  in_progress: [Loader2, 'text-signal animate-spin'],
  blocked: [OctagonX, 'text-destructive'],
  pending: [Circle, 'text-subtle'],
};

const taskTone: Record<TaskInfo['status'], string> = {
  queued: 'text-muted-foreground',
  running: 'text-signal',
  succeeded: 'text-success',
  failed: 'text-destructive',
  cancelled: 'text-muted-foreground',
};

function TasksPanel({
  snapshot,
  onOpenGoal,
  onSelectSession,
}: {
  snapshot: Snapshot | null;
  onOpenGoal: () => void;
  onSelectSession: (id: string) => void;
}) {
  const { t } = useUi();
  const goal = snapshot?.goal ?? null;
  const tasks = snapshot?.tasks ?? [];
  const children = snapshot?.childSessions ?? [];
  const done = goal?.tasks.filter((task) => task.status === 'completed').length ?? 0;
  const total = goal?.tasks.length ?? 0;
  const percent = total ? Math.round((done / total) * 100) : 0;

  return (
    <div className="pb-6">
      <SectionLabel
        action={
          <Button variant="ghost" size="xs" onClick={onOpenGoal}>
            {goal ? <Pencil /> : <Target />}
            {goal ? t('app.edit') : t('chat.goal.set')}
          </Button>
        }
      >
        {t('chat.goal.objective')}
      </SectionLabel>
      {goal ? (
        <div className="mx-5 border border-border bg-background">
          <div className="space-y-2 p-4">
            <p className="text-[14px] leading-snug font-medium">{goal.objective}</p>
            {goal.criteria && (
              <p className="text-[12.5px] leading-relaxed text-muted-foreground">
                {goal.criteria}
              </p>
            )}
          </div>
          {total > 0 && (
            <>
              <div className="flex items-center gap-3 border-t border-border px-4 py-2.5">
                <div className="h-1 flex-1 bg-muted">
                  <div
                    className="h-full bg-foreground transition-[width] duration-500"
                    style={{ width: `${percent}%` }}
                  />
                </div>
                <span className="font-mono text-[11px] tabular-nums text-muted-foreground">
                  {done}/{total}
                </span>
              </div>
              <ul className="divide-y divide-border border-t border-border">
                {goal.tasks.map((task) => {
                  const [Icon, tone] = goalTaskIcon[task.status] ?? goalTaskIcon.pending;
                  return (
                    <li key={task.id} className="flex items-start gap-2.5 px-4 py-2.5">
                      <Icon className={cn('mt-0.5 size-3.5 shrink-0', tone)} aria-hidden="true" />
                      <span
                        className={cn(
                          'text-[13px] leading-snug',
                          task.status === 'completed' && 'text-muted-foreground line-through',
                        )}
                      >
                        {task.title}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </>
          )}
        </div>
      ) : (
        <EmptyState title={t('chat.goal.none')} hint={t('inspector.noGoalHint')} />
      )}

      <SectionLabel>{t('chat.tasks')}</SectionLabel>
      {tasks.length ? (
        <ul className="mx-5 divide-y divide-border border border-border bg-background">
          {tasks.map((task) => (
            <li key={task.id} className="space-y-2 px-4 py-3">
              <div className="flex items-center gap-2">
                <span className="min-w-0 flex-1 truncate font-mono text-[12.5px]">{task.name}</span>
                <span className={cn('font-mono text-[11px]', taskTone[task.status])}>
                  {t(`chat.task.${task.status}`)}
                </span>
              </div>
              {(task.status === 'running' || task.status === 'queued') && (
                <div className="h-0.5 bg-muted">
                  <div
                    className="h-full bg-signal transition-[width] duration-500"
                    style={{ width: `${Math.max(4, Math.round(task.progress * 100))}%` }}
                  />
                </div>
              )}
              {(task.summary || task.error) && (
                <p
                  className={cn(
                    'line-clamp-3 text-[12px] leading-relaxed text-muted-foreground',
                    task.error && 'text-destructive',
                  )}
                >
                  {task.error ?? task.summary}
                </p>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <EmptyState title={t('inspector.noTasks')} />
      )}

      {children.length > 0 && (
        <>
          <SectionLabel>{t('inspector.subagents')}</SectionLabel>
          <ul className="mx-5 divide-y divide-border border border-border bg-background">
            {children.map((child) => (
              <li key={child.id}>
                <button
                  type="button"
                  onClick={() => onSelectSession(child.id)}
                  className="flex w-full items-center gap-2 px-4 py-2.5 text-left transition-colors hover:bg-accent"
                >
                  <GitBranch className="size-3.5 shrink-0 text-muted-foreground" />
                  <span className="min-w-0 flex-1 truncate text-[13px]">{child.title}</span>
                  <SessionStatusBadge status={child.status} compact />
                  <ChevronRight className="size-3.5 text-subtle" />
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------- context */

function ContextPanel({
  snapshot,
  onCompact,
}: {
  snapshot: Snapshot | null;
  onCompact: () => void;
}) {
  const { t } = useUi();
  const usage = snapshot?.usage;
  if (!usage) return <EmptyState title={t('app.loading')} />;
  const percent = Math.min(100, usage.contextPercent);
  const warning = usage.contextPercent >= 80;
  // Any non-zero usage lights at least one of the 40 segments.
  const filledSegments = percent > 0 ? Math.max(1, Math.round(percent / 2.5)) : 0;
  const compaction = snapshot?.compaction;
  const rows: Array<[string, string]> = [
    [t('chat.usage.input'), (usage.inputTokens ?? 0).toLocaleString()],
    [t('chat.usage.output'), (usage.outputTokens ?? 0).toLocaleString()],
    [t('chat.usage.cacheHit'), (usage.cacheHitTokens ?? 0).toLocaleString()],
    [t('chat.usage.cacheMiss'), (usage.cacheMissTokens ?? 0).toLocaleString()],
    [t('chat.usage.cost'), formatCost(usage.cost ?? 0)],
  ];
  return (
    <div className="pb-6">
      <SectionLabel>{t('inspector.contextWindow')}</SectionLabel>
      <div className="mx-5 border border-border bg-background p-5">
        <div className="flex items-baseline gap-2">
          <span className="text-[34px] leading-none font-semibold tracking-tight tabular-nums">
            {usage.contextPercent}
            <span className="text-lg text-muted-foreground">%</span>
          </span>
          <span className="ml-auto font-mono text-[11.5px] text-muted-foreground tabular-nums">
            {formatTokens(usage.contextUsed)} / {formatTokens(usage.contextWindow)}
          </span>
        </div>
        <div
          className="mt-4 grid h-2 grid-cols-[repeat(40,1fr)] gap-[2px]"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={usage.contextPercent}
          aria-label={t('chat.context')}
        >
          {Array.from({ length: 40 }, (_, index) => (
            <span
              key={index}
              className={cn(
                'bg-muted',
                index < filledSegments && (warning ? 'bg-warning' : 'bg-foreground'),
              )}
            />
          ))}
        </div>
        <p className={cn('mt-3 text-[12px] text-muted-foreground', warning && 'text-warning')}>
          {warning ? t('chat.usage.overLimit') : t('chat.usage.estimated')}
        </p>
      </div>

      <dl className="mx-5 mt-4 divide-y divide-border border border-border bg-background">
        <div className="flex items-center justify-between px-4 py-2.5 text-[12.5px]">
          <dt className="text-muted-foreground">{t('chat.model')}</dt>
          <dd className="max-w-[60%] truncate font-mono text-[12px]">{usage.model}</dd>
        </div>
        {rows.map(([label, value]) => (
          <div key={label} className="flex items-center justify-between px-4 py-2.5 text-[12.5px]">
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="font-mono text-[12px] tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>

      {compaction && (
        <>
          <SectionLabel>{t('inspector.lastCompaction')}</SectionLabel>
          <div className="mx-5 flex items-center justify-between border border-border bg-background px-4 py-3 font-mono text-[12px] tabular-nums">
            <span>{formatTokens(compaction.tokensBefore)}</span>
            <span className="text-subtle">→</span>
            <span>{formatTokens(compaction.tokensAfter)}</span>
          </div>
        </>
      )}

      <div className="mx-5 mt-5">
        <Button variant="outline" className="w-full" onClick={onCompact}>
          <Shrink />
          {t('chat.compactNow')}
        </Button>
      </div>
    </div>
  );
}

