import type { StreamEvent, StreamStatus } from '../api/types';
import { currentApiToken } from './client';

export interface ParsedSse {
  id: number | null;
  event: string;
  data: string;
}

/** Incremental SSE frame parser, exported for unit tests. */
export class SseParser {
  private buffer = '';

  push(chunk: string): ParsedSse[] {
    this.buffer += chunk.replace(/\r\n/g, '\n');
    const frames: ParsedSse[] = [];
    let boundary = this.buffer.indexOf('\n\n');
    while (boundary !== -1) {
      const raw = this.buffer.slice(0, boundary);
      this.buffer = this.buffer.slice(boundary + 2);
      const parsed = parseFrame(raw);
      if (parsed) frames.push(parsed);
      boundary = this.buffer.indexOf('\n\n');
    }
    return frames;
  }

  reset(): void {
    this.buffer = '';
  }
}

function parseFrame(raw: string): ParsedSse | null {
  let id: number | null = null;
  let event = 'message';
  const data: string[] = [];
  for (const line of raw.split('\n')) {
    if (!line || line.startsWith(':')) continue;
    const separator = line.indexOf(':');
    const field = separator === -1 ? line : line.slice(0, separator);
    const value =
      separator === -1 ? '' : line.slice(separator + 1).replace(/^ /, '');
    if (field === 'id') {
      const parsed = Number.parseInt(value, 10);
      id = Number.isFinite(parsed) ? parsed : null;
    } else if (field === 'event') {
      event = value || 'message';
    } else if (field === 'data') {
      data.push(value);
    }
  }
  if (!data.length) return null;
  return { id, event, data: data.join('\n') };
}

const SSE_BASE = (
  (import.meta.env.VITE_API_BASE as string | undefined) ?? '/api/v1'
).replace(/\/$/, '');

interface StreamOptions {
  sessionId: string;
  getLastEventId: () => number;
  refreshSnapshot: () => Promise<number>;
  onEvent: (event: StreamEvent) => void;
  onStatus: (status: StreamStatus) => void;
  onFatal?: (message: string) => void;
  sleep?: (ms: number, signal: AbortSignal) => Promise<void>;
}

export function startSessionStream(options: StreamOptions): () => void {
  const controller = new AbortController();
  let closed = false;
  let lastEventId = options.getLastEventId();

  const wait = options.sleep ?? defaultSleep;

  const run = async () => {
    let attempt = 0;
    while (!closed) {
      if (attempt > 0) {
        try {
          lastEventId = await options.refreshSnapshot();
        } catch {
          // The next loop retries; the snapshot refresh is best-effort.
        }
        if (closed) return;
        const delay =
          Math.min(15_000, 1_000 * 2 ** Math.min(attempt - 1, 4)) +
          Math.random() * 250;
        options.onStatus(
          navigator.onLine === false ? 'offline' : 'reconnecting',
        );
        try {
          await wait(delay, controller.signal);
        } catch {
          return;
        }
      } else {
        options.onStatus('connecting');
      }
      try {
        const headers: Record<string, string> = { Accept: 'text/event-stream' };
        if (lastEventId > 0) headers['Last-Event-ID'] = String(lastEventId);
        const response = await fetch(
          `${SSE_BASE}/sessions/${options.sessionId}/events`,
          {
            headers: {
              ...headers,
              ...(currentApiToken()
                ? { Authorization: `Bearer ${currentApiToken()}` }
                : {}),
            },
            signal: controller.signal,
            cache: 'no-store',
          },
        );
        if (response.status === 404) {
          options.onFatal?.('会话不存在或已被删除');
          return;
        }
        if (!response.ok || !response.body) {
          throw new Error(`SSE HTTP ${response.status}`);
        }
        attempt = 0;
        options.onStatus('connected');
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        const parser = new SseParser();
        while (!closed) {
          const { done, value } = await reader.read();
          if (done) break;
          for (const frame of parser.push(
            decoder.decode(value, { stream: true }),
          )) {
            if (frame.id !== null && frame.id <= lastEventId) continue;
            let payload: StreamEvent;
            try {
              payload = JSON.parse(frame.data) as StreamEvent;
            } catch {
              continue;
            }
            if (frame.id !== null) {
              lastEventId = frame.id;
            } else if (typeof payload.id === 'number') {
              lastEventId = payload.id;
            }
            options.onEvent({
              ...payload,
              type: (payload.type as string) || frame.event,
            });
          }
        }
        if (!closed) {
          attempt += 1;
        }
      } catch (error) {
        if (closed || controller.signal.aborted) return;
        if ((error as Error).name === 'AbortError') return;
        attempt += 1;
        options.onStatus(
          navigator.onLine === false ? 'offline' : 'reconnecting',
        );
      }
    }
  };

  void run();

  return () => {
    closed = true;
    controller.abort();
  };
}

function defaultSleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException('Aborted', 'AbortError'));
      return;
    }
    const timer = window.setTimeout(() => {
      signal.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      window.clearTimeout(timer);
      reject(new DOMException('Aborted', 'AbortError'));
    };
    signal.addEventListener('abort', onAbort, { once: true });
  });
}
