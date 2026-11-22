import { useEffect, useMemo, useState } from 'react';
import { Check, HelpCircle, X } from 'lucide-react';
import {
  A2UISurface,
  setJsonPointerValue,
  type A2UIComponentNode,
  type A2UISurfacePayload,
} from '@/components/widgets/A2UISurface';
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

/**
 * Compile a set of `ask_user` questions into an A2UI v0.9 surface (`Column` + `Text` +
 * `ChoiceGroup` + `TextField` + action `Button`s) bound via RFC 6901 JSON Pointers.
 */
function buildAskUserA2UISurface(
  questions: StackedQuestionItem[],
  dataModel: Record<string, unknown>,
  isPending: boolean,
): A2UISurfacePayload {
  const components: A2UIComponentNode[] = [];
  const rootChildren: string[] = [];

  questions.forEach((q, idx) => {
    const groupId = `q_group_${q.id}`;
    const groupChildren: string[] = [];

    const titleId = `q_title_${q.id}`;
    const prefix =
      questions.length > 1
        ? `${q.header || `Q${idx + 1}`}: `
        : q.header
          ? `${q.header}: `
          : '';
    components.push({
      id: titleId,
      component: 'Text',
      props: {
        text: `${prefix}${q.question}`,
        emphasis: 'primary',
      },
    });
    groupChildren.push(titleId);

    if (q.options.length > 0) {
      const choiceId = `q_choice_${q.id}`;
      components.push({
        id: choiceId,
        component: 'ChoiceGroup',
        props: {
          options: q.options,
          multi_select: Boolean(q.multi_select),
          value: { path: `/selections/${q.id}` },
        },
      });
      groupChildren.push(choiceId);
    }

    if (q.allow_other !== false || q.options.length === 0) {
      const inputId = `q_input_${q.id}`;
      components.push({
        id: inputId,
        component: 'TextField',
        props: {
          placeholder:
            q.options.length > 0
              ? 'Or type a custom answer...'
              : 'Type your answer...',
          value: { path: `/custom/${q.id}` },
        },
      });
      groupChildren.push(inputId);
    }

    components.push({
      id: groupId,
      component: 'Column',
      props: { gap: 'tight' },
      children: groupChildren,
    });
    rootChildren.push(groupId);
  });

  components.push({
    id: 'q_divider',
    component: 'Divider',
  });
  rootChildren.push('q_divider');

  components.push({
    id: 'btn_skip',
    component: 'Button',
    props: {
      label: 'Skip',
      variant: 'ghost',
      action: { event: { name: 'skip_questions' } },
    },
  });
  components.push({
    id: 'btn_submit',
    component: 'Button',
    props: {
      label: isPending ? 'Submitting...' : questions.length > 1 ? 'Submit answers' : 'Submit',
      variant: 'primary',
      action: { event: { name: 'submit_answers' } },
    },
  });
  components.push({
    id: 'row_actions',
    component: 'Row',
    props: { align: 'between', gap: 'normal' },
    children: ['btn_skip', 'btn_submit'],
  });
  rootChildren.push('row_actions');

  components.unshift({
    id: 'ask_root',
    component: 'Column',
    props: { gap: 'relaxed' },
    children: rootChildren,
  });

  return {
    version: 'v0.9',
    catalog_id: 'urn:vigil:a2ui:catalog:panels:v1',
    size: 'lg',
    icon: 'sparkles',
    root: 'ask_root',
    components,
    data_model: dataModel,
  };
}

