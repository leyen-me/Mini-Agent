import type { LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

export interface NavItem<T extends string> {
  id: T;
  label: string;
  icon: LucideIcon;
}

/** Two-pane dialog body: vertical tab rail on the left, scrollable panel on the right. */
export function SettingsShell<T extends string>({
  label,
  items,
  value,
  onChange,
  footer,
  children,
}: {
  label: string;
  items: NavItem<T>[];
  value: T;
  onChange: (value: T) => void;
  footer?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="grid h-[min(72dvh,720px)] min-h-0 grid-cols-1 md:grid-cols-[216px_minmax(0,1fr)]">
      <nav
        className="flex min-h-0 gap-1 overflow-x-auto border-b border-border bg-muted/50 p-3 md:flex-col md:overflow-y-auto md:border-r md:border-b-0"
        role="tablist"
        aria-label={label}
        aria-orientation="vertical"
      >
        {items.map((item) => {
          const Icon = item.icon;
          const active = item.id === value;
          return (
            <button
              type="button"
              key={item.id}
              role="tab"
              aria-selected={active}
              onClick={() => onChange(item.id)}
              className={cn(
                'flex h-9 shrink-0 items-center gap-2.5 px-3 text-left text-[13px] whitespace-nowrap transition-colors',
                active
                  ? 'bg-background font-medium text-foreground shadow-[inset_2px_0_0_var(--foreground),0_0_0_1px_var(--border)]'
                  : 'text-muted-foreground hover:bg-accent hover:text-foreground',
              )}
            >
              <Icon className="size-4 shrink-0" aria-hidden="true" />
              {item.label}
            </button>
          );
        })}
        {footer && <div className="mt-auto hidden pt-3 md:block">{footer}</div>}
      </nav>
      <div className="min-h-0 overflow-y-auto px-6 py-6 md:px-8 md:py-8">{children}</div>
    </div>
  );
}

export function PanelHeading({
  title,
  description,
  actions,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="mb-8 flex flex-wrap items-end justify-between gap-4">
      <div className="space-y-1.5">
        <h3 className="text-[22px] leading-tight font-semibold tracking-[-0.02em]">{title}</h3>
        {description && (
          <p className="max-w-prose text-[13px] leading-relaxed text-muted-foreground">
            {description}
          </p>
        )}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </header>
  );
}

export function GroupLabel({ children }: { children: ReactNode }) {
  return <h4 className="eyebrow mt-10 mb-3 first:mt-0">{children}</h4>;
}

/** Label/description on the left, control on the right; stacks on narrow screens. */
export function SettingRow({
  title,
  description,
  htmlFor,
  children,
}: {
  title: ReactNode;
  description?: ReactNode;
  htmlFor?: string;
  children: ReactNode;
}) {
  const Title = htmlFor ? 'label' : 'div';
  return (
    <div className="grid gap-3 border-b border-border py-4 last:border-b-0 sm:grid-cols-[minmax(0,1fr)_minmax(0,280px)] sm:items-center sm:gap-8">
      <div className="min-w-0 space-y-1">
        <Title htmlFor={htmlFor} className="block text-[13.5px] font-medium">
          {title}
        </Title>
        {description && (
          <p className="text-[12.5px] leading-relaxed text-muted-foreground">{description}</p>
        )}
      </div>
      <div className="flex min-w-0 items-center gap-2 sm:justify-end">{children}</div>
    </div>
  );
}

export function RowGroup({ children }: { children: ReactNode }) {
  return <div className="border-y border-border">{children}</div>;
}
