import { useState } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { AlertCircle, Check, Clock, Copy } from 'lucide-react';
import type { StreamMessage } from '@/types';
import { Bubble, BubbleContent } from '@/components/ui/c-message-3-utils/bubble';
import { MarkdownContent } from './MarkdownContent';
import { ThinkingCapsule } from './ThinkingCapsule';
import { ArtifactCard, buildDeliverables } from './ArtifactCard';
import { LiveBrowserCard, buildBrowserPreviews } from './LiveBrowserCard';
import { useRunArtifacts, useRunSteps } from '@/queries/runs';

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

  const runStepsQuery = useRunSteps(message.run_id ? message.run_id : null);
  const runArtifactsQuery = useRunArtifacts(message.run_id ? message.run_id : null);
  const mappedSteps = (runStepsQuery.data ?? [])
    .filter((step) => step.event_type !== 'message_delta')
    .map((step) => ({
      run_id: step.run_id,
      step_number: step.step_number,
      event_type: step.event_type,
      payload: step.payload,
      created_at: step.created_at,
    }));

  const deliverables = message.run_id
    ? buildDeliverables(runArtifactsQuery.data ?? [], mappedSteps)
    : [];
  const browserPreviews = message.run_id
    ? buildBrowserPreviews(mappedSteps, runArtifactsQuery.data ?? [])
    : [];

  const hasSemanticSteps = mappedSteps.some(
    (step) => step.event_type !== 'run_completed' && step.event_type !== 'run_failed',
  );
  const hasApproval = mappedSteps.some((step) => step.event_type === 'approval_required');

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      const textarea = document.createElement('textarea');
      textarea.value = message.content;
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
          <div className="group flex min-w-0 max-w-[88%] flex-col items-end gap-1 sm:max-w-[76%]">
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
                    {message.content}
                  </span>
                </BubbleContent>
              </Bubble>
            </motion.div>

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
