import {
  FilePen,
  FileSearch,
  FolderTree,
  Globe,
  ListTodo,
  MessageCircleQuestion,
  Search,
  SquareTerminal,
  Wrench,
  type LucideIcon,
} from 'lucide-react';
import type { ToolCall } from '../api/types';

const TOOL_ICONS: Array<[RegExp, LucideIcon]> = [
  [/shell|command|bash/, SquareTerminal],
  [/write|edit/, FilePen],
  [/read|image/, FileSearch],
  [/list_dir|glob|tree/, FolderTree],
  [/web|fetch|http/, Globe],
  [/grep|search/, Search],
  [/todo|task|wait/, ListTodo],
  [/ask_question/, MessageCircleQuestion],
];

export function toolIcon(name: string): LucideIcon {
  return TOOL_ICONS.find(([pattern]) => pattern.test(name))?.[1] ?? Wrench;
}

/** The single most identifying argument of a call, for one-line display. */
export function summarizeToolCall(call: ToolCall): string {
  const { input } = call;
  const candidate =
    (input.command as string | undefined) ||
    (input.path as string | undefined) ||
    (input.url as string | undefined) ||
    (input.pattern as string | undefined) ||
    (input.task_id as string | undefined);
  if (candidate) return candidate;
  return Object.keys(input).length ? JSON.stringify(input) : '—';
}

export function toolCallDuration(call: ToolCall): string | null {
  if (!call.startedAt) return null;
  if (!call.endedAt) return '…';
  const ms = Math.max(
    0,
    new Date(call.endedAt).getTime() - new Date(call.startedAt).getTime(),
  );
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export function toolCallDiff(call: ToolCall): string {
  const data = (call.output?.data ?? null) as Record<string, unknown> | null;
  return typeof data?.diff === 'string' ? data.diff : '';
}
