import { useMemo, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import {
  ChevronDown,
  Sparkles,
  Terminal,
  FileText,
  FileCode2,
  Globe,
  Check,
  XCircle,
  ShieldAlert,
  Archive,
  RotateCw,
  FileDown,
  ListChecks,
  Puzzle,
  HelpCircle,
  Copy,
  Mail,
  Calendar,
} from 'lucide-react';
import type { RunStepEvent } from '@/types';
import { InlineApprovalCard } from './InlineApprovalCard';

interface Props {
  active: boolean;
  steps?: RunStepEvent[];
  summary?: string;
  defaultOpen?: boolean;
}

const spring = { type: 'spring' as const, stiffness: 420, damping: 30 };

interface PairedToolItem {
  kind: 'tool';
  id: string;
  stepNumber: number;
  toolName: string;
  arguments: Record<string, unknown>;
  partition?: string;
  resultStep?: RunStepEvent;
  createdAt: string;
}

interface SingleStepItem {
  kind: 'thought' | 'approval_required' | 'compaction';
  id: string;
  stepNumber: number;
  step: RunStepEvent;
  createdAt: string;
}

type TimelineItem = PairedToolItem | SingleStepItem;

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

/**
 * Pair tool_call events with their corresponding tool_result events by tool_call_id
 * (or fallback to the earliest unpaired tool_call of the same tool_name), and filter
 * out the final answer thought so it isn't duplicated inside the thinking trace.
 */
function buildTimeline(steps: RunStepEvent[]): TimelineItem[] {
  const items: TimelineItem[] = [];
  const toolIndexById = new Map<string, number>();
  const openToolIndicesByName = new Map<string, number[]>();

  for (let i = 0; i < steps.length; i += 1) {
    const step = steps[i];
    if (!step) continue;

    if (
      step.event_type === 'message_delta' ||
      step.event_type === 'run_completed' ||
      step.event_type === 'run_failed'
    ) {
      continue;
    }

    if (step.event_type === 'thought') {
      const content =
        typeof step.payload.content === 'string'
          ? step.payload.content.trim()
          : typeof step.payload.text === 'string'
            ? step.payload.text.trim()
            : '';
      const stage = typeof step.payload.stage === 'string' ? step.payload.stage : '';

      // If this thought has no tool_calls attached and no tool_call follows it,
      // it is the final assistant answer (already rendered in the main message stream).
      const toolCallIds = Array.isArray(step.payload.tool_call_ids)
        ? step.payload.tool_call_ids
        : null;
      const hasSubsequentTool = steps
        .slice(i + 1)
        .some((s) => s.event_type === 'tool_call' || s.event_type === 'tool_result');
      if (toolCallIds && toolCallIds.length === 0 && !hasSubsequentTool) {
        continue;
      }

      if (!content && stage !== 'compact_check') continue;

      // Avoid consecutive identical reasoning lines
      const prev = items[items.length - 1];
      if (prev && prev.kind === 'thought') {
        const prevContent =
          typeof prev.step.payload.content === 'string'
            ? prev.step.payload.content.trim()
            : typeof prev.step.payload.text === 'string'
              ? prev.step.payload.text.trim()
              : '';
        if (prevContent && prevContent === content) continue;
      }

      items.push({
        kind: 'thought',
        id: `thought-${step.step_number}-${i}`,
        stepNumber: step.step_number,
        step,
        createdAt: step.created_at,
      });
      continue;
    }

    if (step.event_type === 'tool_call') {
      const callId =
        typeof step.payload.tool_call_id === 'string'
          ? step.payload.tool_call_id
          : `call-${step.step_number}-${i}`;
      const toolName = String(step.payload.tool_name ?? 'tool');
      const args =
        typeof step.payload.arguments === 'object' && step.payload.arguments !== null
          ? (step.payload.arguments as Record<string, unknown>)
          : {};
      const partition =
        typeof step.payload.partition === 'string' ? step.payload.partition : undefined;

      const idx = items.length;
      items.push({
        kind: 'tool',
        id: callId,
        stepNumber: step.step_number,
        toolName,
        arguments: args,
        partition,
        createdAt: step.created_at,
      });

      toolIndexById.set(callId, idx);
      const list = openToolIndicesByName.get(toolName) ?? [];
      list.push(idx);
      openToolIndicesByName.set(toolName, list);
      continue;
    }

    if (step.event_type === 'tool_result') {
      const callId =
        typeof step.payload.tool_call_id === 'string' ? step.payload.tool_call_id : null;
      const toolName = String(step.payload.tool_name ?? 'tool');

      let targetIdx: number | undefined =
        callId !== null ? toolIndexById.get(callId) : undefined;

      if (targetIdx === undefined) {
        const candidates = openToolIndicesByName.get(toolName);
        if (candidates && candidates.length > 0) {
          targetIdx = candidates.shift();
        }
      } else {
        const candidates = openToolIndicesByName.get(toolName);
        if (candidates) {
          const pos = candidates.indexOf(targetIdx);
          if (pos !== -1) candidates.splice(pos, 1);
        }
      }

      if (targetIdx !== undefined) {
        const existing = items[targetIdx];
        if (existing && existing.kind === 'tool') {
          existing.resultStep = step;
          continue;
        }
      }

      items.push({
        kind: 'tool',
        id: callId ?? `res-${step.step_number}-${i}`,
        stepNumber: step.step_number,
        toolName,
        arguments: {},
        resultStep: step,
        createdAt: step.created_at,
      });
      continue;
    }

    if (step.event_type === 'approval_required' || step.event_type === 'compaction') {
      items.push({
        kind: step.event_type,
        id: `${step.event_type}-${step.step_number}-${i}`,
        stepNumber: step.step_number,
        step,
        createdAt: step.created_at,
      });
    }
  }

  return items;
}

interface ParsedToolOutcome {
  completed: boolean;
  ok: boolean;
  errorText: string | null;
  content: unknown;
  artifactRef: string | null;
  rawResult: unknown;
}

function parseToolOutcome(resultStep?: RunStepEvent): ParsedToolOutcome {
  if (!resultStep) {
    return {
      completed: false,
      ok: true,
      errorText: null,
      content: null,
      artifactRef: null,
      rawResult: null,
    };
  }

  const payload = resultStep.payload;
  const raw = 'result' in payload ? payload.result : payload;

  if (typeof raw === 'object' && raw !== null) {
    const rec = raw as Record<string, unknown>;
    const hasOk = typeof rec.ok === 'boolean';
    const hasError = typeof rec.error === 'string' && rec.error.length > 0;
    const ok = hasOk ? Boolean(rec.ok) : !hasError;
    const errorText = hasError
      ? String(rec.error)
      : typeof rec.message === 'string' && !ok
        ? rec.message
        : null;
    const content = 'content' in rec ? rec.content : raw;
    const artifactRef = typeof rec.artifact_ref === 'string' ? rec.artifact_ref : null;
    return {
      completed: true,
      ok,
      errorText,
      content,
      artifactRef,
      rawResult: raw,
    };
  }

  const str = typeof raw === 'string' ? raw : JSON.stringify(raw);
  const isError = str.toLowerCase().startsWith('error:');
  return {
    completed: true,
    ok: !isError,
    errorText: isError ? str : null,
    content: str,
    artifactRef: null,
    rawResult: raw,
  };
}

function parseBashContent(content: unknown): {
  exitCode: number | null;
  stdout: string;
  stderr: string;
} {
  if (typeof content !== 'string') {
    if (typeof content === 'object' && content !== null) {
      const rec = content as Record<string, unknown>;
      return {
        exitCode: typeof rec.exit_code === 'number' ? rec.exit_code : null,
        stdout: typeof rec.stdout === 'string' ? rec.stdout : JSON.stringify(content, null, 2),
        stderr: typeof rec.stderr === 'string' ? rec.stderr : '',
      };
    }
    return { exitCode: null, stdout: '', stderr: '' };
  }

  const exitMatch = /^exit_code:\s*(-?\d+)\n/m.exec(content);
  const stdoutSplit = content.split('--- stdout ---\n');
  if (stdoutSplit.length > 1) {
    const afterStdout = stdoutSplit[1] ?? '';
    const stderrSplit = afterStdout.split('\n--- stderr ---\n');
    return {
      exitCode: exitMatch ? Number(exitMatch[1]) : null,
      stdout: (stderrSplit[0] ?? '').trimEnd(),
      stderr: (stderrSplit[1] ?? '').trimEnd(),
    };
  }

  return {
    exitCode: exitMatch ? Number(exitMatch[1]) : null,
    stdout: content.trimEnd(),
    stderr: '',
  };
}

/** Quiet, minimal status indicator (spinner when running, subtle check when ok, rose pill on error). */
function StepStateMark({
  completed,
  ok,
  active,
  exitCode,
}: {
  completed: boolean;
  ok: boolean;
  active: boolean;
  exitCode?: number | null;
}) {
  if (!completed) {
    return active ? (
      <RotateCw size={12} strokeWidth={2} className="shrink-0 animate-spin text-[var(--text-muted)]" />
    ) : (
      <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--text-muted)] opacity-50" />
    );
  }
  if (!ok) {
    return (
      <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-rose-500/12 px-2 py-0.5 font-mono text-[10px] font-medium text-rose-400">
        <XCircle size={11} strokeWidth={2} />
        {exitCode !== undefined && exitCode !== null ? `exit ${exitCode}` : 'failed'}
      </span>
    );
  }
  return <Check size={13} strokeWidth={2} className="shrink-0 text-emerald-400/80" />;
}

