import {
  cloneElement,
  isValidElement,
  useId,
  type ReactElement,
  type ReactNode,
} from 'react';
import { Label } from '@/components/ui/label';
import { cn } from '@/lib/utils';

interface Props {
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  className?: string;
  /** A single form control; it receives the generated id for label association. */
  children: ReactNode;
}

export function Field({ label, hint, error, className, children }: Props) {
  const generated = useId();
  const control = isValidElement(children)
    ? (children as ReactElement<{ id?: string }>)
    : null;
  const id = control?.props.id ?? generated;
  return (
    <div className={cn('flex min-w-0 flex-col gap-2', className)}>
      <Label htmlFor={id} className="text-[13px] font-medium text-foreground">
        {label}
      </Label>
      {control ? cloneElement(control, { id }) : children}
      {hint && !error && (
        <p className="text-xs leading-relaxed text-muted-foreground">{hint}</p>
      )}
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );
}

/** Section header used inside settings/admin panels. */
export function PanelSection({
  title,
  description,
  actions,
  children,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cn('border-t border-border pt-6 first:border-t-0 first:pt-0', className)}>
      <header className="mb-5 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 space-y-1">
          <h3 className="text-[15px] font-semibold tracking-tight">{title}</h3>
          {description && (
            <p className="max-w-prose text-[13px] leading-relaxed text-muted-foreground">
              {description}
            </p>
          )}
        </div>
        {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </header>
      {children}
    </section>
  );
}
