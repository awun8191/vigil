import { useEffect, useRef, useState } from 'react';
import {
  Bold,
  Check,
  Code,
  Eye,
  Italic,
  Link2,
  List,
  ListOrdered,
  Loader2,
  Mail,
  Maximize2,
  Minimize2,
  Pencil,
  Quote,
  Send,
  X,
} from 'lucide-react';
import { MarkdownContent } from '@/components/chat/MarkdownContent';
import { useApproval, useDecideApproval } from '@/queries/approvals';
import type { Approval } from '@/types';

interface Props {
  title?: string;
  to?: string | string[];
  cc?: string | string[];
  subject?: string;
  body?: string;
  threadId?: string;
  promptQuestion?: string;
  approvalId?: string | null;
  initialData?: Record<string, unknown> | null;
  initialApproval?: Approval;
  mode?: 'approval' | 'draft' | 'preview';
  onSend?: (edited?: { to: string[]; cc: string[]; subject: string; body: string }) => void;
}

function formatRecipients(val: unknown): string {
  if (Array.isArray(val)) return val.map(String).filter(Boolean).join(', ');
  if (typeof val === 'string') return val;
  return '';
}

function parseRecipients(raw: string): string[] {
  return raw
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
}

export function HarkEmailCard({
  title,
  to: propTo,
  cc: propCc,
  subject: propSubject,
  body: propBody,
  threadId: propThreadId,
  promptQuestion,
  approvalId,
  initialData,
  initialApproval,
  mode = 'approval',
  onSend,
}: Props) {
  const effectiveId = approvalId || (initialData?.approval_id ? String(initialData.approval_id) : '');
  const approvalQuery = useApproval(mode === 'approval' && effectiveId ? effectiveId : null);
  const approval = initialApproval ?? approvalQuery.data;
  const decideMutation = useDecideApproval();

  const args =
    approval?.arguments ||
    (initialData?.arguments as Record<string, unknown>) ||
    (initialData?.args as Record<string, unknown>) ||
    {};

  const resolvedTo = propTo !== undefined ? formatRecipients(propTo) : formatRecipients(args.to);
  const resolvedCc = propCc !== undefined ? formatRecipients(propCc) : formatRecipients(args.cc);
  const resolvedSubject = propSubject ?? (typeof args.subject === 'string' ? args.subject : '');
  const resolvedBody = propBody ?? (typeof args.body === 'string' ? args.body : '');
  const resolvedThreadId = propThreadId ?? (typeof args.thread_id === 'string' ? args.thread_id : undefined);

  const [toInput, setToInput] = useState(resolvedTo);
  const [ccInput, setCcInput] = useState(resolvedCc);
  const [showCc, setShowCc] = useState(Boolean(resolvedCc));
  const [subjectInput, setSubjectInput] = useState(resolvedSubject);
  const [bodyInput, setBodyInput] = useState(resolvedBody);
  const [editing, setEditing] = useState(mode === 'approval');
  const [expanded, setExpanded] = useState(false);
  const [steeringOpen, setSteeringOpen] = useState(false);
  const [steeringNote, setSteeringNote] = useState('');
  const [localDecision, setLocalDecision] = useState<'approved' | 'rejected' | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (resolvedTo && !toInput) setToInput(resolvedTo);
    if (resolvedCc && !ccInput) {
      setCcInput(resolvedCc);
      setShowCc(true);
    }
    if (resolvedSubject && !subjectInput) setSubjectInput(resolvedSubject);
    if (resolvedBody && !bodyInput) setBodyInput(resolvedBody);
  }, [resolvedTo, resolvedCc, resolvedSubject, resolvedBody]);

  const status = localDecision || approval?.status || 'pending';
  const isPending = decideMutation.isPending;
  const isReadOnly = mode !== 'approval' || status !== 'pending';

  const applyFormat = (prefix: string, suffix = '', placeholder = 'text') => {
    const el = textareaRef.current;
    if (!el) return;
    const start = el.selectionStart ?? bodyInput.length;
    const end = el.selectionEnd ?? bodyInput.length;
    const selected = bodyInput.slice(start, end) || placeholder;
    const next = bodyInput.slice(0, start) + prefix + selected + suffix + bodyInput.slice(end);
    setBodyInput(next);
    requestAnimationFrame(() => {
      el.focus();
      const cursorPos = start + prefix.length + selected.length + suffix.length;
      el.setSelectionRange(cursorPos, cursorPos);
    });
  };

  const handleSendApproval = () => {
    const toList = parseRecipients(toInput);
    const ccList = parseRecipients(ccInput);
    if (onSend) {
      onSend({ to: toList, cc: ccList, subject: subjectInput, body: bodyInput });
    }
    if (!effectiveId) return;

    const updatedArgs: Record<string, unknown> = {
      ...args,
      to: toList,
      subject: subjectInput,
      body: bodyInput,
      body_html: undefined,
    };
    if (ccList.length > 0) updatedArgs.cc = ccList;
    if (resolvedThreadId) updatedArgs.thread_id = resolvedThreadId;

    setLocalDecision('approved');
    decideMutation.mutate(
      {
        approvalId: effectiveId,
        body: {
          decision: 'approved',
          updated_arguments: updatedArgs,
          decision_note: 'Approved and sent via inline email editor.',
        },
        scope: `approval:email-send:${effectiveId}`,
      },
      { onError: () => setLocalDecision(null) },
    );
  };

  const handleReject = (note: string) => {
    if (!effectiveId) return;
    setLocalDecision('rejected');
    decideMutation.mutate(
      {
        approvalId: effectiveId,
        body: {
          decision: 'rejected',
          decision_note: note.trim() || 'User cancelled sending this email.',
        },
        scope: `approval:email-reject:${effectiveId}`,
      },
      { onError: () => setLocalDecision(null) },
    );
  };

  const headerTitle =
    title ||
    (mode === 'draft'
      ? 'Gmail Draft Saved'
      : mode === 'preview'
        ? 'Email Message'
        : subjectInput
          ? `Send: ${subjectInput}`
          : 'Review & Send Email');

  return (
    <div className={`my-2.5 w-full ${expanded ? 'max-w-4xl' : 'max-w-2xl'}`}>
      <div className="hark-card overflow-hidden rounded-[22px] p-4 sm:p-5">
        {/* Card Header */}
        <div className="flex items-center justify-between gap-2 border-b border-[var(--border)] pb-3">
          <div className="flex min-w-0 items-center gap-2">
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[var(--panel-inset)] text-[var(--text)]">
              <Mail size={14} strokeWidth={1.8} />
            </span>
            <span className="truncate text-[13.5px] font-semibold text-[var(--text)]">
              {headerTitle}
            </span>
          </div>

          <div className="flex shrink-0 items-center gap-1.5">
            {mode === 'approval' && status === 'pending' ? (
              <button
                type="button"
                onClick={() => setEditing((v) => !v)}
                className="inline-flex h-7 items-center gap-1 rounded-full border border-[var(--border)] bg-[var(--panel-inset)] px-2.5 text-[11px] font-medium text-[var(--text-secondary)] transition-colors hover:text-[var(--text)]"
              >
                {editing ? <Eye size={12} /> : <Pencil size={12} />}
                <span>{editing ? 'Rich Preview' : 'Edit'}</span>
              </button>
            ) : null}
            <button
              type="button"
              onClick={() => setExpanded((e) => !e)}
              aria-label={expanded ? 'Collapse email card' : 'Expand email card'}
              className="flex h-7 w-7 items-center justify-center rounded-full text-[var(--text-muted)] transition-colors hover:bg-[var(--panel-inset)] hover:text-[var(--text)]"
            >
              {expanded ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
            </button>
          </div>
        </div>

        {/* Email Headers (Editable when pending) */}
        <div className="mt-3 space-y-2 text-[12.5px]">
          <div className="flex items-center gap-2">
            <span className="w-14 shrink-0 text-[var(--text-muted)]">To:</span>
            {isReadOnly || !editing ? (
              <span className="min-w-0 flex-1 truncate text-[var(--text)]">{toInput || '—'}</span>
            ) : (
              <input
                type="text"
                value={toInput}
                onChange={(e) => setToInput(e.target.value)}
                placeholder="recipient@example.com"
                className="min-w-0 flex-1 rounded-[9px] border border-[var(--border)] bg-[var(--surface-solid)] px-2.5 py-1 text-[12.5px] text-[var(--text)] outline-none focus:border-[var(--text)]"
              />
            )}
            {!isReadOnly && editing && !showCc ? (
              <button
                type="button"
                onClick={() => setShowCc(true)}
                className="shrink-0 text-[11px] font-medium text-[var(--text-muted)] hover:text-[var(--text)]"
              >
                + Cc
              </button>
            ) : null}
          </div>

          {showCc ? (
            <div className="flex items-center gap-2">
              <span className="w-14 shrink-0 text-[var(--text-muted)]">Cc:</span>
              {isReadOnly || !editing ? (
                <span className="min-w-0 flex-1 truncate text-[var(--text)]">{ccInput || '—'}</span>
              ) : (
                <input
                  type="text"
                  value={ccInput}
                  onChange={(e) => setCcInput(e.target.value)}
                  placeholder="cc@example.com"
                  className="min-w-0 flex-1 rounded-[9px] border border-[var(--border)] bg-[var(--surface-solid)] px-2.5 py-1 text-[12.5px] text-[var(--text)] outline-none focus:border-[var(--text)]"
                />
              )}
            </div>
          ) : null}

          <div className="flex items-center gap-2">
            <span className="w-14 shrink-0 text-[var(--text-muted)]">Subject:</span>
            {isReadOnly || !editing ? (
              <span className="min-w-0 flex-1 font-medium text-[var(--text)]">{subjectInput || '—'}</span>
            ) : (
              <input
                type="text"
                value={subjectInput}
                onChange={(e) => setSubjectInput(e.target.value)}
                placeholder="Subject line"
                className="min-w-0 flex-1 rounded-[9px] border border-[var(--border)] bg-[var(--surface-solid)] px-2.5 py-1 text-[12.5px] font-medium text-[var(--text)] outline-none focus:border-[var(--text)]"
              />
            )}
          </div>
        </div>

        {/* Rich Formatting Toolbar + Body */}
        <div className="mt-3.5 overflow-hidden rounded-[14px] border border-[var(--border)] bg-[color-mix(in_srgb,var(--text)_3%,transparent)]">
          {!isReadOnly && editing ? (
            <>
              <div className="flex flex-wrap items-center gap-1 border-b border-[var(--border)] bg-[color-mix(in_srgb,var(--text)_3.5%,transparent)] px-2.5 py-1.5">
                {[
                  { icon: Bold, label: 'Bold', fn: () => applyFormat('**', '**', 'bold text') },
                  { icon: Italic, label: 'Italic', fn: () => applyFormat('*', '*', 'italic text') },
                  { icon: List, label: 'Bullet List', fn: () => applyFormat('\n- ', '', 'list item') },
                  { icon: ListOrdered, label: 'Numbered List', fn: () => applyFormat('\n1. ', '', 'item') },
                  { icon: Quote, label: 'Quote', fn: () => applyFormat('\n> ', '', 'quoted text') },
                  { icon: Link2, label: 'Link', fn: () => applyFormat('[', '](https://example.com)', 'link text') },
                  { icon: Code, label: 'Code', fn: () => applyFormat('`', '`', 'code') },
                ].map(({ icon: Icon, label, fn }) => (
                  <button
                    key={label}
                    type="button"
                    title={label}
                    aria-label={label}
                    onClick={fn}
                    className="flex h-7 w-7 items-center justify-center rounded-[7px] text-[var(--text-muted)] transition-colors hover:bg-[var(--panel-inset)] hover:text-[var(--text)]"
                  >
                    <Icon size={13} strokeWidth={1.8} />
                  </button>
                ))}
              </div>
              <textarea
                ref={textareaRef}
                value={bodyInput}
                onChange={(e) => setBodyInput(e.target.value)}
                rows={expanded ? 12 : 7}
                className="scroll-soft w-full resize-y bg-transparent p-3.5 text-[13px] leading-relaxed text-[var(--text)] outline-none"
                placeholder="Write your email in rich Markdown..."
              />
            </>
          ) : (
            <div className="p-3.5">
              <MarkdownContent content={bodyInput || '_Empty message body_'} />
            </div>
          )}
        </div>

        {/* Steering Input (Request Changes) */}
        {steeringOpen && status === 'pending' && mode === 'approval' ? (
          <div className="mt-3 flex items-center gap-2">
            <input
              type="text"
              value={steeringNote}
              onChange={(e) => setSteeringNote(e.target.value)}
              placeholder="Tell Vigil what to change (e.g. 'Make it shorter and remove Cc')..."
              className="min-w-0 flex-1 rounded-full border border-[var(--border-strong)] bg-[var(--surface-solid)] px-3.5 py-2 text-[12.5px] text-[var(--text)] outline-none focus:border-[var(--text)]"
              onKeyDown={(e) => {
                if (e.key === 'Enter' && steeringNote.trim()) {
                  handleReject(steeringNote);
                }
              }}
            />
            <button
              type="button"
              disabled={isPending || !steeringNote.trim()}
              onClick={() => handleReject(steeringNote)}
              className="h-9 shrink-0 rounded-full bg-[var(--btn-primary-bg)] px-4 text-[12px] font-semibold text-[var(--btn-primary-ink)] disabled:opacity-45"
            >
              Send Feedback
            </button>
          </div>
        ) : null}

        {/* Footer Actions */}
        {mode === 'approval' ? (
          <div className="mt-4 flex flex-wrap items-center justify-between gap-2.5">
            <span className="text-[11.5px] text-[var(--text-muted)]">
              {status === 'approved' ? (
                <span className="inline-flex items-center gap-1 font-medium text-emerald-500">
                  <Check size={13} strokeWidth={2} /> Email sent
                </span>
              ) : status === 'rejected' ? (
                <span className="inline-flex items-center gap-1 font-medium text-rose-500">
                  <X size={13} strokeWidth={2} /> Cancelled / revision requested
                </span>
              ) : (
                'Edit recipients or rich formatting before sending'
              )}
            </span>

            {status === 'pending' ? (
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => handleReject('User cancelled sending this email.')}
                  disabled={isPending || !effectiveId}
                  className="flex h-9 items-center gap-1.5 rounded-full border border-[var(--border-strong)] px-3.5 text-[12px] font-medium text-[var(--text-muted)] transition-colors hover:text-[var(--text)] disabled:opacity-50"
                >
                  <X size={13} strokeWidth={2} />
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={() => setSteeringOpen((v) => !v)}
                  disabled={isPending || !effectiveId}
                  className="flex h-9 items-center gap-1.5 rounded-full border border-[var(--border-strong)] bg-[var(--surface-solid)] px-3.5 text-[12px] font-semibold text-[var(--text)] transition-colors hover:bg-[var(--panel-inset)] disabled:opacity-50"
                >
                  <Pencil size={12} strokeWidth={1.8} />
                  Request Changes
                </button>
                <button
                  type="button"
                  onClick={handleSendApproval}
                  disabled={isPending || !effectiveId || !toInput.trim()}
                  className="flex h-9 items-center gap-1.5 rounded-full bg-[var(--btn-primary-bg)] px-4 text-[12.5px] font-semibold text-[var(--btn-primary-ink)] transition-transform hover:scale-[1.03] active:scale-95 disabled:opacity-50"
                >
                  {isPending && localDecision === 'approved' ? (
                    <Loader2 size={13} className="animate-spin" />
                  ) : (
                    <Send size={13} strokeWidth={2} />
                  )}
                  Send Email
                </button>
              </div>
            ) : null}
          </div>
        ) : null}
      </div>

      {promptQuestion ? (
        <div className="hark-card mt-2.5 rounded-xl p-3 text-[13px] leading-relaxed text-[var(--text-secondary)]">
          {promptQuestion}
        </div>
      ) : null}
    </div>
  );
}

export default HarkEmailCard;
