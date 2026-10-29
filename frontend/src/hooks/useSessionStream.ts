import { useEffect, useRef } from 'react';
import { startSessionStream } from '../api/sse';
import type { StreamEvent, StreamStatus } from '../api/types';

interface Options {
  sessionId: string | null;
  enabled: boolean;
  initialLastEventId: number;
  onEvent: (event: StreamEvent) => void;
  refreshSnapshot: () => Promise<number>;
  onStatus: (status: StreamStatus) => void;
  onFatal?: (message: string) => void;
}

/** Keeps a session's SSE stream alive; refreshes a snapshot before reconnecting. */
export function useSessionStream({
  sessionId,
  enabled,
  initialLastEventId,
  onEvent,
  refreshSnapshot,
  onStatus,
  onFatal,
}: Options) {
  const lastEventIdRef = useRef(initialLastEventId);
  const eventRef = useRef(onEvent);
  const refreshRef = useRef(refreshSnapshot);
  const statusRef = useRef(onStatus);
  const fatalRef = useRef(onFatal);

  useEffect(() => {
    eventRef.current = onEvent;
  }, [onEvent]);
  useEffect(() => {
    refreshRef.current = refreshSnapshot;
  }, [refreshSnapshot]);
  useEffect(() => {
    statusRef.current = onStatus;
  }, [onStatus]);
  useEffect(() => {
    fatalRef.current = onFatal;
  }, [onFatal]);

  useEffect(() => {
    lastEventIdRef.current = initialLastEventId;
  }, [sessionId, initialLastEventId]);

  useEffect(() => {
    if (!enabled || !sessionId) {
      statusRef.current('offline');
      return;
    }
    const close = startSessionStream({
      sessionId,
      getLastEventId: () => lastEventIdRef.current,
      refreshSnapshot: async () => {
        const id = await refreshRef.current();
        lastEventIdRef.current = id;
        return id;
      },
      onEvent: (event) => {
        if (typeof event.id === 'number') lastEventIdRef.current = event.id;
        eventRef.current(event);
      },
      onStatus: (status) => statusRef.current(status),
      onFatal: (message) => fatalRef.current?.(message),
    });
    return close;
  }, [sessionId, enabled]);
}
