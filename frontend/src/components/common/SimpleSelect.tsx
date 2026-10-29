import type { ReactNode } from 'react';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { cn } from '@/lib/utils';

export interface SelectOption {
  value: string;
  label: ReactNode;
  disabled?: boolean;
}

interface Props {
  value: string;
  options: SelectOption[];
  onValueChange: (value: string) => void;
  ariaLabel?: string;
  placeholder?: string;
  disabled?: boolean;
  size?: 'sm' | 'default';
  className?: string;
  contentClassName?: string;
  id?: string;
  icon?: ReactNode;
}

// Radix Select reserves "" for "no selection", so empty option values are
// mapped to a sentinel at the boundary and restored before reaching callers.
const EMPTY = '__empty__';
const encode = (value: string) => (value === '' ? EMPTY : value);
const decode = (value: string) => (value === EMPTY ? '' : value);

export function SimpleSelect({
  value,
  options,
  onValueChange,
  ariaLabel,
  placeholder,
  disabled,
  size = 'default',
  className,
  contentClassName,
  id,
  icon,
}: Props) {
  return (
    <Select
      value={encode(value)}
      onValueChange={(next) => onValueChange(decode(next))}
      disabled={disabled}
    >
      <SelectTrigger
        id={id}
        size={size}
        aria-label={ariaLabel}
        // Radix Select.Value ignores className, so size and align it from here.
        className={cn('w-full min-w-0 text-left *:data-[slot=select-value]:min-w-0 *:data-[slot=select-value]:flex-1', className)}
      >
        {icon}
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent position="popper" className={contentClassName}>
        {options.map((option) => (
          <SelectItem
            key={option.value}
            value={encode(option.value)}
            disabled={option.disabled}
          >
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
