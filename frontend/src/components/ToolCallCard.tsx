import { ChevronDown, ChevronRight, Clock3, Wrench } from 'lucide-react';
import { useState } from 'react';
import type { ToolCall } from '../api/types';
import { CopyButton } from './CopyButton';
import { RiskBadge, ToolStatusBadge } from './StatusBadge';
import { useUi } from '../state/UiContext';

function summarize(call: ToolCall): string {
  const { input } = call;
  const candidate =
    (input.command as string | undefined) ||
    (input.path as string | undefined) ||
    (input.url as string | undefined) ||
    (input.pattern as string | undefined) ||
    (input.task_id as string | undefined);
  if (candidate) return candidate;
  return Object.keys(input).length ? JSON.stringify(input) : '—';
}

function duration(call: ToolCall): string | null {
  if (!call.startedAt) return null;
  const start = new Date(call.startedAt).getTime();
  const end = call.endedAt ? new Date(call.endedAt).getTime() : Date.now();
  const ms = Math.max(0, end - start);
  if (!call.endedAt) return '…';
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export function ToolCallCard({ call }: { call: ToolCall }) {
  const { t } = useUi();
  const [open, setOpen] = useState(false);
  const elapsed = duration(call);
  const Chevron = open ? ChevronDown : ChevronRight;
  return (
    <div className={`tool-card tool-card-${call.status}`}>
      <button
        type="button"
        className="tool-card-header"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
      >
        <Chevron size={14} aria-hidden="true" />
        <Wrench size={14} aria-hidden="true" />
        <span className="tool-name">{call.name}</span>
        <ToolStatusBadge status={call.status} />
        {call.risk !== 'low' && <RiskBadge risk={call.risk} />}
        {elapsed && (
          <span className="tool-duration">
            <Clock3 size={12} aria-hidden="true" /> {elapsed}
          </span>
        )}
        <span className="tool-summary">{summarize(call)}</span>
      </button>
      {open && (
        <div className="tool-card-body">
          <div className="tool-json-grid">
            <section>
              <header>
                <h4>{t('chat.toolInput')}</h4>
                <CopyButton text={JSON.stringify(call.input, null, 2)} label={t('chat.toolCopyInput')} />
              </header>
              <pre>{JSON.stringify(call.input, null, 2)}</pre>
            </section>
            <section>
              <header>
                <h4>{t('chat.toolOutput')}</h4>
                <CopyButton
                  text={call.output ? JSON.stringify(call.output, null, 2) : ''}
                  label={t('chat.toolCopyOutput')}
                />
              </header>
              <pre>{call.output ? JSON.stringify(call.output, null, 2) : t('chat.toolNoOutput')}</pre>
            </section>
          </div>
          {call.output?.truncated && (
            <p className="tool-truncated">
              {t('chat.toolOutput')} {call.output.artifacts.join(', ')}
            </p>
          )}
          {call.errorCode && <p className="tool-error-code">{call.errorCode}</p>}
        </div>
      )}
    </div>
  );
}
