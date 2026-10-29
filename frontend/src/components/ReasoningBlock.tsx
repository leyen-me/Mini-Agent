import { Brain, ChevronDown, ChevronRight } from 'lucide-react';
import { useState } from 'react';
import { useUi } from '../state/UiContext';

export function ReasoningBlock({ text, streaming = false }: { text: string; streaming?: boolean }) {
  const { t } = useUi();
  const [open, setOpen] = useState(false);
  if (!text) return null;
  const Chevron = open ? ChevronDown : ChevronRight;
  return (
    <div className={`reasoning-block ${streaming ? 'is-streaming' : ''}`}>
      <button type="button" className="reasoning-toggle" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
        <Chevron size={13} aria-hidden="true" />
        <Brain size={13} aria-hidden="true" />
        <span>{t('chat.thinking')}</span>
        {streaming && <span className="reasoning-pulse" aria-hidden="true" />}
      </button>
      {open && <div className="reasoning-content">{text}</div>}
    </div>
  );
}
