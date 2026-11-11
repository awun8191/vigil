import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { userMessageOf } from '@/lib/errors';
import { releaseIdempotencyKey } from '@/lib/idempotency';
import type { ChatSession, RunStepEvent, StreamMessage, StreamState } from '@/types';
import { runKey, useCancelRun, useDefaultAgentConfig, useRun } from './runs';

export const SESSIONS_KEY = ['chat', 'sessions'] as const;
export const messagesKey = (sessionId: string) =>
  ['chat', 'sessions', sessionId, 'messages'] as const;

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
  return useQuery<StreamMessage[]>({
    queryKey: messagesKey(sessionId ?? 'none'),
    queryFn: () => api.listMessages(sessionId as string),
    enabled: Boolean(sessionId),
    staleTime: 5_000,
  });
}

/* -------------------------------------------------------------------------------------------
   The chat transport. A sent message IS one agent run: the message posts to POST /runs against
   the server's default agent config, ThinkingCapsule renders the run's step events, and the
   reply arrives in the run_completed frame payload (`{"content": ...}`,
   backend/src/modules/runs/loop.py). Stop posts POST /runs/{id}/cancel. History persists
   server-side: run messages carry the thread, GET /chat/sessions/{id}/messages returns the
   user prompts and assistant replies of the whole conversation.
   ------------------------------------------------------------------------------------------- */

export interface AgentChat {
  /** True while the head run is queued, running, awaiting approval or a background job. */
  streaming: boolean;
  /** Always empty: the agent reply arrives as a completed run, not token frames. */
  streamedText: string;
  connection: StreamState;
  error: string | null;
  /** Step events of the active run, oldest first, fed straight to the ThinkingCapsule. */
  steps: RunStepEvent[];
  /** True when the run parked on an approval, the Approvals page holds the decision. */
  awaitingApproval: boolean;
  lastPrompt: string | null;
  send: (prompt: string, overrideSessionId?: string) => void;
  retry: () => void;
  stop: () => void;
}

/**
 * One chat turn = one run. Each send has its own prompt and its own run; multi-turn context
 * replays like the CLI harness performs are a backend feature, not yet over HTTP, so every
 * turn stands alone.
 */
