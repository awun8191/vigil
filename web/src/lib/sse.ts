import type { SseEvent, StreamState } from '@/types';

export interface EventStreamOptions {
  url: string;
  token: string;
  onEvent: (event: SseEvent) => void;
  onStateChange?: (state: StreamState) => void;
  maxAttempts?: number;
}

const BASE_BACKOFF_MS = 500;
const MAX_BACKOFF_MS = 15_000;

/**
 * Reads a `text/event-stream` response with fetch.
 *
 * EventSource is deliberately not used: it cannot set an `Authorization` header, and the backend
 * accepts only `Authorization: Bearer vgt_...`. Putting the token in the query string would leak
 * it into access logs and `Referer`.
 *
 * Returns a close function. Reconnection is internal, with exponential backoff and
 * `Last-Event-ID` replay when the server supplies event ids.
 */
export function createEventStream(options: EventStreamOptions): () => void {
  const maxAttempts = options.maxAttempts ?? 6;

  let closed = false;
  let attempt = 0;
  let controller: AbortController | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  let lastEventId: string | undefined;

  const setState = (state: StreamState) => options.onStateChange?.(state);

  const scheduleReconnect = () => {
    if (closed) return;
    attempt += 1;

    if (attempt > maxAttempts) {
      setState('closed');
      return;
    }

    setState('reconnecting');
    const delay = Math.min(MAX_BACKOFF_MS, BASE_BACKOFF_MS * 2 ** (attempt - 1));
    retryTimer = setTimeout(() => {
      void connect();
    }, delay);
  };

  const handleFrame = (rawFrame: string) => {
    // Normalise CRLF so a proxy that rewrites line endings cannot break parsing.
    const frame = rawFrame.replace(/\r\n/g, '\n');
    if (frame.trimStart().startsWith(':')) return; // comment / keep-alive

    let type = 'message';
    let id: string | undefined;
    const dataLines: string[] = [];

    for (const line of frame.split('\n')) {
      if (line.startsWith('event:')) {
        type = line.slice(6).trim();
      } else if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).replace(/^ /, ''));
      } else if (line.startsWith('id:')) {
        id = line.slice(3).trim();
      }
    }

    if (dataLines.length === 0) return;

    if (id) lastEventId = id;
    const event: SseEvent = id
      ? { type, data: dataLines.join('\n'), id }
      : { type, data: dataLines.join('\n') };
    options.onEvent(event);
  };

  const connect = async (): Promise<void> => {
    if (closed) return;

    controller = new AbortController();
    setState(attempt === 0 ? 'connecting' : 'reconnecting');

    const headers: Record<string, string> = {
      Accept: 'text/event-stream',
      Authorization: `Bearer ${options.token}`,
    };
    if (lastEventId) headers['Last-Event-ID'] = lastEventId;

    try {
      const response = await fetch(options.url, {
        headers,
        signal: controller.signal,
        cache: 'no-store',
      });

      if (!response.ok || !response.body) {
        throw new Error(`Stream rejected with ${response.status}`);
      }

      attempt = 0;
      setState('open');

      const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
      let buffer = '';

      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += value;

        let boundary = buffer.indexOf('\n\n');
        while (boundary !== -1) {
          const frame = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          handleFrame(frame);
          boundary = buffer.indexOf('\n\n');
        }
      }
    } catch (error) {
      if (closed) return;
      if (error instanceof DOMException && error.name === 'AbortError') return;
      // Falls through to reconnect.
    }

    if (closed) return;
    scheduleReconnect();
  };

  void connect();

  return () => {
    closed = true;
    if (retryTimer) clearTimeout(retryTimer);
    controller?.abort();
    setState('closed');
  };
}
