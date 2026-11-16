import { useMemo, useState } from 'react';
import { Check, HelpCircle, Loader2, Send, X } from 'lucide-react';
import { useApproval, useDecideApproval } from '@/queries/approvals';
import type { Approval } from '@/types';

export interface QuestionOptionItem {
  label: string;
  description?: string | null;
}

export interface StackedQuestionItem {
  id: string;
  header?: string | null;
  question: string;
  options: QuestionOptionItem[];
  multi_select?: boolean;
  allow_other?: boolean;
}

interface Props {
  approvalId?: string | null;
  initialData?: Record<string, unknown> | null;
  initialApproval?: Approval;
}

function normalizeOptions(raw: unknown): QuestionOptionItem[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((item) => {
      if (typeof item === 'string') return { label: item };
      if (typeof item === 'object' && item !== null) {
        const rec = item as Record<string, unknown>;
        const label = typeof rec.label === 'string' ? rec.label : String(rec.value ?? '');
        if (!label) return null;
        return {
          label,
          description: typeof rec.description === 'string' ? rec.description : null,
        };
      }
      return null;
    })
    .filter((x): x is QuestionOptionItem => x !== null);
}

function extractQuestions(args: Record<string, unknown>): StackedQuestionItem[] {
  if (Array.isArray(args.questions) && args.questions.length > 0) {
    return args.questions
      .map<StackedQuestionItem | null>((raw, idx) => {
        if (typeof raw !== 'object' || raw === null) return null;
        const rec = raw as Record<string, unknown>;
        const qText = typeof rec.question === 'string' ? rec.question : '';
        if (!qText) return null;
        return {
          id: typeof rec.id === 'string' && rec.id ? rec.id : `q_${idx + 1}`,
          header: typeof rec.header === 'string' ? rec.header : null,
          question: qText,
          options: normalizeOptions(rec.options),
          multi_select: Boolean(rec.multi_select),
          allow_other: rec.allow_other !== false,
        };
      })
      .filter((x): x is StackedQuestionItem => x !== null);
  }

  const singleQ = typeof args.question === 'string' ? args.question : '';
  if (!singleQ) return [];
  return [
    {
      id: 'q_1',
      header: null,
      question: singleQ,
      options: normalizeOptions(args.options),
      multi_select: Boolean(args.multi_select),
      allow_other: args.allow_other !== false,
    },
  ];
}

function parseSubmittedAnswers(note: string | null | undefined): Record<string, unknown> | null {
  if (!note) return null;
  try {
    const parsed = JSON.parse(note) as unknown;
    if (typeof parsed === 'object' && parsed !== null) {
      const rec = parsed as Record<string, unknown>;
      if (typeof rec.answers === 'object' && rec.answers !== null) {
        return rec.answers as Record<string, unknown>;
      }
      return rec;
    }
  } catch {
    return { answer: note };
  }
  return null;
}