/* -------------------------------------------------------------------------------------------
   Streamlined Timeline Step Components
   ------------------------------------------------------------------------------------------- */

function ReasoningStep({ step }: { step: RunStepEvent }) {
  const [expanded, setExpanded] = useState(false);
  const { payload } = step;
  const content =
    typeof payload.content === 'string'
      ? payload.content.trim()
      : typeof payload.text === 'string'
        ? payload.text.trim()
        : '';
  const isLong = content.length > 220;

  if (!content) return null;

  return (
    <div className="group/thought relative py-0.5 text-[13px] leading-[1.6] text-[var(--text-muted)]">
      <div
        onClick={isLong ? () => setExpanded((e) => !e) : undefined}
        className={isLong ? 'cursor-pointer select-none' : ''}
      >
        <p
          className={`[overflow-wrap:anywhere] whitespace-pre-wrap ${
            !expanded && isLong ? 'line-clamp-2' : ''
          }`}
        >
          {content}
        </p>
        {isLong ? (
          <button
            type="button"
            className="mt-1 inline-flex items-center gap-1 text-[11px] font-medium text-[var(--text-muted)] hover:text-[var(--text)]"
          >
            <span>{expanded ? 'Show less' : 'Read more'}</span>
            <ChevronDown
              size={11}
              className={`transition-transform duration-200 ${expanded ? 'rotate-180' : ''}`}
            />
          </button>
        ) : null}
      </div>
    </div>
  );
}

