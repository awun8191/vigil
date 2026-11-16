import { useEffect, useMemo, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import type { ReactNode } from 'react';
import type { StreamMessage } from '@/types';
import MessageItem from './MessageItem';
import { MarkdownContent } from './MarkdownContent';

/**
 * Smoothly paces incoming streamed text via requestAnimationFrame so batched SSE
 * deltas glide at 60fps rather than jumping in large chunks.
 */
function useSmoothedStreamText(targetText: string, streaming: boolean): string {
  const [displayedLength, setDisplayedLength] = useState(() =>
    streaming ? 0 : targetText.length,
  );
  const targetRef = useRef(targetText);
  const lengthRef = useRef(displayedLength);
  targetRef.current = targetText;

  useEffect(() => {
    if (!streaming) {
      lengthRef.current = targetText.length;
      setDisplayedLength(targetText.length);
      return undefined;
    }
    if (targetText.length < lengthRef.current) {
      // New turn / reset
      lengthRef.current = targetText.length;
      setDisplayedLength(targetText.length);
      return undefined;
    }
    if (lengthRef.current >= targetText.length) return undefined;

    let rafId = 0;
    let lastTime = performance.now();

    const tick = (now: number) => {
      const dt = Math.min(64, Math.max(8, now - lastTime));
      lastTime = now;
      const targetLen = targetRef.current.length;
      const remaining = targetLen - lengthRef.current;
      if (remaining <= 0) {
        rafId = 0;
        return;
      }
      // Reveal proportionally to backlog (~120-180ms catch-up), minimum 1 char per frame
      const charsPerMs = Math.max(0.06, remaining / 130);
      const step = Math.max(1, Math.ceil(charsPerMs * dt));
      const next = Math.min(targetLen, lengthRef.current + step);
      lengthRef.current = next;
      setDisplayedLength(next);
      if (next < targetLen) {
        rafId = window.requestAnimationFrame(tick);
      } else {
        rafId = 0;
      }
    };

    rafId = window.requestAnimationFrame(tick);
    return () => {
      if (rafId) window.cancelAnimationFrame(rafId);
    };
  }, [targetText, streaming]);

  return streaming ? targetText.slice(0, displayedLength) : targetText;
}

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
  const smoothStreamedText = useSmoothedStreamText(streamedText, streaming);

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

                {smoothStreamedText ? (
                  <div className="bubble-assistant min-w-0 rounded-[20px] rounded-bl-[7px] px-4 py-3">
                    <MarkdownContent content={smoothStreamedText} />
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
