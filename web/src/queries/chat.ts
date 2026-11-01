import { useCallback, useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { userMessageOf } from '@/lib/errors';
import { releaseIdempotencyKey } from '@/lib/idempotency';
import type { ChatMessage, ChatSession, StreamState } from '@/types';

export const SESSIONS_KEY = ['chat', 'sessions'] as const;
export const messagesKey = (sessionId: string) =>
  ['chat', 'sessions', sessionId, 'messages'] as const;

const DONE_SENTINEL = '[DONE]';

export function useSessions() {
  return useQuery<ChatSession[]>({
    queryKey: SESSIONS_KEY,
    queryFn: () => api.listSessions(),
  });
}

export function useCreateSession() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ title, scope }: { title: string | undefined; scope: string }) =>
      api.createSession(title, scope),
    onSettled: (_data, _error, variables) => {
      releaseIdempotencyKey(variables.scope);
      void queryClient.invalidateQueries({ queryKey: SESSIONS_KEY });
    },
  });
}

export function useMessages(sessionId: string | null) {
  return useQuery<ChatMessage[]>({
    queryKey: messagesKey(sessionId ?? 'none'),
    queryFn: () => api.listMessages(sessionId as string),
    enabled: Boolean(sessionId),
    staleTime: 5_000,
  });
}

export interface ChatStream {
  streaming: boolean;
  streamedText: string;
  connection: StreamState;
  error: string | null;
  lastPrompt: string | null;
  send: (prompt: string) => void;
  retry: () => void;
  stop: () => void;
}

/**
 * Owns the assistant reply stream for one session.
 *
 * The backend's stream carries the prompt itself (`?q=`), so the client must not also POST the
 * same text to `/messages`: that would process the turn twice. Sending therefore means appending
 * the user message locally, then opening the stream.
 *
 * The stream emits bare `data: <token>` frames and terminates with `data: [DONE]`. It does not
 * emit event ids, so `Last-Event-ID` resume is inert until the backend adds them; reconnects
 * still work through the backoff in `lib/sse.ts`.
 */
export function useChatStream(sessionId: string | null): ChatStream {
  const queryClient = useQueryClient();

  const [streaming, setStreaming] = useState(false);
  const [streamedText, setStreamedText] = useState('');
  const [connection, setConnection] = useState<StreamState>('idle');
  const [error, setError] = useState<string | null>(null);
  const [lastPrompt, setLastPrompt] = useState<string | null>(null);

  const closeRef = useRef<(() => void) | null>(null);
  const textRef = useRef('');
  const promptRef = useRef<string | null>(null);
  const sessionRef = useRef<string | null>(sessionId);

  const teardown = useCallback(() => {
    closeRef.current?.();
    closeRef.current = null;
  }, []);

  const finalise = useCallback(() => {
    teardown();
    const text = textRef.current;
    textRef.current = '';
    setStreaming(false);
    setStreamedText('');

    if (!text) return;
    const activeSession = sessionRef.current;
    if (!activeSession) return;

    // The stream is not guaranteed to persist the reply, so keep the assembled text locally and
    // refresh the session list (not the message list, which would drop the local copy).
    const localReply: ChatMessage = {
      id: `local-${Date.now()}`,
      session_id: activeSession,
      role: 'assistant',
      content: text,
      created_at: new Date().toISOString(),
    };

    queryClient.setQueryData<ChatMessage[]>(messagesKey(activeSession), (current) =>
      current ? [...current, localReply] : [localReply],
    );
    void queryClient.invalidateQueries({ queryKey: SESSIONS_KEY });
  }, [queryClient, teardown]);

  const begin = useCallback(
    (prompt: string) => {
      if (!sessionId) return;

      teardown();
      textRef.current = '';
      promptRef.current = prompt;
      setStreamedText('');
      setError(null);
      setStreaming(true);
      setLastPrompt(prompt);

      closeRef.current = api.openStream(sessionId, prompt, {
        onEvent(event) {
          if (event.data === DONE_SENTINEL) {
            finalise();
            return;
          }
          textRef.current += event.data;
          setStreamedText(textRef.current);
        },
        onStateChange(state) {
          setConnection(state);
          if (state === 'closed' && textRef.current.length === 0) {
            setStreaming(false);
          }
        },
      });
    },
    [sessionId, teardown, finalise],
  );

  const send = useCallback(
    (prompt: string) => {
      const trimmed = prompt.trim();
      if (!trimmed || !sessionId) return;

      queryClient.setQueryData<ChatMessage[]>(messagesKey(sessionId), (current) => {
        const optimistic: ChatMessage = {
          id: `local-user-${Date.now()}`,
          session_id: sessionId,
          role: 'user',
          content: trimmed,
          created_at: new Date().toISOString(),
        };
        return current ? [...current, optimistic] : [optimistic];
      });

      begin(trimmed);
    },
    [begin, queryClient, sessionId],
  );

  const retry = useCallback(() => {
    const prompt = promptRef.current;
    if (!prompt) return;
    begin(prompt);
  }, [begin]);

  const stop = useCallback(() => {
    try {
      finalise();
    } catch (cause) {
      setError(userMessageOf(cause));
    }
  }, [finalise]);

  // A stale reader must never write into a different session.
  useEffect(() => {
    if (sessionRef.current !== sessionId) {
      teardown();
      textRef.current = '';
      promptRef.current = null;
      setStreaming(false);
      setStreamedText('');
      setError(null);
      setLastPrompt(null);
      sessionRef.current = sessionId;
    }
  }, [sessionId, teardown]);

  useEffect(() => teardown, [teardown]);

  return { streaming, streamedText, connection, error, lastPrompt, send, retry, stop };
}