function CommandExecutionCard({
  item,
  active,
}: {
  item: PairedToolItem;
  active: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const command = typeof item.arguments.command === 'string' ? item.arguments.command : '';

  const outcome = parseToolOutcome(item.resultStep);
  const parsed = parseBashContent(outcome.content);
  const hasOutput = Boolean(parsed.stdout || parsed.stderr || outcome.errorText);
  const isMultilineOrLong = command.length > 72 || command.includes('\n');
  const canExpand = hasOutput || isMultilineOrLong;

  const handleCopyCommand = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!command) return;
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      // ignore
    }
  };

  return (
    <div className="w-full min-w-0 overflow-hidden rounded-[10px] border border-[var(--border)] bg-[color-mix(in_srgb,var(--text)_3%,transparent)] transition-colors hover:border-[var(--border-strong)]">
      {/* Compact single-line terminal bar */}
      <div
        role={canExpand ? 'button' : undefined}
        tabIndex={canExpand ? 0 : undefined}
        onClick={canExpand ? () => setExpanded((e) => !e) : undefined}
        onKeyDown={
          canExpand
            ? (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  setExpanded((v) => !v);
                }
              }
            : undefined
        }
        className={`flex items-center justify-between gap-3 px-3 py-2 font-mono text-[12px] ${
          canExpand ? 'cursor-pointer select-none' : ''
        }`}
      >
        <div className="flex items-center gap-2 min-w-0 flex-1">
          <span className="select-none font-semibold text-[var(--text-muted)]">$</span>
          <span className="truncate text-[var(--text)]">{command || 'bash'}</span>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          {command ? (
            <button
              type="button"
              onClick={handleCopyCommand}
              aria-label="Copy command"
              className="rounded p-1 text-[var(--text-muted)] opacity-0 transition-opacity hover:bg-[color-mix(in_srgb,var(--text)_8%,transparent)] hover:text-[var(--text)] group-hover:opacity-100 focus:opacity-100 sm:opacity-60"
            >
              {copied ? (
                <Check size={11} className="text-emerald-400" />
              ) : (
                <Copy size={11} strokeWidth={1.5} />
              )}
            </button>
          ) : null}

          <StepStateMark
            completed={outcome.completed}
            ok={outcome.ok}
            active={active}
            exitCode={parsed.exitCode}
          />

          {canExpand ? (
            <ChevronDown
              size={13}
              strokeWidth={1.5}
              className={`text-[var(--text-muted)] transition-transform duration-200 ${
                expanded ? 'rotate-180' : ''
              }`}
            />
          ) : null}
        </div>
      </div>

      {/* Expandable full command & stdout/stderr drawer */}
      <AnimatePresence initial={false}>
        {expanded && canExpand ? (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.18, ease: [0.25, 1, 0.5, 1] }}
            className="overflow-hidden border-t border-[var(--border)] bg-[color-mix(in_srgb,var(--surface-solid)_85%,transparent)]"
          >
            <div className="space-y-2.5 p-3 font-mono text-[11.5px]">
              {isMultilineOrLong ? (
                <div>
                  <div className="mb-1 text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
                    Command
                  </div>
                  <pre className="scroll-soft overflow-x-auto rounded-[7px] bg-[color-mix(in_srgb,var(--text)_4%,transparent)] p-2.5 leading-relaxed text-[var(--text)] whitespace-pre-wrap [overflow-wrap:anywhere]">
                    $ {command}
                  </pre>
                </div>
              ) : null}

              {parsed.stdout ? (
                <div>
                  <div className="mb-1 text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
                    Output
                  </div>
                  <pre className="scroll-soft max-h-52 overflow-auto rounded-[7px] bg-[color-mix(in_srgb,var(--text)_4%,transparent)] p-2.5 leading-relaxed text-[var(--text-muted)]">
                    {parsed.stdout}
                  </pre>
                </div>
              ) : null}

              {parsed.stderr ? (
                <div>
                  <div className="mb-1 text-[10px] uppercase tracking-wider text-rose-400">
                    Stderr
                  </div>
                  <pre className="scroll-soft max-h-40 overflow-auto rounded-[7px] border border-rose-500/20 bg-rose-500/5 p-2.5 leading-relaxed text-rose-300">
                    {parsed.stderr}
                  </pre>
                </div>
              ) : null}

              {!parsed.stdout && !parsed.stderr && outcome.errorText ? (
                <p className="text-rose-400">{outcome.errorText}</p>
              ) : null}
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}

