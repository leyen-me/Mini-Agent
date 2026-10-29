import { Check, Copy } from 'lucide-react';
import { useState } from 'react';
import { copyText } from '../lib/clipboard';
import { useUi } from '../state/UiContext';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

interface Props {
  text: string;
  label?: string;
  className?: string;
  iconOnly?: boolean;
}

export function CopyButton({ text, label, className, iconOnly = false }: Props) {
  const { t } = useUi();
  const [copied, setCopied] = useState(false);
  const resolvedLabel = label ?? t('app.copy');
  const current = copied ? t('app.copied') : resolvedLabel;
  return (
    <Button
      type="button"
      variant="ghost"
      size={iconOnly ? 'icon-xs' : 'xs'}
      className={cn(
        'copy-button text-muted-foreground hover:text-foreground',
        copied && 'text-success hover:text-success',
        className,
      )}
      title={current}
      aria-label={current}
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
      {copied ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
      {!iconOnly && <span>{current}</span>}
    </Button>
  );
}
