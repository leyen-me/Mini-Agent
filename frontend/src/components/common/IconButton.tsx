import type { ComponentProps, ReactNode } from 'react';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';

interface Props extends Omit<ComponentProps<typeof Button>, 'size' | 'title'> {
  label: string;
  shortcut?: ReactNode;
  size?: 'icon' | 'icon-sm' | 'icon-xs';
  side?: 'top' | 'right' | 'bottom' | 'left';
}

/** Icon-only ghost button with an accessible name and a hover tooltip. */
export function IconButton({
  label,
  shortcut,
  size = 'icon-sm',
  side = 'bottom',
  variant = 'ghost',
  className,
  children,
  ...props
}: Props) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant={variant}
          size={size}
          aria-label={label}
          className={cn('text-muted-foreground hover:text-foreground', className)}
          {...props}
        >
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent side={side} className="flex items-center gap-2">
        {label}
        {shortcut}
      </TooltipContent>
    </Tooltip>
  );
}