export function AskUserQuestionCard({ approvalId, initialData, initialApproval }: Props) {
  const effectiveId = approvalId || (initialData?.approval_id ? String(initialData.approval_id) : '');
  const approvalQuery = useApproval(effectiveId || null);
  const approval = initialApproval ?? approvalQuery.data;
  const decideMutation = useDecideApproval();

  const args = useMemo(
    () =>
      approval?.arguments ||
      (initialData?.arguments as Record<string, unknown>) ||
      (initialData?.args as Record<string, unknown>) ||
      {},
    [approval?.arguments, initialData],
  );

  const questions = useMemo(() => extractQuestions(args), [args]);
  const contextText = typeof args.context === 'string' ? args.context : null;

  const [dataModel, setDataModel] = useState<Record<string, unknown>>(() => ({
    selections: {},
    custom: {},
  }));
  const [localDecision, setLocalDecision] = useState<'approved' | 'rejected' | null>(null);

  useEffect(() => {
    setDataModel((prev) => {
      const selections = { ...((prev.selections as Record<string, unknown>) ?? {}) };
      const custom = { ...((prev.custom as Record<string, unknown>) ?? {}) };
      for (const q of questions) {
        if (!(q.id in selections)) {
          selections[q.id] = q.multi_select ? [] : '';
        }
        if (!(q.id in custom)) {
          custom[q.id] = '';
        }
      }
      return { selections, custom };
    });
  }, [questions]);

  const status = localDecision || approval?.status || 'pending';
  const isPending = decideMutation.isPending;
  const savedAnswers = useMemo(
    () => parseSubmittedAnswers(approval?.decision_note),
    [approval?.decision_note],
  );

  const selectionsMap = (dataModel.selections as Record<string, unknown>) ?? {};
  const customMap = (dataModel.custom as Record<string, unknown>) ?? {};

  const hasAnyAnswer = questions.some((q) => {
    const rawPicked = selectionsMap[q.id];
    const pickedCount = Array.isArray(rawPicked)
      ? rawPicked.length
      : typeof rawPicked === 'string' && rawPicked.trim().length > 0
        ? 1
        : 0;
    const custom = String(customMap[q.id] ?? '').trim();
    return pickedCount > 0 || custom.length > 0;
  });

  const handleUpdateDataModel = (path: string, value: unknown) => {
    if (status !== 'pending' || isPending) return;
    setDataModel((prev) => setJsonPointerValue(prev, path, value));
  };

  const handleSubmit = () => {
    if (!effectiveId || !hasAnyAnswer || status !== 'pending' || isPending) return;
    const answersPayload: Record<string, unknown> = {};
    for (const q of questions) {
      const rawPicked = selectionsMap[q.id];
      const picked: string[] = Array.isArray(rawPicked)
        ? rawPicked.map(String)
        : typeof rawPicked === 'string' && rawPicked.trim()
          ? [rawPicked.trim()]
          : [];
      const custom = String(customMap[q.id] ?? '').trim();
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
    if (!effectiveId || status !== 'pending' || isPending) return;
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

  const handleEventAction = (name: string) => {
    if (name === 'submit_answers') {
      handleSubmit();
    } else if (name === 'skip_questions') {
      handleDismiss();
    }
  };

  const a2uiSurface = useMemo(
    () => buildAskUserA2UISurface(questions, dataModel, isPending),
    [questions, dataModel, isPending],
  );

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
              {questions.length > 1
                ? `Quick Clarification (${questions.length} questions)`
                : 'Quick Clarification'}
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

      <div className="mt-4">
        {status !== 'pending' && savedAnswers ? (
          <div className="space-y-2.5">
            {questions.map((q) => {
              const savedVal = savedAnswers[q.header || q.id] ?? savedAnswers[q.id];
              return (
                <div key={q.id} className="space-y-1">
                  <p className="text-[13px] font-medium text-[var(--text)]">{q.question}</p>
                  <div className="rounded-[14px] border border-[var(--border)] bg-[var(--panel-inset)] px-3.5 py-2 text-[12.5px] text-[var(--text)]">
                    {Array.isArray(savedVal)
                      ? savedVal.join(', ')
                      : String(savedVal ?? 'Answered')}
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <A2UISurface
            surface={a2uiSurface}
            onUpdateDataModel={handleUpdateDataModel}
            onEventAction={handleEventAction}
            disabled={status !== 'pending' || isPending}
          />
        )}
      </div>
    </div>
  );
}

export default AskUserQuestionCard;
