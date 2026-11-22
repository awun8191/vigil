import { useEffect, useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { motion, useReducedMotion } from 'framer-motion';
import {
  AlertCircle,
  Check,
  Clock,
  Copy,
  Download,
  FileCode2,
  FileSpreadsheet,
  FileText,
  Image as ImageIcon,
  Paperclip,
} from 'lucide-react';
import { api } from '@/lib/api';
import type { Artifact, RunAttachmentItem, StreamMessage } from '@/types';
import { Bubble, BubbleContent } from '@/components/ui/c-message-3-utils/bubble';
import { MarkdownContent } from './MarkdownContent';
import { ThinkingCapsule } from './ThinkingCapsule';
import { ArtifactCard, buildDeliverables } from './ArtifactCard';
import { LiveBrowserCard, buildBrowserPreviews } from './LiveBrowserCard';
import { runArtifactsKey, useRunArtifacts, useRunSteps } from '@/queries/runs';

interface Props {
  message: StreamMessage;
  /** Set when this message opens a new day, rendered as a labelled divider. */
  divider?: string;
  /**
   * 0 keeps the entry instant; N staggers the pop, so a thread opened from
   * cold bounces in sequence instead of landing as one slab.
   */
  staggerIndex?: number;
}

const metaTime = (iso: string): string =>
  new Date(iso).toLocaleTimeString(undefined, {
    hour: 'numeric',
    minute: '2-digit',
  });

const enterSpring = {
  type: 'spring' as const,
  stiffness: 420,
  damping: 26,
  mass: 0.9,
};

const ATTACHED_BLOCK_MARKER = '[Attached Files in /workspace]';
const DEFAULT_ATTACHMENT_PROMPT = 'Please inspect the attached file(s) and help me with them.';

interface UserAttachmentView {
  name: string;
  sizeBytes: number;
  contentType: string;
  artifactId: string | null;
}

function formatAttachmentBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function parseUserMessageContent(rawContent: string): {
  cleanText: string;
  parsedAttachments: RunAttachmentItem[];
} {
  const markerIdx = rawContent.indexOf(ATTACHED_BLOCK_MARKER);
  if (markerIdx === -1) {
    return { cleanText: rawContent, parsedAttachments: [] };
  }
  const head = rawContent.slice(0, markerIdx).trim();
  const cleanText = head === DEFAULT_ATTACHMENT_PROMPT ? '' : head;
  const tail = rawContent.slice(markerIdx + ATTACHED_BLOCK_MARKER.length);
  const parsedAttachments: RunAttachmentItem[] = [];
  const lineRe = /^-\s+`([^`]+)`\s+\(path:\s+`([^`]+)`,\s+type:\s+`([^`]+)`,\s+size:\s+(\d+)\s+B\)/;
  for (const line of tail.split(/\r?\n/)) {
    const trimmed = line.trim();
    const match = lineRe.exec(trimmed);
    if (match) {
      parsedAttachments.push({
        name: match[1] ?? 'file',
        workspace_path: match[2] ?? '/workspace/file',
        content_type: match[3] ?? 'application/octet-stream',
        size_bytes: Number(match[4] ?? 0),
      });
    }
  }
  return { cleanText, parsedAttachments };
}

function AttachmentFormatIcon({ name, contentType }: { name: string; contentType: string }) {
  const ext = name.includes('.') ? (name.split('.').pop()?.toLowerCase() ?? '') : '';
  if (contentType.startsWith('image/') || ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg'].includes(ext)) {
    return <ImageIcon size={13} strokeWidth={1.65} className="shrink-0 text-amber-400" />;
  }
  if (['xlsx', 'xlsm', 'csv', 'tsv'].includes(ext)) {
    return <FileSpreadsheet size={13} strokeWidth={1.65} className="shrink-0 text-emerald-400" />;
  }
  if (['html', 'htm', 'json', 'yaml', 'yml', 'py', 'ts', 'tsx', 'js', 'sh', 'sql'].includes(ext)) {
    return <FileCode2 size={13} strokeWidth={1.65} className="shrink-0 text-[var(--text-muted)]" />;
  }
  if (['pdf', 'docx', 'pptx', 'md', 'txt'].includes(ext)) {
    return <FileText size={13} strokeWidth={1.65} className="shrink-0 text-rose-400" />;
  }
  return <Paperclip size={13} strokeWidth={1.65} className="shrink-0 text-[var(--text-muted)]" />;
}

/** Inline 4-point Vigil spark SVG mark (zero emoji, crisp at 14px). */
export function VigilSpark({ className = '' }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 16 16"
      fill="currentColor"
      aria-hidden="true"
      className={`h-3.5 w-3.5 shrink-0 ${className}`}
    >
      <path d="M8 1C8.55 4.6 11.4 7.45 15 8C11.4 8.55 8.55 11.4 8 15C7.45 11.4 4.6 8.55 1 8C4.6 7.45 7.45 4.6 8 1Z" />
    </svg>
  );
}

export function MessageItem({ message, divider, staggerIndex = 0 }: Props) {
  const mine = message.role === 'user';
  const time = metaTime(message.created_at);
  const reduced = useReducedMotion();
  const delay = staggerIndex * 0.05;

  const [copied, setCopied] = useState(false);

  const runStepsQuery = useRunSteps(!mine && message.run_id ? message.run_id : null);
  const runArtifactsQuery = useRunArtifacts(message.run_id ? message.run_id : null);
  const queryClient = useQueryClient();

  // While a run streams, new deliver_file/generate_document results land in
  // steps before the artifacts row is queryable. Refetch once per new step so
  // the step-only fallback (artifactId null) upgrades to the saved row without
  // a manual refresh. Old runs hit this at most once: their step count is stable.
  const stepCount = runStepsQuery.data?.length ?? 0;
  useEffect(() => {
    if (!message.run_id || stepCount === 0) return;
    if (runArtifactsQuery.isFetching) return;
    const hasUnlinkedDeliverable = (runStepsQuery.data ?? []).some((step) => {
      if (step.event_type !== 'tool_result') return false;
      const toolName = String(step.payload?.tool_name ?? '');
      if (toolName !== 'deliver_file' && toolName !== 'generate_document') return false;
      const result = step.payload?.result;
      if (typeof result !== 'object' || result === null) return false;
      const res = result as Record<string, unknown>;
      if (res.ok !== true) return false;
      const linkedId =
        typeof res.artifact_id === 'string'
          ? res.artifact_id
          : typeof res.content === 'object' && res.content !== null
            ? (res.content as Record<string, unknown>).artifact_id
            : undefined;
      if (typeof linkedId === 'string' && linkedId.length > 0) {
        return !(runArtifactsQuery.data ?? []).some((a) => a.id === linkedId);
      }
      const path =
        typeof res.artifact_ref === 'string'
          ? res.artifact_ref
          : typeof res.content === 'object' && res.content !== null &&
              typeof (res.content as Record<string, unknown>).path === 'string'
            ? String((res.content as Record<string, unknown>).path)
            : '';
      if (!path) return false;
      return !(runArtifactsQuery.data ?? []).some((a) => a.path === path);
    });
    if (hasUnlinkedDeliverable) {
      void queryClient.invalidateQueries({ queryKey: runArtifactsKey(message.run_id) });
    }
  }, [message.run_id, stepCount, runArtifactsQuery.data, runArtifactsQuery.isFetching, queryClient]);

  const mappedSteps = (runStepsQuery.data ?? [])
    .filter((step) => step.event_type !== 'message_delta')
    .map((step) => ({
      run_id: step.run_id,
      step_number: step.step_number,
      event_type: step.event_type,
      payload: step.payload,
      created_at: step.created_at,
    }));

  const deliverables = !mine && message.run_id
    ? buildDeliverables(runArtifactsQuery.data ?? [], mappedSteps)
    : [];
  const browserPreviews = !mine && message.run_id
    ? buildBrowserPreviews(mappedSteps, runArtifactsQuery.data ?? [])
    : [];

  const { cleanText: userDisplayText, parsedAttachments } = useMemo(
    () =>
      mine
        ? parseUserMessageContent(message.content)
        : { cleanText: message.content, parsedAttachments: [] },
    [mine, message.content],
  );

  const userAttachments: UserAttachmentView[] = useMemo(() => {
    if (!mine) return [];
    const artifactAttachments: Artifact[] = (runArtifactsQuery.data ?? []).filter(
      (a) => a.kind === 'attachment',
    );
    const artifactByName = new Map<string, Artifact>();
    for (const art of artifactAttachments) {
      const base = art.path.split('/').pop() ?? art.path;
      artifactByName.set(base, art);
    }

    const sourceList =
      message.attachments && message.attachments.length > 0
        ? message.attachments
        : parsedAttachments;

    if (sourceList.length > 0) {
      return sourceList.map((item) => {
        const linked = artifactByName.get(item.name);
        return {
          name: item.name,
          sizeBytes: item.size_bytes || linked?.size_bytes || 0,
          contentType: item.content_type || 'application/octet-stream',
          artifactId: linked?.id ?? null,
        };
      });
    }

    return artifactAttachments.map((art) => {
      const base = art.path.split('/').pop() ?? art.path;
      return {
        name: base,
        sizeBytes: art.size_bytes,
        contentType: 'application/octet-stream',
        artifactId: art.id,
      };
    });
  }, [mine, message.attachments, parsedAttachments, runArtifactsQuery.data]);

  const handleDownloadAttachment = async (att: UserAttachmentView) => {
    if (!att.artifactId) return;
    try {
      const blob = await api.downloadArtifactBlob(att.artifactId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = att.name;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(url), 1500);
    } catch {
      // ignore download error
    }
  };

  const hasSemanticSteps = mappedSteps.some(
    (step) => step.event_type !== 'run_completed' && step.event_type !== 'run_failed',
  );
  const hasApproval = mappedSteps.some((step) => step.event_type === 'approval_required');

  const handleCopy = async () => {
    const textToCopy = mine ? userDisplayText || message.content : message.content;
    try {
      await navigator.clipboard.writeText(textToCopy);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      const textarea = document.createElement('textarea');
      textarea.value = textToCopy;
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand('copy');
      document.body.removeChild(textarea);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <motion.div
      initial={reduced ? { opacity: 0 } : { opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={reduced ? { duration: 0.2 } : { ...enterSpring, delay }}
      className="w-full min-w-0"
    >
      {divider ? <Divider label={divider} /> : null}

      {mine ? (
        <div className="flex w-full min-w-0 justify-end">
          <div className="group flex min-w-0 max-w-[88%] flex-col items-end gap-1.5 sm:max-w-[76%]">
            {userAttachments.length > 0 ? (
              <div className="flex flex-wrap justify-end gap-1.5">
                {userAttachments.map((att) => {
                  const sizeLabel = formatAttachmentBytes(att.sizeBytes);
                  return (
                    <div
                      key={`${att.name}-${att.sizeBytes}`}
                      className="inline-flex items-center gap-2 rounded-[12px] border border-[var(--border)] bg-[var(--card)] px-3 py-1.5 text-[12px] text-[var(--text)] shadow-2xs"
                    >
                      <AttachmentFormatIcon name={att.name} contentType={att.contentType} />
                      <span className="max-w-[200px] truncate font-mono font-medium">
                        {att.name}
                      </span>
                      {sizeLabel ? (
                        <span className="font-mono text-[10.5px] text-[var(--text-muted)]">
                          {sizeLabel}
                        </span>
                      ) : null}
                      {att.artifactId ? (
                        <button
                          type="button"
                          onClick={() => void handleDownloadAttachment(att)}
                          aria-label={`Download ${att.name}`}
                          title={`Download ${att.name}`}
                          className="ml-0.5 inline-flex h-5 w-5 cursor-pointer items-center justify-center rounded-full text-[var(--text-muted)] transition-colors hover:bg-[var(--panel-inset)] hover:text-[var(--text)]"
                        >
                          <Download size={11} strokeWidth={1.75} />
                        </button>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            ) : null}

            {userDisplayText ? (
              <motion.div
                initial={reduced ? {} : { scale: 0.96 }}
                animate={{ scale: 1 }}
                transition={reduced ? { duration: 0.2 } : { ...enterSpring, delay: delay + 0.03 }}
                className="min-w-0 max-w-full"
                style={{ transformOrigin: 'bottom right' }}
              >
                <Bubble variant="user">
                  <BubbleContent>
                    <span className="block whitespace-pre-wrap [overflow-wrap:anywhere]">
                      {userDisplayText}
                    </span>
                  </BubbleContent>
                </Bubble>
              </motion.div>
            ) : null}

            {message.local_state === 'failed' && message.failure_reason ? (
              <div className="mt-0.5 flex items-center gap-1.5 text-[12px] text-rose-400">
                <AlertCircle size={13} strokeWidth={1.75} className="shrink-0" />
                <span className="[overflow-wrap:anywhere]">{message.failure_reason}</span>
              </div>
            ) : null}

            <MessageMeta
              time={time}
              copied={copied}
              onCopy={handleCopy}
              sending={message.local_state === 'sending'}
              align="end"
            />
          </div>
        </div>
      ) : (
        <div className="flex w-full min-w-0 justify-start">
          <div className="group flex w-full min-w-0 max-w-[88%] flex-col items-start gap-1.5 sm:max-w-[78%]">
            {message.run_id && hasSemanticSteps ? (
              <div className="w-full min-w-0">
                <ThinkingCapsule
                  active={runStepsQuery.isLoading}
                  steps={mappedSteps}
                  defaultOpen={hasApproval}
                />
              </div>
            ) : null}

            <Bubble variant="assistant" className="w-fit max-w-full">
              <MarkdownContent content={message.content} />
            </Bubble>

            {browserPreviews.length > 0 ? (
              <div className="w-full min-w-0 space-y-2 pt-1">
                {browserPreviews.map((preview) => (
                  <LiveBrowserCard key={preview.id} preview={preview} />
                ))}
              </div>
            ) : null}

            {deliverables.length > 0 ? (
              <div className="w-full min-w-0 space-y-2 pt-1">
                {deliverables.map((item) => (
                  <ArtifactCard key={item.id} item={item} />
                ))}
              </div>
            ) : null}

            <MessageMeta
              time={time}
              copied={copied}
              onCopy={handleCopy}
              align="start"
            />
          </div>
        </div>
      )}
    </motion.div>
  );
}

function MessageMeta({
  time,
  copied,
  onCopy,
  sending = false,
  align,
}: {
  time: string;
  copied: boolean;
  onCopy: () => void;
  sending?: boolean;
  align: 'start' | 'end';
}) {
  return (
    <div
      className={`flex items-center gap-2 px-1 text-[11px] text-[var(--text-muted)] opacity-0 transition-opacity duration-200 group-hover:opacity-100 focus-within:opacity-100 ${
        align === 'end' ? 'justify-end' : 'justify-start'
      }`}
    >
      {sending ? (
        <span className="inline-flex items-center gap-1">
          <Clock size={11} strokeWidth={1.5} />
          Sending
        </span>
      ) : (
        <span className="tabular-nums">{time}</span>
      )}
      <span aria-hidden="true">·</span>
      <button
        type="button"
        onClick={onCopy}
        aria-label={copied ? 'Copied message' : 'Copy message'}
        className="inline-flex cursor-pointer items-center gap-1 rounded-full px-1.5 py-0.5 font-medium text-[var(--text-muted)] transition-colors hover:bg-[var(--panel-inset)] hover:text-[var(--text)]"
      >
        {copied ? (
          <>
            <Check size={11} strokeWidth={2} className="text-emerald-500" />
            <span className="text-emerald-500">Copied</span>
          </>
        ) : (
          <>
            <Copy size={11} strokeWidth={1.5} />
            <span>Copy</span>
          </>
        )}
      </button>
    </div>
  );
}

function Divider({ label }: { label: string }) {
  return (
    <div className="mb-5 flex items-center gap-3 text-[10.5px] font-medium uppercase tracking-[0.1em] text-[var(--text-muted)]">
      <span className="h-px flex-1 bg-[var(--border)]" />
      <span className="rounded-full bg-[var(--panel-inset)] px-2.5 py-1">{label}</span>
      <span className="h-px flex-1 bg-[var(--border)]" />
    </div>
  );
}

export default MessageItem;
