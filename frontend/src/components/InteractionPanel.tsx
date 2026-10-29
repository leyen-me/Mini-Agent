import { ChevronRight, MessageCircleQuestion, PauseCircle, ShieldAlert, SkipForward } from 'lucide-react';
import { useId, useMemo, useState, type ReactNode } from 'react';
import type { Interaction, Question } from '../api/types';
import { useUi } from '../state/UiContext';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { Textarea } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';
import { RiskBadge } from './StatusBadge';

interface Props {
  interactions: Interaction[];
  onAnswer: (interactionId: string, answer: Record<string, unknown>) => Promise<void>;
}

function defaultValue(question: Question): unknown {
  if (question.default !== undefined) return question.default;
  if (question.type === 'multi') return [];
  if (question.type === 'confirm') return false;
  if (question.type === 'text') return '';
  return '';
}

function Card({
  tone = 'neutral',
  label,
  icon,
  title,
  aside,
  children,
  footer,
  className,
}: {
  tone?: 'neutral' | 'warning';
  label: string;
  icon: ReactNode;
  title: ReactNode;
  aside?: ReactNode;
  children: ReactNode;
  footer: ReactNode;
  className?: string;
}) {
  return (
    <section
      aria-label={label}
      className={cn(
        'interaction-card border border-border bg-background shadow-[0_16px_40px_-20px_rgba(0,0,0,0.18)] animate-rise',
        tone === 'warning' && 'permission-card border-t-2 border-t-warning',
        className,
      )}
    >
      <header className="interaction-header flex items-center justify-between gap-3 border-b border-border px-5 py-3.5">
        <span
          className={cn(
            'flex items-center gap-2 text-[13.5px] font-semibold',
            tone === 'warning' && 'text-warning',
          )}
        >
          <span className="[&_svg]:size-4">{icon}</span>
          {title}
        </span>
        {aside}
      </header>
      {children}
      <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-border bg-muted/60 px-5 py-3">
        {footer}
      </footer>
    </section>
  );
}

function OptionRow({
  id,
  selected,
  control,
  children,
}: {
  id: string;
  selected: boolean;
  control: ReactNode;
  children: ReactNode;
}) {
  return (
    <label
      htmlFor={id}
      className={cn(
        'option flex cursor-pointer items-center gap-3 border border-border px-3.5 py-3 text-[13.5px] transition-colors hover:border-border-strong hover:bg-muted/60',
        selected && 'selected border-foreground bg-muted/60 hover:border-foreground',
      )}
    >
      {control}
      <span>{children}</span>
    </label>
  );
}

