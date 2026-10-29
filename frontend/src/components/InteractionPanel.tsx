import { ChevronDown, ChevronRight, ShieldAlert, SkipForward } from 'lucide-react';
import { useMemo, useState } from 'react';
import type { Interaction, Question } from '../api/types';
import { useUi } from '../state/UiContext';
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

function AskQuestionCard({ interaction, onAnswer }: { interaction: Interaction; onAnswer: Props['onAnswer'] }) {
  const { t } = useUi();
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
  const submit = async (skip = false) => {
    setBusy(true);
    try {
      await onAnswer(interaction.id, skip ? { skipped: true, answers: {} } : { answers });
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="interaction-card" aria-label={t('chat.interaction.ask')}>
      <header className="interaction-header">
        <span className="interaction-title">{t('chat.interaction.ask')}</span>
        <span className="interaction-chip">{questions.length} Q</span>
      </header>
      <div className="questions">
        {questions.map((question, index) => {
          const value = answers[question.id];
          return (
            <fieldset className="question" key={question.id}>
              <legend>
                <span className="question-index">{index + 1}</span>
                {question.header && <span className="question-header">{question.header}</span>}
                <span className="question-text">{question.question}</span>
                <span className={`question-required ${question.allow_skip ? 'optional' : ''}`}>
                  {question.allow_skip ? t('chat.interaction.optional') : t('chat.interaction.required')}
                </span>
              </legend>
              {question.type === 'single' && (
                <div className="option-list">
                  {(question.options ?? []).map((option) => (
                    <label className={`option ${value === option.value ? 'selected' : ''}`} key={option.value}>
                      <input
                        type="radio"
                        name={`${interaction.id}-${question.id}`}
                        checked={value === option.value}
                        onChange={() => setAnswers((current) => ({ ...current, [question.id]: option.value }))}
                      />
                      <span>{option.label}</span>
                    </label>
                  ))}
                </div>
              )}
              {question.type === 'multi' && (
                <div className="option-list">
                  {(question.options ?? []).map((option) => {
                    const selected = Array.isArray(value) && value.includes(option.value);
                    return (
                      <label className={`option ${selected ? 'selected' : ''}`} key={option.value}>
                        <input
                          type="checkbox"
                          checked={selected}
                          onChange={() =>
                            setAnswers((current) => {
                              const list = Array.isArray(current[question.id]) ? (current[question.id] as string[]) : [];
                              return {
                                ...current,
                                [question.id]: selected
                                  ? list.filter((item) => item !== option.value)
                                  : [...list, option.value],
                              };
                            })
                          }
                        />
                        <span>{option.label}</span>
                      </label>
                    );
                  })}
                </div>
              )}
              {question.type === 'text' && (
                <textarea
                  className="question-textarea"
                  placeholder={t('chat.interaction.textPlaceholder')}
                  value={String(value ?? '')}
                  onChange={(event) => setAnswers((current) => ({ ...current, [question.id]: event.target.value }))}
                  rows={3}
                />
              )}
              {question.type === 'confirm' && (
                <label className={`option ${value ? 'selected' : ''}`}>
                  <input
                    type="checkbox"
                    checked={Boolean(value)}
                    onChange={(event) => setAnswers((current) => ({ ...current, [question.id]: event.target.checked }))}
                  />
                  <span>{t('chat.interaction.confirm')}</span>
                </label>
              )}
            </fieldset>
          );
        })}
      </div>
      <footer className="interaction-footer">
        <span className="muted small">{t('chat.interaction.waiting')}</span>
        <div className="inline-actions">
          {allOptional && (
            <button type="button" className="text-button" disabled={busy} onClick={() => void submit(true)}>
              <SkipForward size={14} aria-hidden="true" /> {t('chat.interaction.skip')}
            </button>
          )}
          <button
            type="button"
            className="primary-button"
            disabled={busy || missing.length > 0}
            onClick={() => void submit(false)}
          >
            {t('chat.interaction.submit')}
          </button>
        </div>
      </footer>
    </section>
  );
}

function PermissionCard({ interaction, onAnswer }: { interaction: Interaction; onAnswer: Props['onAnswer'] }) {
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
    <section className="interaction-card permission-card" aria-label={t('chat.interaction.permission')}>
      <header className="interaction-header">
        <span className="interaction-title">
          <ShieldAlert size={15} aria-hidden="true" /> {t('chat.interaction.permission')}
        </span>
        <RiskBadge risk={risk} />
      </header>
      <div className="permission-body">
        <dl>
          <div>
            <dt>{t('chat.tool')}</dt>
            <dd><code>{payload.tool}</code></dd>
          </div>
          <div>
            <dt>{t('chat.interaction.command')}</dt>
            <dd className="command-preview">{operation || JSON.stringify(payload.input)}</dd>
          </div>
          <div>
            <dt>{t('chat.interaction.risk')}</dt>
            <dd>{t(`chat.interaction.risk.${risk}`)} · {payload.reason}</dd>
          </div>
        </dl>
        <button type="button" className="text-button" onClick={() => setExpanded((value) => !value)}>
          {expanded ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
          {t('chat.toolInput')}
        </button>
        {expanded && <pre className="permission-input">{JSON.stringify(payload.input, null, 2)}</pre>}
        <input
          className="text-input"
          value={reason}
          placeholder={t('chat.interaction.denyReason')}
          onChange={(event) => setReason(event.target.value)}
        />
      </div>
      <footer className="interaction-footer">
        <div className="inline-actions">
          <button type="button" className="text-button danger" disabled={busy} onClick={() => void answer('deny')}>
            {t('chat.interaction.deny')}
          </button>
          <button type="button" className="secondary-button" disabled={busy} onClick={() => void answer('allow_always')}>
            {t('chat.interaction.allowAlways')}
          </button>
          <button type="button" className="primary-button" disabled={busy} onClick={() => void answer('allow_once')}>
            {t('chat.interaction.allowOnce')}
          </button>
        </div>
      </footer>
    </section>
  );
}

function BudgetCard({ interaction, onAnswer }: { interaction: Interaction; onAnswer: Props['onAnswer'] }) {
  const { t } = useUi();
  const [busy, setBusy] = useState(false);
  const payload = interaction.payload as { reason?: string };
  return (
    <section className="interaction-card" aria-label={t('chat.interaction.budget')}>
      <header className="interaction-header">
        <span className="interaction-title">{t('chat.interaction.budget')}</span>
      </header>
      <p>{payload.reason ?? t('chat.pausedHint')}</p>
      <footer className="interaction-footer">
        <button
          type="button"
          className="primary-button"
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
        </button>
      </footer>
    </section>
  );
}

export function InteractionPanel({ interactions, onAnswer }: Props) {
  const { t } = useUi();
  const pending = useMemo(() => interactions.filter((item) => item.status === 'pending'), [interactions]);
  if (!pending.length) return null;
  return (
    <div className="interaction-panel">
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
