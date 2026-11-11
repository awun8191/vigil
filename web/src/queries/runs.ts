import { useCallback, useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { releaseIdempotencyKey } from '@/lib/idempotency';
import type { AgentConfigResponse, Artifact, Run, RunStatus, RunStep, RunStepEvent } from '@/types';

export const runKey = (runId: string) => ['runs', runId] as const;
export const runStepsKey = (runId: string) => ['runs', runId, 'steps'] as const;
export const runArtifactsKey = (runId: string) => ['runs', runId, 'artifacts'] as const;
export const RUN_CONFIG_KEY = ['runs', 'default-config'] as const;

/** The pinned server-side default agent config, created on the first call, cached for the
 *  session. Every run creator (chat sends, card run actions) keys off the same row. */
export function useDefaultAgentConfig() {
  return useQuery<AgentConfigResponse>({
    queryKey: RUN_CONFIG_KEY,
    queryFn: () => api.getDefaultAgentConfig(),
    staleTime: Infinity,
  });
}

const TERMINAL_STATUSES: Run['status'][] = ['completed', 'failed'];

const STEP_LABELS: Record<string, string> = {
  thought: 'Thinking',
  tool_call: 'Using a tool',
  tool_result: 'Tool finished',
  approval_required: 'Waiting on you',
  run_completed: 'Done',
  run_failed: 'Failed',
};

/** One line for the ThinkingCapsule-style step list. */
export function stepLabel(step: RunStepEvent): string {
  const base = STEP_LABELS[step.event_type] ?? step.event_type;
  const detail =
    step.payload['content'] ?? step.payload['text'] ?? step.payload['tool_name'] ?? step.payload['reason'];
  return typeof detail === 'string' && detail.length > 0
    ? `${base}: ${detail.length > 90 ? `${detail.slice(0, 90)}...` : detail}`
    : base;
}

export interface RunSubscription {
  run: Run | null;
  /** Steps received so far, oldest first, both from fetch and the stream. */
  steps: RunStepEvent[];
  connection: 'idle' | 'connecting' | 'open' | 'reconnecting' | 'closed';
}

/**
 * One run's live state: the run row, its steps, and the SSE subscription that keeps both
 * current.
 *
 * Fetches `GET /runs/{id}` once, then opens `GET /runs/{id}/events`. Frames carry the step
 * number as the SSE id, so a reconnect replays with `Last-Event-ID` and nothing is missed
 * between fetch and stream. A fetched snapshot can race the stream, so every frame is merged
 * by `step_number` rather than appended blindly. The subscription closes itself when the
 * backend closes the stream, which it does only on terminal statuses.
 */
export function useRun(runId: string | null): RunSubscription {
  const queryClient = useQueryClient();

  const [steps, setSteps] = useState<RunStepEvent[]>([]);
  const [connection, setConnection] = useState<RunSubscription['connection']>('idle');
  const bufferRef = useRef<RunStepEvent[]>([]);
  const streamVersionRef = useRef(0);

  // Merging on every single frame would thrash React. Frames land in a ref buffer and a
  // rAF-aligned flush publishes them at most once per frame.
  const scheduleFlush = useCallback(() => {
    if (bufferRef.current.length === 0) return;
    const buffered = bufferRef.current;
    bufferRef.current = [];
    streamVersionRef.current += 1;
    setSteps((current) => mergeSteps(current, buffered));
  }, []);

  const runQuery = useQuery<Run>({
    queryKey: runKey(runId ?? 'none'),
    queryFn: () => api.getRun(runId as string),
    enabled: Boolean(runId),
  });

  useEffect(() => {
    if (!runId) {
      bufferRef.current = [];
      setSteps([]);
      setConnection('idle');
      return undefined;
    }

    bufferRef.current = [];
    setSteps([]);
    streamVersionRef.current += 1;

    // Buffered snapshot first: GET /runs/{id}/steps seeds the list, the stream then carries
    // only frames past that point.
    let cancelled = false;
    void api
      .getRunSteps(runId)
      .then((history: RunStep[]) => {
        if (cancelled) return;
        setSteps(
          history.map((step) => ({
            run_id: step.run_id,
            step_number: step.step_number,
            event_type: step.event_type,
            payload: step.payload,
            created_at: step.created_at,
          })),
        );
      })
      .catch(() => {
        // The stream replays from the cursor anyway, an unreachable history is not fatal.
      });

    const close = api.openRunStream(runId, {
      handlers: {
        onEvent(event) {
          let parsed: RunStepEvent;
          try {
            parsed = JSON.parse(event.data) as RunStepEvent;
          } catch {
            return;
          }
          // Terminal frames override whatever the payload says, the SSE event name is what
          // the backend wrote and the payload mirrors it.
          const eventType: RunStepEvent['event_type'] =
            event.type === 'run_completed' || event.type === 'run_failed' ? event.type : parsed.event_type;
          bufferRef.current = [...bufferRef.current, { ...parsed, event_type: eventType }];
          scheduleFlush();
        },
        onStateChange(state) {
          setConnection(state);
        },
      },
    });

    return () => {
      cancelled = true;
      close();
    };
  }, [runId, queryClient, scheduleFlush]);

  // The stream carries the terminal step, but the run row was fetched once when the run was
  // created (still queued). Without this derivation the finaliser waits on a status that only
  // a later refetch would deliver, which made replies appear only after a tab refresh.
  useEffect(() => {
    if (!runId) return;
    const terminal = [...steps].reverse().find((s) => s.event_type === 'run_completed' || s.event_type === 'run_failed');
    if (!terminal) return;
    queryClient.setQueryData<Run>(runKey(runId), (current) => {
      if (!current || TERMINAL_STATUSES.includes(current.status)) return current;
      const status: RunStatus = terminal.event_type === 'run_completed' ? 'completed' : 'failed';
      return { ...current, status };
    });
  }, [runId, steps, queryClient]);

  return { run: runQuery.data ?? null, steps, connection };
}

/** Merge by step number, deduping re-delivered frames after a reconnect. */
function mergeSteps(current: RunStepEvent[], incoming: RunStepEvent[]): RunStepEvent[] {
  const byStep = new Map<number, RunStepEvent>();
  for (const step of current) byStep.set(step.step_number, step);
  for (const step of incoming) {
    const existing = byStep.get(step.step_number);
    if (!existing || existing.event_type !== step.event_type) byStep.set(step.step_number, step);
  }
  return [...byStep.values()].sort((a, b) => a.step_number - b.step_number);
}

export function useCancelRun() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ runId, scope }: { runId: string; scope: string }) =>
      api.cancelRun(runId, scope),

    onMutate: async ({ runId }) => {
      await queryClient.cancelQueries({ queryKey: runKey(runId) });
      const previous = queryClient.getQueryData<Run>(runKey(runId));
      if (previous && !TERMINAL_STATUSES.includes(previous.status)) {
        queryClient.setQueryData<Run>(runKey(runId), { ...previous, status: 'failed' });
      }
      return { previous };
    },

    onError: (_error, variables, context) => {
      if (context?.previous) queryClient.setQueryData(runKey(variables.runId), context.previous);
    },

    onSuccess: (result) => {
      queryClient.setQueryData<Run>(runKey(result.run_id), (current) =>
        current ? { ...current, status: (result.status as Run['status']) ?? current.status } : current,
      );
    },

    onSettled: (_data, _error, variables) => {
      releaseIdempotencyKey(variables.scope);
      void queryClient.invalidateQueries({ queryKey: runKey(variables.runId) });
    },
  });
}

export function useRunSteps(runId: string | null, sinceStep = 0) {
  return useQuery<RunStep[]>({
    queryKey: [...runStepsKey(runId ?? 'none'), sinceStep],
    queryFn: () => api.getRunSteps(runId as string, sinceStep),
    enabled: Boolean(runId),
  });
}

export function useRunArtifacts(runId: string | null) {
  return useQuery<Artifact[]>({
    queryKey: runArtifactsKey(runId ?? 'none'),
    queryFn: () => api.listRunArtifacts(runId as string),
    enabled: Boolean(runId),
  });
}