export function useAgentChat(sessionId: string | null): AgentChat {
  const queryClient = useQueryClient();
  const defaultConfig = useDefaultAgentConfig();
  const cancelRunMutation = useCancelRun();

  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastPrompt, setLastPrompt] = useState<string | null>(null);
  const [finalisedRunId, setFinalisedRunId] = useState<string | null>(null);
  const optimisticIdRef = useRef<string | null>(null);
  const sessionIdRef = useRef<string | null>(sessionId);

  const subscription = useRun(activeRunId);
  const steps = subscription.steps;
  const run = subscription.run;

  // Live typing text: only concatenate message_delta frames from the current model generation
  // turn (after the latest thought / tool_call / tool_result step), so intermediate narration
  // from earlier tool iterations never gets smashed onto the front of the final reply.
  const streamedText = useMemo(() => {
    let lastBoundary = -1;
    for (let i = steps.length - 1; i >= 0; i -= 1) {
      const t = steps[i]?.event_type;
      if (t && t !== 'message_delta' && t !== 'run_completed' && t !== 'run_failed') {
        lastBoundary = i;
        break;
      }
    }
    return steps
      .slice(lastBoundary + 1)
      .filter((s) => s.event_type === 'message_delta')
      .map((s) => (typeof s.payload['delta'] === 'string' ? (s.payload['delta'] as string) : ''))
      .join('');
  }, [steps]);

  // Rehydrate after a reload: the newest session run may still be live. One attempt per
  // session; terminal runs stay for history instead of a re-subscription.
  const rehydratedRef = useRef<string | null>(null);
  useEffect(() => {
    const target = sessionId;
    if (!target || rehydratedRef.current === target) return undefined;
    rehydratedRef.current = target;
    let cancelled = false;
    void api
      .getLatestRun(target)
      .then((run) => {
        if (cancelled || !run) return;
        const isTerminal = run.status === 'completed' || run.status === 'failed';
        if (isTerminal) return;
        setActiveRunId(run.id);
        queryClient.setQueryData(runKey(run.id), run);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [sessionId, queryClient]);

  const streaming =
    activeRunId !== null &&
    run !== null &&
    !['completed', 'failed'].includes(run.status);
  const awaitingApproval = run?.status === 'awaiting_approval';

  // Swallow a stale reader's writes into a different session.
  useEffect(() => {
    if (sessionIdRef.current !== sessionId) {
      sessionIdRef.current = sessionId;
      setActiveRunId(null);
      setFinalisedRunId(null);
      setError(null);
      setLastPrompt(null);
    }
  }, [sessionId]);

  const clearOptimisticFlag = useCallback(
    (id: string) => {
      const target = sessionIdRef.current;
      if (!target) return;
      queryClient.setQueryData<StreamMessage[]>(messagesKey(target), (current) =>
        current ? current.map((m) => (m.id === id ? { ...m, local_state: undefined } : m)) : current,
      );
    },
    [queryClient],
  );

  // The reply lands here: the run_completed frame payload carries the final text directly
  // (loop.py `{"content": final}`), falling back to the last thought text. A failed or
  // cancelled run marks the optimistic user message instead of inventing a reply.
  useEffect(() => {
    if (!activeRunId || finalisedRunId === activeRunId) return;
    const terminal = [...steps]
      .reverse()
      .find((s) => s.event_type === 'run_completed' || s.event_type === 'run_failed');
    if (!run || !terminal || !['completed', 'failed'].includes(run.status)) return;

    setFinalisedRunId(activeRunId);
    const target = sessionIdRef.current;
    const optimisticId = optimisticIdRef.current;

    const appendAnswer = (patch: Partial<StreamMessage>) => {
      if (!target) return;
      const answer: StreamMessage = {
        id: `local-run-${activeRunId}`,
        session_id: target,
        role: 'assistant',
        content: '',
        created_at: new Date().toISOString(),
        run_id: activeRunId,
        ...patch,
      };
      if (!answer.content && !answer.local_state) return;
      queryClient.setQueryData<StreamMessage[]>(messagesKey(target), (current) =>
        current ? [...current, answer] : [answer],
      );
    };

    if (run.status === 'completed') {
      const finalContent =
        typeof terminal.payload['content'] === 'string' && terminal.payload['content'].length > 0
          ? (terminal.payload['content'] as string)
          : (([...steps]
              .reverse()
              .find(
                (s) =>
                  s.event_type === 'thought' &&
                  typeof s.payload['content'] === 'string' &&
                  s.payload['content'].length > 0,
              )?.payload['content'] as string | undefined) ?? '');
      if (optimisticId) clearOptimisticFlag(optimisticId);
      appendAnswer({ content: finalContent || 'The run finished without producing a reply.' });
    } else {
      const reason =
        typeof terminal.payload['reason'] === 'string' ? (terminal.payload['reason'] as string) : 'The run failed.';
      if (reason === 'cancelled') {
        // Stopped on purpose: no reply, no failure notice.
        if (optimisticId) clearOptimisticFlag(optimisticId);
      } else if (optimisticId && target) {
        queryClient.setQueryData<StreamMessage[]>(messagesKey(target), (current) =>
          current
            ? current.map((m) =>
                m.id === optimisticId
                  ? { ...m, local_state: 'failed', failure_class: 'network', failure_reason: reason }
                  : m,
              )
            : current,
        );
      }
    }
    optimisticIdRef.current = null;
  }, [activeRunId, finalisedRunId, steps, run, queryClient, clearOptimisticFlag]);

  const send = useCallback(
    async (prompt: string, overrideSessionId?: string) => {
      const trimmed = prompt.trim();
      let target = overrideSessionId ?? sessionIdRef.current;
      if (!trimmed) return;

      if (!target) {
        try {
          const newSession = await api.createSession(
            trimmed.slice(0, 30),
            `chat:create-session:${Date.now()}`,
          );
          target = newSession.session_id;
          sessionIdRef.current = target;
        } catch (cause) {
          setError(userMessageOf(cause));
          return;
        }
      }

      let configId = defaultConfig.data?.id;
      if (!configId) {
        try {
          const cfg = await api.getDefaultAgentConfig();
          configId = cfg.id;
        } catch (cause) {
          setError(userMessageOf(cause));
          return;
        }
      }

      const localId = `local-user-${Date.now()}`;
      optimisticIdRef.current = localId;
      setLastPrompt(trimmed);
      setError(null);
      queryClient.setQueryData<StreamMessage[]>(messagesKey(target), (current) => {
        const optimistic: StreamMessage = {
          id: localId,
          session_id: target!,
          role: 'user',
          content: trimmed,
          created_at: new Date().toISOString(),
          local_state: 'sending',
        };
        return current ? [...current, optimistic] : [optimistic];
      });

      try {
        const run = await api.createRun(
          {
            config_id: configId,
            session_id: target,
            initial_prompt: trimmed,
          },
          `chat:send:${target}:${Date.now()}`,
        );
        setActiveRunId(run.id);
        queryClient.setQueryData(runKey(run.id), run);
        if (optimisticIdRef.current) {
          clearOptimisticFlag(optimisticIdRef.current);
        }
      } catch (cause: unknown) {
        optimisticIdRef.current = null;
        if (target) {
          const reason = userMessageOf(cause);
          queryClient.setQueryData<StreamMessage[]>(messagesKey(target), (current) =>
            current
              ? current.map((m) =>
                  m.id === localId
                    ? { ...m, local_state: 'failed', failure_class: 'network', failure_reason: reason }
                    : m,
                )
              : current,
          );
        }
        setError(userMessageOf(cause));
      }
    },
    [defaultConfig.data, queryClient, clearOptimisticFlag],
  );

  const stop = useCallback(() => {
    if (!activeRunId) return;
    cancelRunMutation.mutate({ runId: activeRunId, scope: `run:cancel:${activeRunId}` });
  }, [activeRunId, cancelRunMutation]);

  const retry = useCallback(() => {
    if (lastPrompt) send(lastPrompt);
  }, [lastPrompt, send]);

  return {
    streaming,
    streamedText,
    connection: subscription.connection,
    error,
    steps,
    awaitingApproval,
    lastPrompt,
    send,
    retry,
    stop,
  };
}
