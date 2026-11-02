import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { releaseIdempotencyKey } from '@/lib/idempotency';
import type { Approval, ApprovalDecisionRequest } from '@/types';
import { CARDS_KEY } from './cards';

export const APPROVALS_KEY = ['approvals'] as const;

export function useApprovals() {
  return useQuery<Approval[]>({
    queryKey: APPROVALS_KEY,
    queryFn: () => api.listApprovals(),
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
  });
}

export function useApproval(approvalId: string | null) {
  return useQuery<Approval>({
    queryKey: [...APPROVALS_KEY, approvalId],
    queryFn: () => api.getApproval(approvalId as string),
    enabled: Boolean(approvalId),
  });
}

/**
 * Decisions post to the approvals module, never to `/cards/{id}/feedback`. Feedback is telemetry.
 */
export function useDecideApproval() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      approvalId,
      body,
      scope,
    }: {
      approvalId: string;
      body: ApprovalDecisionRequest;
      scope: string;
    }) => api.decideApproval(approvalId, body, scope),

    onSettled: (_data, _error, variables) => {
      releaseIdempotencyKey(variables.scope);
      void queryClient.invalidateQueries({ queryKey: APPROVALS_KEY });
      void queryClient.invalidateQueries({ queryKey: CARDS_KEY });
    },
  });
}
