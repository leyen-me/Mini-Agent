import { useEffect, useRef, type ReactNode } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from '@/components/ui/dialog';
import { cn } from '@/lib/utils';
import { useUi } from '../state/UiContext';

interface Props {
  open: boolean;
  title: string;
  description?: ReactNode;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  className?: string;
  bodyClassName?: string;
  wide?: boolean;
}

export function Modal({
  open,
  title,
  description,
  onClose,
  children,
  footer,
  className,
  bodyClassName,
  wide = false,
}: Props) {
  const { t } = useUi();
  const openedAt = useRef(0);
  useEffect(() => {
    if (open) openedAt.current = performance.now();
  }, [open]);
  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent
        aria-label={title}
        closeLabel={t('app.close')}
        aria-describedby={undefined}
        // The pointerdown that started the click which opened this dialog can
        // reach its outside-click handler and close it immediately; only
        // pointer presses that begin after the dialog opened are dismissals.
        onPointerDownOutside={(event) => {
          if (event.detail.originalEvent.timeStamp < openedAt.current) event.preventDefault();
        }}
        className={cn(
          'flex max-h-[min(88dvh,920px)] flex-col gap-0 overflow-hidden border-border p-0 sm:max-w-[600px]',
          wide && 'sm:max-w-[min(1080px,calc(100%-3rem))]',
          className,
        )}
      >
        <header className="flex shrink-0 flex-col gap-1 border-b border-border px-6 py-5 pr-14">
          <DialogTitle className="text-base font-semibold tracking-tight">
            {title}
          </DialogTitle>
          {description && (
            <DialogDescription className="text-[13px] text-muted-foreground">
              {description}
            </DialogDescription>
          )}
        </header>
        <div className={cn('min-h-0 flex-1 overflow-y-auto px-6 py-6', bodyClassName)}>
          {children}
        </div>
        {footer && (
          <footer className="flex shrink-0 items-center justify-end gap-2 border-t border-border bg-muted/50 px-6 py-4">
            {footer}
          </footer>
        )}
      </DialogContent>
    </Dialog>
  );
}
