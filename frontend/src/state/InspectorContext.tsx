import { createContext, useContext } from 'react';

export type InspectorTab = 'activity' | 'tasks' | 'files' | 'context';

export interface InspectorContextValue {
  selectedToolCallId: string | null;
  /** Reveal a tool call in the inspector, opening the panel if needed. */
  focusToolCall: (id: string) => void;
}

export const InspectorContext = createContext<InspectorContextValue | null>(null);

/** Returns null outside the app shell (e.g. share view, isolated tests). */
export function useInspector(): InspectorContextValue | null {
  return useContext(InspectorContext);
}
