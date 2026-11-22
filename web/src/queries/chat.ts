import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { userMessageOf } from '@/lib/errors';
import { releaseIdempotencyKey } from '@/lib/idempotency';
import type {
  ChatSession,
  RunAttachmentItem,
  RunStepEvent,
  StreamMessage,
  StreamState,
} from '@/types';
import { projectDetailKey } from './projects';
import { runKey, useCancelRun, useDefaultAgentConfig, useRun } from './runs';

export const PRIMARY_SESSION_KEY = ['chat', 'primary'] as const;
export const SESSIONS_KEY = ['chat', 'sessions'] as const;
export const messagesKey = (sessionId: string) =>
  ['chat', 'sessions', sessionId, 'messages'] as const;

export function usePrimarySession() {
  return useQuery<ChatSession>({
    queryKey: PRIMARY_SESSION_KEY,
    queryFn: () => api.getPrimarySession(),
  });
}

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
    queryFn: () => api.listMessages(sessionId as string, { limit: 100 }),
    enabled: Boolean(sessionId),
    staleTime: 5_000,
  });
}

export function useLoadEarlierMessages(sessionId: string | null) {
  const queryClient = useQueryClient();
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const [hasMore, setHasMore] = useState(true);

  useEffect(() => {
    setHasMore(true);
  }, [sessionId]);

  const loadEarlier = useCallback(async () => {
    if (!sessionId || loadingEarlier || !hasMore) return;
    const current = queryClient.getQueryData<StreamMessage[]>(messagesKey(sessionId)) ?? [];
    const oldest = current[0];
    if (!oldest) return;
    setLoadingEarlier(true);
    try {
      const pageSize = 100;
      const older = await api.listMessages(sessionId, {
        limit: pageSize,
        beforeId: oldest.id,
      });
      if (older.length < pageSize) {
        setHasMore(false);
      }
      if (older.length > 0) {
        queryClient.setQueryData<StreamMessage[]>(messagesKey(sessionId), (prev) => {
          const existingIds = new Set((prev ?? []).map((m) => m.id));
          const deduped = older.filter((m) => !existingIds.has(m.id));
          return [...deduped, ...(prev ?? [])];
        });
      }
    } finally {
      setLoadingEarlier(false);
    }
  }, [sessionId, loadingEarlier, hasMore, queryClient]);

  return { loadEarlier, loadingEarlier, hasMore };
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
  send: (prompt: string, overrideSessionId?: string, files?: File[]) => void;
  retry: () => void;
  stop: () => void;
}

/**
 * One chat turn = one run. Each send has its own prompt and its own run; multi-turn context
 * replays like the CLI harness performs are a backend feature, not yet over HTTP, so every
 * turn stands alone.
 */
export function useAgentChat(
  sessionId: string | null,
  projectId: string | null = null,
): AgentChat {
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
  // Do NOT reset activeRunId when transitioning from null -> newly created sessionId
  // on the first message of a new chat, otherwise the in-flight run stream is killed.
  useEffect(() => {
    const prev = sessionIdRef.current;
    if (prev !== sessionId) {
      sessionIdRef.current = sessionId;
      if (prev !== null) {
        setActiveRunId(null);
        setFinalisedRunId(null);
        setError(null);
        setLastPrompt(null);
      }
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
    async (prompt: string, overrideSessionId?: string, files?: File[]) => {
      const trimmed = prompt.trim();
      const attachedFiles = files ?? [];
      let target = overrideSessionId ?? sessionIdRef.current;
      if (!trimmed && attachedFiles.length === 0) return;

      if (!target) {
        try {
          const primary = await api.getPrimarySession();
          target = primary.session_id;
          sessionIdRef.current = target;
          queryClient.setQueryData(PRIMARY_SESSION_KEY, primary);
        } catch (cause) {
          setError(userMessageOf(cause));
          return;
        }
      } else if (sessionIdRef.current !== target) {
        sessionIdRef.current = target;
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

      const optimisticAttachments: RunAttachmentItem[] = attachedFiles.map((f) => ({
        name: f.name,
        workspace_path: `/workspace/${f.name}`,
        content_type: f.type || 'application/octet-stream',
        size_bytes: f.size,
      }));

      const displayPrompt =
        trimmed ||
        (attachedFiles.length === 1
          ? `Attached ${attachedFiles[0]?.name ?? 'file'}`
          : `Attached ${attachedFiles.length} files`);

      const localId = `local-user-${Date.now()}`;
      optimisticIdRef.current = localId;
      setLastPrompt(displayPrompt);
      setError(null);
      queryClient.setQueryData<StreamMessage[]>(messagesKey(target), (current) => {
        const optimistic: StreamMessage = {
          id: localId,
          session_id: target!,
          role: 'user',
          content: trimmed,
          created_at: new Date().toISOString(),
          local_state: 'sending',
          ...(optimisticAttachments.length > 0
            ? { attachments: optimisticAttachments }
            : {}),
        };
        return current ? [...current, optimistic] : [optimistic];
      });

      try {
        let uploadedAttachments: RunAttachmentItem[] = [];
        if (attachedFiles.length > 0) {
          const uploadRes = await api.uploadRunFiles(attachedFiles, {
            sessionId: target,
            projectId,
          });
          uploadedAttachments = uploadRes.files;
          if (projectId) {
            void queryClient.invalidateQueries({
              queryKey: projectDetailKey(projectId),
            });
          }
        }

        const run = await api.createRun(
          {
            config_id: configId,
            session_id: target,
            initial_prompt: trimmed || null,
            project_id: projectId ?? null,
            ...(uploadedAttachments.length > 0
              ? { attachments: uploadedAttachments }
              : {}),
          },
          `chat:send:${target}:${Date.now()}`,
        );
        setActiveRunId(run.id);
        queryClient.setQueryData(runKey(run.id), run);
        if (target) {
          queryClient.setQueryData<StreamMessage[]>(messagesKey(target), (current) =>
            current
              ? current.map((m) =>
                  m.id === localId
                    ? {
                        ...m,
                        run_id: run.id,
                        local_state: undefined,
                        ...(uploadedAttachments.length > 0
                          ? { attachments: uploadedAttachments }
                          : {}),
                      }
                    : m,
                )
              : current,
          );
        }
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
    [defaultConfig.data, queryClient, clearOptimisticFlag, projectId],
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
