import { useEffect, useMemo, useRef } from 'react';
import { motion } from 'framer-motion';
import type { ReactNode } from 'react';
import type { StreamMessage } from '@/types';
import MessageItem from './MessageItem';
import {
  Message,
} from '@/components/ui/c-message-3-utils/message';
import { Bubble, BubbleContent } from '@/components/ui/c-message-3-utils/bubble';

/** Overshoot spring shared with MessageItem so the reply lands in step. */
const enterSpring = {
  type: 'spring' as const,
  stiffness: 480,
  damping: 20,
  mass: 0.9,
};

interface Props {
  messages: StreamMessage[];
  streamedText: string;
  streaming: boolean;
  trailing?: ReactNode;
  emptyHint: ReactNode;
}

const STICK_THRESHOLD = 80;

function dayLabel(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' });
}

export function MessageStream({ messages, streamedText, streaming, trailing, emptyHint }: Props) {
  const endRef = useRef<HTMLDivElement>(null);
  const scrollerRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);
  // Message ids that have already rendered. The first batch ever seen gets a
  // stagger; everything appended later (sends, streaming) enters immediately.
  const seenRef = useRef<Set<string>>(new Set());
  const staggers = useMemo(() => {
    const coldStart = seenRef.current.size === 0;
    return messages.map((m, i) => {
      const fresh = !seenRef.current.has(m.id);
      seenRef.current.add(m.id);
      return fresh && coldStart && i < 8 ? i : 0;
    });
  }, [messages]);

  // A divider whenever the calendar day changes, matching the brief separators in the design.
  const dividers = useMemo(() => {
    const out: (string | undefined)[] = [];
    let previous: string | null = null;
    for (const m of messages) {
      const day = dayLabel(m.created_at);
      out.push(day !== previous ? day : undefined);
      previous = day;
    }
    return out;
  }, [messages]);

  useEffect(() => {
    const node = scrollerRef.current;
    if (!node) return;
    const onScroll = () => {
      stickRef.current = node.scrollHeight - node.scrollTop - node.clientHeight < STICK_THRESHOLD;
    };
    node.addEventListener('scroll', onScroll, { passive: true });
    return () => node.removeEventListener('scroll', onScroll);
  }, []);

  useEffect(() => {
    if (!stickRef.current) return;
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages.length, streamedText]);

  const isEmpty = messages.length === 0 && !streaming;

  return (
    <div ref={scrollerRef} className="scroll-soft h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl space-y-8 px-6 pb-48 pt-24 md:pl-24 md:pr-6 md:pt-28">
        {isEmpty ? emptyHint : null}

        <ul className="space-y-8" aria-live="polite" aria-relevant="additions text" aria-busy={streaming}>
          {messages.map((message, i) => (
            <li key={message.id} className="group">
              <MessageItem message={message} divider={dividers[i]} staggerIndex={staggers[i]} />
            </li>
          ))}

          {streaming && streamedText ? (
            <li>
              <motion.div initial={{ opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }} transition={enterSpring}>
                <Message>
                  <motion.div
                    initial={{ scale: 0.82 }}
                    animate={{ scale: 1 }}
                    transition={{ ...enterSpring, delay: 0.06 }}
                    style={{ transformOrigin: 'top left' }}
                  >
                    <Bubble variant="muted">
                      <motion.div
                        initial={{ opacity: 0, y: 6 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ duration: 0.45, delay: 0.18, ease: 'easeOut' }}
                      >
                        <BubbleContent>
                          {streamedText}
                          <span
                            aria-hidden
                            className="ml-0.5 inline-block h-[1.05em] w-[2px] translate-y-[3px] animate-pulse bg-[var(--text)]"
                          />
                        </BubbleContent>
                      </motion.div>
                    </Bubble>
                  </motion.div>
                </Message>
              </motion.div>
            </li>
          ) : null}
        </ul>

        {trailing ? <div className="mt-6">{trailing}</div> : null}

        <div ref={endRef} />
      </div>
    </div>
  );
}

export default MessageStream;
