import { errorMessage } from '../../api/client';

export type ToastFn = (
  message: string,
  kind?: 'info' | 'error' | 'success',
) => void;

export async function runAction(
  action: () => Promise<unknown>,
  onToast: ToastFn,
  success?: string,
): Promise<unknown | null> {
  try {
    const result = await action();
    if (success) onToast(success, 'success');
    return result;
  } catch (error) {
    onToast(errorMessage(error), 'error');
    return null;
  }
}

export function splitList(value: string): string[] {
  return value
    .split(/[,\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}
