import { useMemo, useRef } from 'react';
import { motion } from 'framer-motion';
import type { ReactNode } from 'react';
import type { StreamMessage } from '@/types';
import MessageItem from './MessageItem';
import { MarkdownContent } from './MarkdownContent';

const enterSpring = {
  type: 'spring' as const,
  stiffness: 420,
  damping: 26,
  mass: 0.9,
};

interface Props {
  messages: StreamMessage[];
  streamedText: string;
  streaming: boolean;
  trailing?: ReactNode;
  emptyHint: ReactNode;
}

function dayLabel(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' });
}

export function MessageStream({ messages, streamedText, streaming, trailing, emptyHint }: Props) {
  const seenRef = useRef<Set<string>>(new Set());

  const staggers = useMemo(() => {
    const coldStart = seenRef.current.size === 0;
    return messages.map((message, index) => {
      const fresh = !seenRef.current.has(message.id);
      seenRef.current.add(message.id);
      return fresh && coldStart && index < 8 ? index : 0;
    });
  }, [messages]);

  const dividers = useMemo(() => {
    const out: (string | undefined)[] = [];
    let previous: string | null = null;
    for (const message of messages) {
      const day = dayLabel(message.created_at);
      out.push(day !== previous ? day : undefined);
      previous = day;
    }
    return out;
  }, [messages]);

  const isEmpty = messages.length === 0 && !streaming;

  return (
    <div className="w-full min-w-0">
      {isEmpty ? emptyHint : null}

      <ul
        className="w-full min-w-0 space-y-6"
        aria-live="polite"
        aria-relevant="additions text"
        aria-busy={streaming}
      >
        {messages.map((message, index) => (
          <li key={message.id} className="group w-full min-w-0">
            <MessageItem message={message} divider={dividers[index]} staggerIndex={staggers[index]} />
          </li>
        ))}

        {streaming && (trailing || streamedText) ? (
          <li className="w-full min-w-0">
            <motion.div
              initial={{ opacity: 0, y: 14 }}
              animate={{ opacity: 1, y: 0 }}
              transition={enterSpring}
              className="flex w-full min-w-0 justify-start"
            >
              <div className="w-full min-w-0 max-w-[88%] space-y-2 sm:max-w-[78%]">
                {trailing ? <div className="w-full min-w-0">{trailing}</div> : null}

                {streamedText ? (
                  <div className="bubble-assistant min-w-0 rounded-[20px] rounded-bl-[7px] px-4 py-3">
                    <MarkdownContent content={streamedText} />
                    <span
                      aria-hidden="true"
                      className="mt-1 inline-block h-[1.05em] w-[2px] animate-pulse bg-[var(--text)] align-middle"
                    />
                  </div>
                ) : null}
              </div>
            </motion.div>
          </li>
        ) : null}
      </ul>
    </div>
  );
}

export default MessageStream;
