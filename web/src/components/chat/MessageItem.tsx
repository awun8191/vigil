import { motion, useReducedMotion } from 'framer-motion';
import { AlertCircle } from 'lucide-react';
import type { StreamMessage } from '@/types';
import { Message } from '@/components/ui/c-message-3-utils/message';
import { Bubble, BubbleContent } from '@/components/ui/c-message-3-utils/bubble';

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

/** Overshoot spring: damping low enough that the card settles with a bounce. */
const enterSpring = {
  type: 'spring' as const,
  stiffness: 480,
  damping: 20,
  mass: 0.9,
};

export function MessageItem({ message, divider, staggerIndex = 0 }: Props) {
  const mine = message.role === 'user';
  const time = metaTime(message.created_at);
  const reduced = useReducedMotion();
  const delay = staggerIndex * 0.07;

  /*
   * The vessel and the text enter on separate stages. The row slides up and
   * the bubble expands toward its corner first; only then does the text fade
   * and settle into the vessel while it is still growing. Reduced motion
   * collapses all of it to a plain fade.
   */
  return (
    <motion.div
      initial={reduced ? { opacity: 0 } : { opacity: 0, y: 18 }}
      animate={{ opacity: 1, y: 0 }}
      transition={reduced ? { duration: 0.25 } : { ...enterSpring, delay }}
      className="space-y-1.5"
    >
      {divider ? <Divider label={divider} /> : null}
      <Message align={mine ? 'end' : 'start'}>
        <div className="group flex items-center gap-2">
          {/*
            The send time lives beside its bubble, not under it, and only
            resolves on hover, so the resting thread carries nothing but text.
          */}
          <span
            className={`shrink-0 text-[11px] font-medium tabular-nums text-[var(--text-muted)] opacity-0 transition-all duration-300 ease-spring group-hover:opacity-100 ${
              mine
                ? '-translate-x-1 group-hover:translate-x-0'
                : 'translate-x-1 group-hover:translate-x-0'
            }`}
          >
            {time}
          </span>
          <motion.div
            initial={reduced ? {} : { scale: 0.82 }}
            animate={{ scale: 1 }}
            transition={reduced ? { duration: 0.25 } : { ...enterSpring, delay: delay + 0.04 }}
            className="min-w-0"
            style={{ transformOrigin: mine ? 'bottom right' : 'top left' }}
          >
            <Bubble
              variant={mine ? 'default' : 'muted'}
              className="transition-transform duration-300 ease-spring hover:scale-[1.015]"
            >
              <motion.div
                initial={reduced ? { opacity: 0 } : { opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                transition={
                  reduced
                    ? { duration: 0.4, delay: delay + 0.1 }
                    : { duration: 0.45, delay: delay + 0.2, ease: 'easeOut' }
                }
              >
                <BubbleContent>{message.content}</BubbleContent>
              </motion.div>
            </Bubble>

            {message.local_state === 'failed' && message.failure_reason ? (
              <div className="mt-1 flex items-center gap-1.5 text-[12px] text-[var(--text-muted)]">
                <AlertCircle size={13} strokeWidth={1.75} className="shrink-0" />
                {message.failure_reason}
              </div>
            ) : null}
          </motion.div>
        </div>
      </Message>
    </motion.div>
  );
}

function Divider({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-4 pb-1.5 text-[12px] text-[var(--text-muted)]">
      <span className="h-px flex-1 bg-[color-mix(in_srgb,var(--text)_10%,transparent)]" />
      {label}
      <span className="h-px flex-1 bg-[color-mix(in_srgb,var(--text)_10%,transparent)]" />
    </div>
  );
}

export default MessageItem;