function AskQuestionCard({
  interaction,
  onAnswer,
}: {
  interaction: Interaction;
  onAnswer: Props['onAnswer'];
}) {
  const { t } = useUi();
  const baseId = useId();
  const questions = interaction.payload.questions ?? [];
  const [answers, setAnswers] = useState<Record<string, unknown>>(() =>
    Object.fromEntries(questions.map((question) => [question.id, defaultValue(question)])),
  );
  const [busy, setBusy] = useState(false);
  const allOptional = questions.length > 0 && questions.every((question) => question.allow_skip);
  const missing = questions.filter((question) => {
    const value = answers[question.id];
    if (question.allow_skip) return false;
    if (question.type === 'multi') return !Array.isArray(value) || value.length === 0;
    if (question.type === 'text') return !String(value ?? '').trim();
    return value === undefined || value === null || value === '';
  });
  const setAnswer = (id: string, value: unknown) =>
    setAnswers((current) => ({ ...current, [id]: value }));
  const submit = async (skip = false) => {
    setBusy(true);
    try {
      await onAnswer(interaction.id, skip ? { skipped: true, answers: {} } : { answers });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card
      label={t('chat.interaction.ask')}
      icon={<MessageCircleQuestion />}
      title={t('chat.interaction.ask')}
      aside={
        <span className="font-mono text-[11px] text-muted-foreground tabular-nums">
          {String(questions.length).padStart(2, '0')} Q
        </span>
      }
      footer={
        <>
          <span className="text-[12px] text-muted-foreground">{t('chat.interaction.waiting')}</span>
          <div className="flex items-center gap-2">
            {allOptional && (
              <Button variant="ghost" size="sm" disabled={busy} onClick={() => void submit(true)}>
                <SkipForward /> {t('chat.interaction.skip')}
              </Button>
            )}
            <Button size="sm" disabled={busy || missing.length > 0} onClick={() => void submit(false)}>
              {t('chat.interaction.submit')}
            </Button>
          </div>
        </>
      }
    >
      <div className="max-h-[360px] divide-y divide-border overflow-y-auto">
        {questions.map((question, index) => {
          const value = answers[question.id];
          const fieldId = `${baseId}-${question.id}`;
          return (
            <fieldset className="question space-y-3 px-5 py-4" key={question.id}>
              <legend className="contents">
                <span className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <span className="font-mono text-[11px] text-subtle tabular-nums">
                    {String(index + 1).padStart(2, '0')}
                  </span>
                  {question.header && <span className="eyebrow">{question.header}</span>}
                  <span className="text-[14px] font-medium">{question.question}</span>
                  <span
                    className={cn(
                      'ml-auto font-mono text-[10.5px] uppercase',
                      question.allow_skip ? 'text-muted-foreground' : 'text-destructive',
                    )}
                  >
                    {question.allow_skip
                      ? t('chat.interaction.optional')
                      : t('chat.interaction.required')}
                  </span>
                </span>
              </legend>
              {question.type === 'single' && (
                <RadioGroup
                  value={typeof value === 'string' ? value : ''}
                  onValueChange={(next) => setAnswer(question.id, next)}
                  className="grid gap-2 sm:grid-cols-2"
                >
                  {(question.options ?? []).map((option) => {
                    const id = `${fieldId}-${option.value}`;
                    return (
                      <OptionRow
                        key={option.value}
                        id={id}
                        selected={value === option.value}
                        control={<RadioGroupItem id={id} value={option.value} />}
                      >
                        {option.label}
                      </OptionRow>
                    );
                  })}
                </RadioGroup>
              )}
              {question.type === 'multi' && (
                <div className="grid gap-2 sm:grid-cols-2">
                  {(question.options ?? []).map((option) => {
                    const id = `${fieldId}-${option.value}`;
                    const selected = Array.isArray(value) && value.includes(option.value);
                    return (
                      <OptionRow
                        key={option.value}
                        id={id}
                        selected={selected}
                        control={
                          <Checkbox
                            id={id}
                            checked={selected}
                            onCheckedChange={() => {
                              const list = Array.isArray(value) ? (value as string[]) : [];
                              setAnswer(
                                question.id,
                                selected
                                  ? list.filter((item) => item !== option.value)
                                  : [...list, option.value],
                              );
                            }}
                          />
                        }
                      >
                        {option.label}
                      </OptionRow>
                    );
                  })}
                </div>
              )}
              {question.type === 'text' && (
                <Textarea
                  placeholder={t('chat.interaction.textPlaceholder')}
                  value={String(value ?? '')}
                  onChange={(event) => setAnswer(question.id, event.target.value)}
                  rows={3}
                />
              )}
              {question.type === 'confirm' && (
                <OptionRow
                  id={fieldId}
                  selected={Boolean(value)}
                  control={
                    <Checkbox
                      id={fieldId}
                      checked={Boolean(value)}
                      onCheckedChange={(checked) => setAnswer(question.id, checked === true)}
                    />
                  }
                >
                  {t('chat.interaction.confirm')}
                </OptionRow>
              )}
            </fieldset>
          );
        })}
      </div>
    </Card>
  );
}

function PermissionCard({
  interaction,
  onAnswer,
}: {
  interaction: Interaction;
  onAnswer: Props['onAnswer'];
}) {
  const { t } = useUi();
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const payload = interaction.payload;
  const risk = payload.risk ?? 'medium';
  const operation = String(payload.resource || payload.input?.command || payload.input?.path || '');
  const answer = async (action: 'allow_once' | 'allow_always' | 'deny') => {
    setBusy(true);
    try {
      await onAnswer(interaction.id, { action, reason });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card
      tone="warning"
      label={t('chat.interaction.permission')}
      icon={<ShieldAlert />}
      title={t('chat.interaction.permission')}
      aside={<RiskBadge risk={risk} />}
      footer={
        <>
          <Input
            value={reason}
            placeholder={t('chat.interaction.denyReason')}
            onChange={(event) => setReason(event.target.value)}
            className="h-8 min-w-0 flex-1 bg-background text-[13px] sm:max-w-64"
          />
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button
              variant="ghost"
              size="sm"
              className="text-destructive hover:bg-danger-soft hover:text-destructive"
              disabled={busy}
              onClick={() => void answer('deny')}
            >
              {t('chat.interaction.deny')}
            </Button>
            <Button variant="outline" size="sm" disabled={busy} onClick={() => void answer('allow_always')}>
              {t('chat.interaction.allowAlways')}
            </Button>
            <Button size="sm" disabled={busy} onClick={() => void answer('allow_once')}>
              {t('chat.interaction.allowOnce')}
            </Button>
          </div>
        </>
      }
    >
      <div className="space-y-4 px-5 py-4">
        <dl className="grid gap-px border border-border bg-border sm:grid-cols-[minmax(0,0.7fr)_minmax(0,1.6fr)_minmax(0,1fr)]">
          <div className="min-w-0 bg-background px-3.5 py-2.5">
            <dt className="eyebrow">{t('chat.tool')}</dt>
            <dd className="mt-1.5 font-mono text-[13px]">{payload.tool}</dd>
          </div>
          <div className="min-w-0 bg-background px-3.5 py-2.5">
            <dt className="eyebrow">{t('chat.interaction.command')}</dt>
            <dd className="command-preview mt-1.5 font-mono text-[13px] break-all">
              {operation || JSON.stringify(payload.input)}
            </dd>
          </div>
          <div className="min-w-0 bg-background px-3.5 py-2.5">
            <dt className="eyebrow">{t('chat.interaction.risk')}</dt>
            <dd className="mt-1.5 text-[13px]">
              {t(`chat.interaction.risk.${risk}`)} · {payload.reason}
            </dd>
          </div>
        </dl>
        <button
          type="button"
          className="inline-flex items-center gap-1.5 text-[12.5px] text-muted-foreground transition-colors hover:text-foreground"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
        >
          <ChevronRight
            className={cn('size-3.5 transition-transform', expanded && 'rotate-90')}
            aria-hidden="true"
          />
          {t('chat.toolInput')}
        </button>
        {expanded && (
          <pre className="max-h-56 overflow-auto border border-code-border bg-code px-3.5 py-3 font-mono text-[11.5px] leading-relaxed whitespace-pre-wrap text-code-foreground">
            {JSON.stringify(payload.input, null, 2)}
          </pre>
        )}
      </div>
    </Card>
  );
}

function BudgetCard({
  interaction,
  onAnswer,
}: {
  interaction: Interaction;
  onAnswer: Props['onAnswer'];
}) {
  const { t } = useUi();
  const [busy, setBusy] = useState(false);
  const payload = interaction.payload as { reason?: string };
  return (
    <Card
      tone="warning"
      className="border-t-warning"
      label={t('chat.interaction.budget')}
      icon={<PauseCircle />}
      title={t('chat.interaction.budget')}
      footer={
        <Button
          size="sm"
          className="ml-auto"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              await onAnswer(interaction.id, { action: 'continue' });
            } finally {
              setBusy(false);
            }
          }}
        >
          {t('chat.resume')}
        </Button>
      }
    >
      <p className="px-5 py-4 text-[13.5px] text-foreground/80">
        {payload.reason ?? t('chat.pausedHint')}
      </p>
    </Card>
  );
}

export function InteractionPanel({ interactions, onAnswer }: Props) {
  const { t } = useUi();
  const pending = useMemo(
    () => interactions.filter((item) => item.status === 'pending'),
    [interactions],
  );
  if (!pending.length) return null;
  return (
    <div className="interaction-panel flex w-full max-w-[760px] flex-col gap-3 max-sm:max-h-[52dvh] max-sm:overflow-y-auto">
      {pending.map((interaction) => {
        if (interaction.type === 'permission') {
          return <PermissionCard key={interaction.id} interaction={interaction} onAnswer={onAnswer} />;
        }
        if (interaction.type === 'budget') {
          return <BudgetCard key={interaction.id} interaction={interaction} onAnswer={onAnswer} />;
        }
        return <AskQuestionCard key={interaction.id} interaction={interaction} onAnswer={onAnswer} />;
      })}
      <span className="sr-only">{t('chat.interaction.waiting')}</span>
    </div>
  );
}
