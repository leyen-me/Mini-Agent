import { Check, Copy } from 'lucide-react';
import { useState } from 'react';
import { copyText } from '../lib/clipboard';
import { useUi } from '../state/UiContext';

interface Props {
  text: string;
  label?: string;
  className?: string;
  iconOnly?: boolean;
}

export function CopyButton({ text, label, className = '', iconOnly = false }: Props) {
  const { t } = useUi();
  const [copied, setCopied] = useState(false);
  const resolvedLabel = label ?? t('app.copy');
  return (
    <button
      type="button"
      className={`icon-button copy-button ${className}`}
      title={copied ? t('app.copied') : resolvedLabel}
      aria-label={copied ? t('app.copied') : resolvedLabel}
      onClick={async (event) => {
        event.stopPropagation();
        try {
          await copyText(text);
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1400);
        } catch {
          setCopied(false);
        }
      }}
    >
      {copied ? <Check size={15} aria-hidden="true" /> : <Copy size={15} aria-hidden="true" />}
      {!iconOnly && <span>{copied ? t('app.copied') : resolvedLabel}</span>}
    </button>
  );
}
