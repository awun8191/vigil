import { useState } from 'react';
import { Check, Loader2, ShieldAlert, X } from 'lucide-react';
import { useApproval, useDecideApproval } from '@/queries/approvals';
import type { Approval } from '@/types';

interface Props {
  approvalId?: string | null;
  initialData?: Record<string, unknown> | null;
  initialApproval?: Approval;
}

export function InlineApprovalCard({ approvalId, initialData, initialApproval }: Props) {
  const effectiveId = approvalId || (initialData?.approval_id ? String(initialData.approval_id) : '');
  const approvalQuery = useApproval(effectiveId || null);
  const approval = initialApproval ?? approvalQuery.data;
  const decideMutation = useDecideApproval();
  const [localDecision, setLocalDecision] = useState<'approved' | 'rejected' | null>(null);

  const toolName =
    approval?.tool_name ||
    (initialData?.tool_name ? String(initialData.tool_name) : '') ||
    (initialData?.tool ? String(initialData.tool) : '') ||
    'action_execution';

  const args =
    approval?.arguments ||
    (initialData?.arguments as Record<string, unknown>) ||
    (initialData?.args as Record<string, unknown>) ||
    {};

  const status = localDecision || approval?.status || 'pending';
  const isPending = decideMutation.isPending;

  const handleDecision = (decision: 'approved' | 'rejected') => {
    if (!effectiveId) return;
    setLocalDecision(decision);
    decideMutation.mutate(
      {
        approvalId: effectiveId,
        body: { decision },
        scope: `approval:decide:${effectiveId}:${decision}`,
      },
      {
        onError: () => {
          setLocalDecision(null);
        },
      },
    );
  };

  return (
    <div className="approval-surface my-2 overflow-hidden p-4">
      <div className="flex flex-wrap items-center justify-between gap-2.5">
        <div className="flex min-w-0 items-center gap-2">
          <ShieldAlert size={16} strokeWidth={1.9} className="shrink-0 text-amber-500" />
          <span className="truncate text-[13px] font-semibold text-[var(--text)]">
            Action Approval Required
          </span>
        </div>
        <span className="shrink-0 rounded-full border border-[var(--approval-border)] bg-[var(--approval-bg)] px-2.5 py-0.5 font-mono text-[10.5px] font-medium text-[var(--approval-text)]">
          {toolName}
        </span>
      </div>

      <div className="mt-3">
        <div className="text-[10px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted)]">
          Tool arguments
        </div>
        {Object.keys(args).length > 0 ? (
          <pre className="mt-1.5 max-h-44 overflow-auto rounded-[12px] border border-[var(--border)] bg-[var(--surface-solid)] px-4 py-3 font-mono text-[12px] leading-relaxed text-[var(--text)]">
            {JSON.stringify(args, null, 2)}
          </pre>
        ) : (
          <div className="mt-1.5 rounded-[12px] border border-[var(--border)] bg-[var(--surface-solid)] px-4 py-3 font-mono text-[12px] text-[var(--text-muted)]">
            // No arguments passed
          </div>
        )}
      </div>

      <div className="mt-4 flex flex-wrap items-center justify-between gap-2.5">
        <span className="text-[11px] leading-relaxed text-[var(--text-muted)]">
          {status === 'approved' ? (
            <span className="inline-flex items-center gap-1 font-medium text-emerald-500">
              <Check size={13} strokeWidth={2} /> Action approved
            </span>
          ) : status === 'rejected' ? (
            <span className="inline-flex items-center gap-1 font-medium text-rose-500">
              <X size={13} strokeWidth={2} /> Action rejected
            </span>
          ) : (
            'Requires your manual authorization to proceed'
          )}
        </span>

        {status === 'pending' ? (
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => handleDecision('rejected')}
              disabled={isPending || !effectiveId}
              className="flex h-9 items-center gap-1.5 rounded-full border border-[var(--border-strong)] bg-[var(--surface-solid)] px-4 text-[12.5px] font-semibold text-[var(--text)] transition-all hover:bg-[var(--panel-inset)] active:scale-95 disabled:opacity-50"
            >
              {isPending && localDecision === 'rejected' ? (
                <Loader2 size={13} className="animate-spin" />
              ) : (
                <X size={13} strokeWidth={2} />
              )}
              Reject
            </button>
            <button
              type="button"
              onClick={() => handleDecision('approved')}
              disabled={isPending || !effectiveId}
              className="flex h-9 items-center gap-1.5 rounded-full bg-[var(--btn-primary-bg)] px-4 text-[12.5px] font-semibold text-[var(--btn-primary-ink)] transition-transform hover:scale-[1.03] active:scale-95 disabled:opacity-50"
            >
              {isPending && localDecision === 'approved' ? (
                <Loader2 size={13} className="animate-spin" />
              ) : (
                <Check size={13} strokeWidth={2} />
              )}
              Approve
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}

export default InlineApprovalCard;