export function AskUserQuestionCard({ approvalId, initialData, initialApproval }: Props) {
  const effectiveId = approvalId || (initialData?.approval_id ? String(initialData.approval_id) : '');
  const approvalQuery = useApproval(effectiveId || null);
  const approval = initialApproval ?? approvalQuery.data;
  const decideMutation = useDecideApproval();

  const args =
    approval?.arguments ||
    (initialData?.arguments as Record<string, unknown>) ||
    (initialData?.args as Record<string, unknown>) ||
    {};

  const questions = useMemo(() => extractQuestions(args), [args]);
  const contextText = typeof args.context === 'string' ? args.context : null;

  const [selections, setSelections] = useState<Record<string, string[]>>({});
  const [otherInputs, setOtherInputs] = useState<Record<string, string>>({});
  const [localDecision, setLocalDecision] = useState<'approved' | 'rejected' | null>(null);

  const status = localDecision || approval?.status || 'pending';
  const isPending = decideMutation.isPending;
  const savedAnswers = useMemo(
    () => parseSubmittedAnswers(approval?.decision_note),
    [approval?.decision_note],
  );

  const toggleOption = (q: StackedQuestionItem, label: string) => {
    if (status !== 'pending') return;
    setSelections((prev) => {
      const current = prev[q.id] ?? [];
      if (q.multi_select) {
        const next = current.includes(label)
          ? current.filter((item) => item !== label)
          : [...current, label];
        return { ...prev, [q.id]: next };
      }
      return { ...prev, [q.id]: [label] };
    });
  };

  const hasAnyAnswer = questions.some((q) => {
    const picked = selections[q.id] ?? [];
    const custom = (otherInputs[q.id] ?? '').trim();
    return picked.length > 0 || custom.length > 0;
  });

  const handleSubmit = () => {
    if (!effectiveId || !hasAnyAnswer) return;
    const answersPayload: Record<string, unknown> = {};
    for (const q of questions) {
      const picked = selections[q.id] ?? [];
      const custom = (otherInputs[q.id] ?? '').trim();
      const combined = custom ? [...picked, custom] : picked;
      const key = q.header || q.id;
      answersPayload[key] = q.multi_select ? combined : (combined[0] ?? '');
    }

    setLocalDecision('approved');
    decideMutation.mutate(
      {
        approvalId: effectiveId,
        body: {
          decision: 'approved',
          answers: answersPayload,
          decision_note: JSON.stringify({ answers: answersPayload }),
        },
        scope: `approval:ask-user:${effectiveId}`,
      },
      {
        onError: () => setLocalDecision(null),
      },
    );
  };

  const handleDismiss = () => {
    if (!effectiveId) return;
    setLocalDecision('rejected');
    decideMutation.mutate(
      {
        approvalId: effectiveId,
        body: {
          decision: 'rejected',
          decision_note: 'User skipped answering the questions.',
        },
        scope: `approval:ask-user-skip:${effectiveId}`,
      },
      {
        onError: () => setLocalDecision(null),
      },
    );
  };

  if (questions.length === 0) {
    return null;
  }

  return (
    <div className="hark-card my-2.5 overflow-hidden rounded-[22px] p-4 sm:p-5">
      <div className="flex items-center justify-between gap-2 border-b border-[var(--border)] pb-3">
        <div className="flex items-center gap-2">
          <span className="flex h-7 w-7 items-center justify-center rounded-full bg-[var(--panel-inset)] text-[var(--text)]">
            <HelpCircle size={15} strokeWidth={1.8} />
          </span>
          <div>
            <h3 className="text-[13.5px] font-semibold text-[var(--text)]">
              {questions.length > 1 ? `Quick Clarification (${questions.length} questions)` : 'Quick Clarification'}
            </h3>
            {contextText ? (
              <p className="mt-0.5 text-[12px] text-[var(--text-muted)]">{contextText}</p>
            ) : null}
          </div>
        </div>

        {status === 'approved' ? (
          <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/12 px-2.5 py-1 text-[11px] font-medium text-emerald-500">
            <Check size={12} strokeWidth={2.2} /> Answered
          </span>
        ) : status === 'rejected' ? (
          <span className="inline-flex items-center gap-1 rounded-full bg-rose-500/12 px-2.5 py-1 text-[11px] font-medium text-rose-500">
            <X size={12} strokeWidth={2.2} /> Skipped
          </span>
        ) : null}
      </div>

      {/* Stacked Questions */}
      <div className="mt-4 space-y-5">
        {questions.map((q, qIdx) => {
          const picked = selections[q.id] ?? [];
          const customText = otherInputs[q.id] ?? '';
          const savedVal = savedAnswers?.[q.header || q.id] ?? savedAnswers?.[q.id];

          return (
            <div key={q.id} className="space-y-2.5">
              <div className="flex items-baseline gap-2">
                {questions.length > 1 ? (
                  <span className="shrink-0 rounded-full bg-[var(--panel-inset)] px-2 py-0.5 font-mono text-[10.5px] font-semibold text-[var(--text-muted)]">
                    {q.header || `Q${qIdx + 1}`}
                  </span>
                ) : q.header ? (
                  <span className="shrink-0 rounded-full bg-[var(--panel-inset)] px-2 py-0.5 font-mono text-[10.5px] font-semibold text-[var(--text-muted)]">
                    {q.header}
                  </span>
                ) : null}
                <p className="text-[13.5px] font-medium leading-snug text-[var(--text)]">
                  {q.question}
                </p>
              </div>

              {status !== 'pending' && savedVal !== undefined ? (
                <div className="rounded-[14px] border border-[var(--border)] bg-[var(--panel-inset)] px-3.5 py-2.5 text-[12.5px] text-[var(--text)]">
                  {Array.isArray(savedVal) ? savedVal.join(', ') : String(savedVal)}
                </div>
              ) : (
                <>
                  {q.options.length > 0 ? (
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                      {q.options.map((opt) => {
                        const isSelected = picked.includes(opt.label);
                        const isRecommended = opt.label.toLowerCase().includes('recommended');
                        return (
                          <button
                            key={opt.label}
                            type="button"
                            disabled={status !== 'pending' || isPending}
                            onClick={() => toggleOption(q, opt.label)}
                            className={`flex items-start gap-2.5 rounded-[14px] border px-3.5 py-2.5 text-left transition-all ${
                              isSelected
                                ? 'border-[var(--text)] bg-[color-mix(in_srgb,var(--text)_8%,transparent)] shadow-xs'
                                : 'border-[var(--border)] bg-[color-mix(in_srgb,var(--text)_2.5%,transparent)] hover:border-[var(--border-strong)] hover:bg-[color-mix(in_srgb,var(--text)_5%,transparent)]'
                            } disabled:opacity-60`}
                          >
                            <span
                              className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center border ${
                                q.multi_select ? 'rounded-[4px]' : 'rounded-full'
                              } ${
                                isSelected
                                  ? 'border-[var(--text)] bg-[var(--text)] text-[var(--surface-solid)]'
                                  : 'border-[var(--border-strong)]'
                              }`}
                            >
                              {isSelected ? <Check size={11} strokeWidth={2.5} /> : null}
                            </span>
                            <span className="min-w-0 flex-1">
                              <span className="flex flex-wrap items-center gap-1.5 text-[12.5px] font-medium text-[var(--text)]">
                                <span>{opt.label.replace(/\s*\(Recommended\)/i, '')}</span>
                                {isRecommended ? (
                                  <span className="rounded-full bg-[var(--panel-inset)] px-1.5 py-0.5 text-[10px] font-semibold text-[var(--text-muted)]">
                                    Recommended
                                  </span>
                                ) : null}
                              </span>
                              {opt.description ? (
                                <span className="mt-0.5 block text-[11.5px] leading-snug text-[var(--text-muted)]">
                                  {opt.description}
                                </span>
                              ) : null}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  ) : null}

                  {q.allow_other !== false || q.options.length === 0 ? (
                    <input
                      type="text"
                      disabled={status !== 'pending' || isPending}
                      value={customText}
                      onChange={(e) =>
                        setOtherInputs((prev) => ({ ...prev, [q.id]: e.target.value }))
                      }
                      placeholder={
                        q.options.length > 0
                          ? 'Other (optional custom answer)...'
                          : 'Type your answer...'
                      }
                      className="w-full rounded-[12px] border border-[var(--border)] bg-[var(--surface-solid)] px-3.5 py-2 text-[12.5px] text-[var(--text)] outline-none placeholder:text-[var(--text-muted)] focus:border-[var(--text)]"
                    />
                  ) : null}
                </>
              )}
            </div>
          );
        })}
      </div>

      {status === 'pending' ? (
        <div className="mt-5 flex flex-wrap items-center justify-between gap-2 border-t border-[var(--border)] pt-3.5">
          <button
            type="button"
            onClick={handleDismiss}
            disabled={isPending || !effectiveId}
            className="inline-flex h-9 items-center gap-1.5 rounded-full border border-[var(--border-strong)] px-4 text-[12.5px] font-medium text-[var(--text-muted)] transition-colors hover:text-[var(--text)] disabled:opacity-50"
          >
            Skip
          </button>

          <button
            type="button"
            onClick={handleSubmit}
            disabled={isPending || !effectiveId || !hasAnyAnswer}
            className="inline-flex h-9 items-center gap-1.5 rounded-full bg-[var(--btn-primary-bg)] px-5 text-[12.5px] font-semibold text-[var(--btn-primary-ink)] transition-transform hover:scale-[1.02] active:scale-95 disabled:opacity-45"
          >
            {isPending ? <Loader2 size={13} className="animate-spin" /> : <Send size={13} strokeWidth={2} />}
            Submit Answers
          </button>
        </div>
      ) : null}
    </div>
  );
}

export default AskUserQuestionCard;
