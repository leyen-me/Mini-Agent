import { ChevronRight } from 'lucide-react';
import { useState } from 'react';
import { useUi } from '../state/UiContext';
import { cn } from '@/lib/utils';

export function ReasoningBlock({
  text,
  streaming = false,
}: {
  text: string;
  streaming?: boolean;
}) {
  const { t } = useUi();
  const [open, setOpen] = useState(false);
  if (!text) return null;
  return (
    <div className="reasoning-block">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="group inline-flex items-center gap-1.5 py-0.5 text-[12.5px] text-muted-foreground transition-colors hover:text-foreground"
      >
        <ChevronRight
          className={cn('size-3.5 transition-transform duration-200', open && 'rotate-90')}
          aria-hidden="true"
        />
        <span className={cn(streaming && 'text-shimmer')}>{t('chat.thinking')}</span>
      </button>
      {open && (
        <div className="mt-2 max-h-80 overflow-auto border-l border-border-strong py-0.5 pl-4 text-[13px] leading-relaxed whitespace-pre-wrap text-muted-foreground animate-in fade-in-0">
          {text}
        </div>
      )}
    </div>
  );
}