function FileOperationCard({
  item,
  active,
}: {
  item: PairedToolItem;
  active: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const path = typeof item.arguments.path === 'string' ? item.arguments.path : '/workspace';
  const opLabel =
    item.toolName === 'read'
      ? 'Read'
      : item.toolName === 'read_document'
        ? 'Inspected'
        : item.toolName === 'write'
          ? 'Wrote'
          : 'Edited';
  const outcome = parseToolOutcome(item.resultStep);

  const oldString =
    typeof item.arguments.old_string === 'string' ? item.arguments.old_string : null;
  const newString =
    typeof item.arguments.new_string === 'string' ? item.arguments.new_string : null;
  const writeContent =
    typeof item.arguments.content === 'string' ? item.arguments.content : null;

  const previewText =
    typeof outcome.content === 'string'
      ? outcome.content
      : writeContent ?? (outcome.content ? JSON.stringify(outcome.content, null, 2) : '');

  const hasDetails = Boolean(oldString || newString || previewText || outcome.errorText);

  return (
    <div className="w-full min-w-0 overflow-hidden rounded-[10px] border border-[var(--border)] bg-[color-mix(in_srgb,var(--text)_3%,transparent)] transition-colors hover:border-[var(--border-strong)]">
      <button
        type="button"
        disabled={!hasDetails}
        onClick={() => setExpanded((e) => !e)}
        className={`flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-[12.5px] ${
          hasDetails ? 'cursor-pointer' : 'cursor-default'
        }`}
      >
        <div className="flex items-center gap-2 min-w-0">
          <span className="shrink-0 font-medium text-[var(--text-muted)]">{opLabel}</span>
          <span className="truncate font-mono text-[12px] text-[var(--text)]" title={path}>
            {path}
          </span>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          <StepStateMark completed={outcome.completed} ok={outcome.ok} active={active} />
          {hasDetails ? (
            <ChevronDown
              size={13}
              strokeWidth={1.5}
              className={`text-[var(--text-muted)] transition-transform duration-200 ${
                expanded ? 'rotate-180' : ''
              }`}
            />
          ) : null}
        </div>
      </button>

      <AnimatePresence initial={false}>
        {expanded && hasDetails ? (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.18 }}
            className="overflow-hidden border-t border-[var(--border)] bg-[color-mix(in_srgb,var(--surface-solid)_85%,transparent)]"
          >
            <div className="space-y-2 p-3">
              {item.toolName === 'edit' && (oldString !== null || newString !== null) ? (
                <div className="grid gap-1.5 font-mono text-[11px]">
                  {oldString ? (
                    <pre className="scroll-soft max-h-36 overflow-auto rounded-[7px] border border-rose-500/20 bg-rose-500/8 p-2 text-rose-300">
                      - {oldString}
                    </pre>
                  ) : null}
                  {newString ? (
                    <pre className="scroll-soft max-h-36 overflow-auto rounded-[7px] border border-emerald-500/20 bg-emerald-500/8 p-2 text-emerald-300">
                      + {newString}
                    </pre>
                  ) : null}
                </div>
              ) : null}

              {previewText ? (
                <pre className="scroll-soft max-h-48 overflow-auto rounded-[7px] bg-[color-mix(in_srgb,var(--text)_4%,transparent)] p-2.5 font-mono text-[11px] leading-relaxed text-[var(--text-muted)]">
                  {previewText}
                </pre>
              ) : null}
              {outcome.errorText ? (
                <p className="font-mono text-[11px] text-rose-400">{outcome.errorText}</p>
              ) : null}
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}

function SandboxDocumentCard({
  item,
  active,
}: {
  item: PairedToolItem;
  active: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const [copiedContent, setCopiedContent] = useState(false);
  const outcome = parseToolOutcome(item.resultStep);

  const metaObj =
    typeof outcome.content === 'object' && outcome.content !== null
      ? (outcome.content as Record<string, unknown>)
      : {};

  const rawName =
    typeof metaObj.name === 'string'
      ? metaObj.name
      : typeof item.arguments.filename === 'string'
        ? item.arguments.filename
        : typeof item.arguments.name === 'string'
          ? item.arguments.name
          : typeof item.arguments.path === 'string'
            ? item.arguments.path.split('/').pop() ?? 'document'
            : 'document';

  const format =
    typeof metaObj.format === 'string'
      ? metaObj.format
      : typeof item.arguments.format === 'string'
        ? item.arguments.format
        : rawName.split('.').pop() ?? 'file';

  const sizeBytes = typeof metaObj.size_bytes === 'number' ? metaObj.size_bytes : null;
  const hostPath =
    outcome.artifactRef ?? (typeof metaObj.path === 'string' ? metaObj.path : null);
  const docBody = typeof item.arguments.content === 'string' ? item.arguments.content : null;
  const docTitle = typeof item.arguments.title === 'string' ? item.arguments.title : null;

  const handleDownloadClientSide = () => {
    if (!docBody) return;
    const mime =
      format === 'html'
        ? 'text/html'
        : format === 'csv'
          ? 'text/csv'
          : format === 'markdown'
            ? 'text/markdown'
            : 'text/plain';
    const blob = new Blob([docBody], { type: `${mime};charset=utf-8` });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = rawName.includes('.') ? rawName : `${rawName}.${format === 'markdown' ? 'md' : format}`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const handleCopyDoc = async () => {
    const target = docBody ?? hostPath ?? rawName;
    try {
      await navigator.clipboard.writeText(target);
      setCopiedContent(true);
      setTimeout(() => setCopiedContent(false), 1800);
    } catch {
      // ignore
    }
  };

  return (
    <div className="w-full min-w-0 overflow-hidden rounded-[12px] border border-[var(--border-strong)] bg-[color-mix(in_srgb,var(--text)_4%,transparent)] p-3">
      <div className="flex flex-wrap items-center justify-between gap-2.5">
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[8px] border border-[var(--border)] bg-[color-mix(in_srgb,var(--text)_7%,transparent)] text-[var(--text)]">
            <FileDown size={15} strokeWidth={1.6} />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="truncate text-[13px] font-semibold text-[var(--text)]">
                {docTitle ?? rawName}
              </span>
              <span className="shrink-0 rounded bg-[color-mix(in_srgb,var(--text)_8%,transparent)] px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
                {format}
              </span>
            </div>
            <div className="flex items-center gap-2 text-[11px] text-[var(--text-muted)]">
              <span className="truncate font-mono">{rawName}</span>
              {sizeBytes !== null ? (
                <>
                  <span>·</span>
                  <span className="tabular-nums">{formatBytes(sizeBytes)}</span>
                </>
              ) : null}
            </div>
          </div>
        </div>

        <div className="flex items-center gap-1.5 shrink-0">
          <StepStateMark completed={outcome.completed} ok={outcome.ok} active={active} />
          {docBody ? (
            <button
              type="button"
              onClick={handleDownloadClientSide}
              className="inline-flex cursor-pointer items-center gap-1 rounded-[7px] border border-[var(--border)] bg-[color-mix(in_srgb,var(--text)_7%,transparent)] px-2.5 py-1 text-[11px] font-medium text-[var(--text)] transition-colors hover:bg-[color-mix(in_srgb,var(--text)_12%,transparent)]"
            >
              <FileDown size={12} strokeWidth={1.75} />
              <span>Save</span>
            </button>
          ) : null}
          <button
            type="button"
            onClick={handleCopyDoc}
            className="inline-flex cursor-pointer items-center gap-1 rounded-[7px] px-2 py-1 text-[11px] font-medium text-[var(--text-muted)] transition-colors hover:bg-[color-mix(in_srgb,var(--text)_8%,transparent)] hover:text-[var(--text)]"
          >
            {copiedContent ? (
              <>
                <Check size={12} className="text-emerald-400" />
                <span className="text-emerald-400">Copied</span>
              </>
            ) : (
              <>
                <Copy size={12} strokeWidth={1.5} />
                <span>{docBody ? 'Copy' : 'Path'}</span>
              </>
            )}
          </button>
          {docBody ? (
            <button
              type="button"
              onClick={() => setExpanded((e) => !e)}
              className="inline-flex cursor-pointer items-center gap-1 rounded-[7px] px-2 py-1 text-[11px] font-medium text-[var(--text-muted)] transition-colors hover:bg-[color-mix(in_srgb,var(--text)_8%,transparent)] hover:text-[var(--text)]"
            >
              <ChevronDown
                size={13}
                className={`transition-transform duration-200 ${expanded ? 'rotate-180' : ''}`}
              />
            </button>
          ) : null}
        </div>
      </div>

      {expanded && docBody ? (
        <div className="mt-2.5 border-t border-[var(--border)] pt-2.5">
          <pre className="scroll-soft max-h-56 overflow-auto rounded-[8px] bg-[color-mix(in_srgb,var(--text)_4%,transparent)] p-3 font-mono text-[11.5px] leading-relaxed text-[var(--text)] whitespace-pre-wrap">
            {docBody}
          </pre>
        </div>
      ) : null}
    </div>
  );
}

function WebSearchCard({
  item,
  active,
}: {
  item: PairedToolItem;
  active: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const outcome = parseToolOutcome(item.resultStep);
  const query =
    typeof item.arguments.query === 'string'
      ? item.arguments.query
      : typeof item.arguments.q === 'string'
        ? item.arguments.q
        : null;
  const urls = Array.isArray(item.arguments.urls)
    ? item.arguments.urls.map(String)
    : typeof item.arguments.url === 'string'
      ? [item.arguments.url]
      : [];

  const outputText =
    typeof outcome.content === 'string'
      ? outcome.content
      : outcome.content
        ? JSON.stringify(outcome.content, null, 2)
        : '';

  return (
    <div className="w-full min-w-0 overflow-hidden rounded-[10px] border border-[var(--border)] bg-[color-mix(in_srgb,var(--text)_3%,transparent)] transition-colors hover:border-[var(--border-strong)]">
      <button
        type="button"
        disabled={!outputText}
        onClick={() => setExpanded((e) => !e)}
        className={`flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-[12.5px] ${
          outputText ? 'cursor-pointer' : 'cursor-default'
        }`}
      >
        <div className="flex items-center gap-2 min-w-0">
          <span className="shrink-0 font-medium text-[var(--text-muted)]">
            {item.toolName.includes('fetch') || item.toolName.includes('contents')
              ? 'Fetched'
              : 'Searched'}
          </span>
          {query ? (
            <span className="truncate text-[var(--text)]" title={query}>
              “{query}”
            </span>
          ) : urls.length > 0 ? (
            <span className="truncate font-mono text-[11.5px] text-[var(--text)]">
              {urls[0]} {urls.length > 1 ? `(+${urls.length - 1})` : ''}
            </span>
          ) : null}
        </div>

        <div className="flex items-center gap-2 shrink-0">
          <StepStateMark completed={outcome.completed} ok={outcome.ok} active={active} />
          {outputText ? (
            <ChevronDown
              size={13}
              strokeWidth={1.5}
              className={`text-[var(--text-muted)] transition-transform duration-200 ${
                expanded ? 'rotate-180' : ''
              }`}
            />
          ) : null}
        </div>
      </button>

      {expanded && outputText ? (
        <div className="border-t border-[var(--border)] bg-[color-mix(in_srgb,var(--surface-solid)_85%,transparent)] p-3">
          <pre className="scroll-soft max-h-52 overflow-auto rounded-[7px] bg-[color-mix(in_srgb,var(--text)_4%,transparent)] p-2.5 font-mono text-[11px] leading-relaxed text-[var(--text-muted)] whitespace-pre-wrap [overflow-wrap:anywhere]">
            {outputText}
          </pre>
        </div>
      ) : null}
    </div>
  );
}

function GenericToolCard({
  item,
  active,
}: {
  item: PairedToolItem;
  active: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const outcome = parseToolOutcome(item.resultStep);

  const label =
    item.toolName === 'skill_load'
      ? `Loaded skill ${String(item.arguments.name ?? '')}`
      : item.toolName === 'manage_tasks'
        ? `Tasks (${String(item.arguments.operation ?? 'update')})`
        : item.toolName === 'ask_user'
          ? `Asked: ${String(item.arguments.question ?? '')}`
          : item.toolName.replace(/_/g, ' ');

  const hasArgs = Object.keys(item.arguments).length > 0;
  const hasResult = outcome.completed && outcome.rawResult !== null;
  const canExpand = hasArgs || hasResult;

  return (
    <div className="w-full min-w-0 overflow-hidden rounded-[10px] border border-[var(--border)] bg-[color-mix(in_srgb,var(--text)_3%,transparent)] transition-colors hover:border-[var(--border-strong)]">
      <button
        type="button"
        disabled={!canExpand}
        onClick={() => setExpanded((e) => !e)}
        className={`flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-[12.5px] ${
          canExpand ? 'cursor-pointer' : 'cursor-default'
        }`}
      >
        <span className="truncate font-medium text-[var(--text)]">{label}</span>

        <div className="flex items-center gap-2 shrink-0">
          <StepStateMark completed={outcome.completed} ok={outcome.ok} active={active} />
          {canExpand ? (
            <ChevronDown
              size={13}
              strokeWidth={1.5}
              className={`text-[var(--text-muted)] transition-transform duration-200 ${
                expanded ? 'rotate-180' : ''
              }`}
            />
          ) : null}
        </div>
      </button>

      {expanded && canExpand ? (
        <div className="space-y-2 border-t border-[var(--border)] bg-[color-mix(in_srgb,var(--surface-solid)_85%,transparent)] p-3 font-mono text-[11px]">
          {hasArgs ? (
            <pre className="scroll-soft max-h-40 overflow-auto rounded-[7px] bg-[color-mix(in_srgb,var(--text)_4%,transparent)] p-2 text-[var(--text-muted)]">
              {JSON.stringify(item.arguments, null, 2)}
            </pre>
          ) : null}
          {hasResult ? (
            <pre className="scroll-soft max-h-44 overflow-auto rounded-[7px] bg-[color-mix(in_srgb,var(--text)_4%,transparent)] p-2 text-[var(--text)]">
              {typeof outcome.content === 'string'
                ? outcome.content
                : JSON.stringify(outcome.rawResult, null, 2)}
            </pre>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function getTimelineNodeIcon(item: TimelineItem) {
  if (item.kind === 'thought') {
    return <Sparkles size={11} strokeWidth={1.75} className="text-[var(--text-muted)]" />;
  }
  if (item.kind === 'approval_required') {
    return <ShieldAlert size={11} strokeWidth={1.75} className="text-amber-400" />;
  }
  if (item.kind !== 'tool') {
    return <Archive size={11} strokeWidth={1.75} className="text-[var(--text-muted)]" />;
  }
  const { toolName } = item;
  if (toolName === 'bash') {
    return <Terminal size={11} strokeWidth={1.75} className="text-[var(--text-muted)]" />;
  }
  if (
    toolName === 'read' ||
    toolName === 'read_document' ||
    toolName === 'write' ||
    toolName === 'edit'
  ) {
    return <FileCode2 size={11} strokeWidth={1.75} className="text-[var(--text-muted)]" />;
  }
  if (toolName === 'deliver_file' || toolName === 'generate_document') {
    return <FileDown size={11} strokeWidth={1.75} className="text-[var(--text)]" />;
  }
  if (
    toolName === 'web_search' ||
    toolName === 'web_fetch' ||
    toolName.startsWith('exa_') ||
    toolName.includes('search')
  ) {
    return <Globe size={11} strokeWidth={1.75} className="text-[var(--text-muted)]" />;
  }
  if (toolName === 'skill_load') {
    return <Puzzle size={11} strokeWidth={1.75} className="text-[var(--text-muted)]" />;
  }
  if (toolName === 'manage_tasks') {
    return <ListChecks size={11} strokeWidth={1.75} className="text-[var(--text-muted)]" />;
  }
  if (toolName === 'ask_user') {
    return <HelpCircle size={11} strokeWidth={1.75} className="text-amber-400" />;
  }
  if (toolName.startsWith('gmail')) {
    return <Mail size={11} strokeWidth={1.75} className="text-[var(--text-muted)]" />;
  }
  if (toolName.startsWith('calendar')) {
    return <Calendar size={11} strokeWidth={1.75} className="text-[var(--text-muted)]" />;
  }
  return <FileText size={11} strokeWidth={1.75} className="text-[var(--text-muted)]" />;
}

function renderTimelineItem(item: TimelineItem, active: boolean) {
  if (item.kind === 'thought') {
    return <ReasoningStep step={item.step} />;
  }

  if (item.kind === 'approval_required') {
    const payload = (item.step.payload ?? {}) as Record<string, unknown>;
    const approvalId = payload.approval_id ? String(payload.approval_id) : undefined;
    return <InlineApprovalCard approvalId={approvalId} initialData={payload} />;
  }

  if (item.kind !== 'tool') {
    return (
      <div className="py-0.5 text-[12px] text-[var(--text-muted)]">
        Context history compacted
      </div>
    );
  }

  const { toolName } = item;
  if (toolName === 'bash') {
    return <CommandExecutionCard item={item} active={active} />;
  }
  if (
    toolName === 'read' ||
    toolName === 'read_document' ||
    toolName === 'write' ||
    toolName === 'edit'
  ) {
    return <FileOperationCard item={item} active={active} />;
  }
  if (toolName === 'deliver_file' || toolName === 'generate_document') {
    return <SandboxDocumentCard item={item} active={active} />;
  }
  if (
    toolName === 'web_search' ||
    toolName === 'web_fetch' ||
    toolName.startsWith('exa_') ||
    toolName.includes('search')
  ) {
    return <WebSearchCard item={item} active={active} />;
  }

  return <GenericToolCard item={item} active={active} />;
}

export function ThinkingCapsule({ active, steps = [], summary, defaultOpen }: Props) {
  const items = useMemo(() => buildTimeline(steps), [steps]);
  const [open, setOpen] = useState<boolean>(defaultOpen ?? active);

  if (!active && items.length === 0) return null;

  const toolCount = items.filter((i) => i.kind === 'tool').length;

  const latestItem = items[items.length - 1];

  let displaySummary = summary;
  if (!displaySummary) {
    if (active) {
      if (latestItem?.kind === 'tool' && !latestItem.resultStep) {
        if (latestItem.toolName === 'bash') {
          const cmd =
            typeof latestItem.arguments.command === 'string'
              ? latestItem.arguments.command.slice(0, 42)
              : 'command';
          displaySummary = `Running $ ${cmd}${cmd.length >= 42 ? '…' : ''}`;
        } else if (
          latestItem.toolName === 'deliver_file' ||
          latestItem.toolName === 'generate_document'
        ) {
          displaySummary = 'Preparing document...';
        } else {
          displaySummary = `Running ${latestItem.toolName.replace(/_/g, ' ')}...`;
        }
      } else {
        displaySummary =
          items.length > 0 ? `Working through ${items.length} steps...` : 'Thinking...';
      }
    } else {
      displaySummary =
        toolCount > 0
          ? `Executed ${toolCount} ${toolCount === 1 ? 'action' : 'actions'}`
          : 'Thought process';
    }
  }

  return (
    <div className="my-1.5 w-full max-w-2xl min-w-0" role="status" aria-live="polite">
      {/* Clean, low-profile toggle pill */}
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-label={active ? 'Vigil is thinking' : displaySummary}
        className="group inline-flex max-w-full cursor-pointer items-center gap-2 rounded-full border border-[var(--border)] bg-[color-mix(in_srgb,var(--text)_3.5%,transparent)] px-3 py-1.5 text-left text-[12.5px] font-medium text-[var(--text-muted)] transition-colors hover:border-[var(--border-strong)] hover:text-[var(--text)]"
      >
        {active ? (
          <span className="thinking-dots shrink-0 text-[var(--text)]" aria-hidden="true">
            <span />
            <span />
            <span />
          </span>
        ) : (
          <Sparkles size={12} strokeWidth={1.75} className="shrink-0 text-[var(--text-muted)] transition-colors group-hover:text-[var(--text)]" />
        )}

        {active && items.length === 0 ? null : (
          <span className="truncate">{displaySummary}</span>
        )}

        {items.length > 0 ? (
          <motion.span
            animate={{ rotate: open ? 180 : 0 }}
            transition={spring}
            className="flex shrink-0 items-center justify-center text-[var(--text-muted)]"
          >
            <ChevronDown size={13} strokeWidth={1.75} />
          </motion.span>
        ) : null}
      </button>

      {/* Clean Left-Rail Timeline (no heavy outer box) */}
      <AnimatePresence initial={false}>
        {open && items.length > 0 ? (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.22, ease: [0.25, 1, 0.5, 1] }}
            className="overflow-hidden"
          >
            <ol className="relative mt-2.5 ml-3 space-y-2.5 border-l border-[var(--border)] pl-4">
              {items.map((item) => (
                <li key={item.id} className="relative min-w-0">
                  {/* Timeline Node Icon sitting cleanly on the left rail */}
                  <span
                    aria-hidden="true"
                    className="absolute -left-[25px] top-1.5 flex h-[18px] w-[18px] items-center justify-center rounded-full border border-[var(--border)] bg-[var(--surface-solid)]"
                  >
                    {getTimelineNodeIcon(item)}
                  </span>

                  {renderTimelineItem(item, active)}
                </li>
              ))}
            </ol>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}

export default ThinkingCapsule;
