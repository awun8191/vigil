import { motion } from 'framer-motion';
import { SparkMark } from '../layout/SparkMark';
import type { ChatMessage } from '@/types';

const enter = {
  initial: { opacity: 0, y: 12 },
  animate: { opacity: 1, y: 0 },
  transition: { type: 'spring' as const, stiffness: 400, damping: 30 },
};

interface Props {
  message: ChatMessage;
  /** Set when this message opens a new day, rendered as a labelled divider. */
  divider?: string;
}

export function MessageItem({ message, divider }: Props) {
  const time = new Date(message.created_at).toLocaleTimeString(undefined, {
    hour: 'numeric',
    minute: '2-digit',
  });

  if (message.role === 'user') {
    return (
      <motion.div {...enter} className="flex justify-end">
        <div className="max-w-[85%] space-y-2">
          {divider ? <Divider label={divider} /> : null}
          <div className="rounded-2xl border border-[color-mix(in_srgb,var(--accent)_22%,transparent)] bg-[color-mix(in_srgb,var(--accent)_9%,transparent)] px-4 py-3 text-[15px] leading-relaxed whitespace-pre-wrap">
            {message.content}
          </div>
        </div>
      </motion.div>
    );
  }

  return (
    <motion.div {...enter} className="space-y-4">
      {divider ? <Divider label={divider} /> : null}
      <div className="flex gap-3">
        <SparkMark size={14} className="mt-[5px] shrink-0 text-[var(--accent)]" />
        <div className="min-w-0 flex-1">
          {divider ? (
            <div className="bubble max-w-[34rem] rounded-3xl px-5 py-4 text-[15px] leading-relaxed [&_p]:mb-3 [&_p:last-child]:mb-0">
              {message.content}
            </div>
          ) : (
            <p className="text-[15px] leading-relaxed whitespace-pre-wrap">{message.content}</p>
          )}
          <p className="mt-1 text-[11px] text-[var(--text-muted)] opacity-0 transition-opacity group-hover:opacity-100">
            {time}
          </p>
        </div>
      </div>
    </motion.div>
  );
}

function Divider({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-4 text-[11px] font-medium uppercase tracking-[0.14em] text-[var(--text-muted)]">
      <span className="h-px flex-1 bg-[var(--border)]" />
      {label}
      <span className="h-px flex-1 bg-[var(--border)]" />
    </div>
  );
}

export default MessageItem;
