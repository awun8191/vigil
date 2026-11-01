import { useEffect, useRef } from 'react';
import { SparkMark } from '@/components/layout/SparkMark';
import type { ChatMessage } from '@/types';
import { MessageItem } from './MessageItem';

interface MessageStreamProps {
  messages: ChatMessage[];
  streamedText: string;
  streaming: boolean;
  emptyHint: React.ReactNode;
  trailing?: React.ReactNode;
}

const STICK_THRESHOLD = 80;

/**
 * The conversation.
 *
 * `aria-live="polite"` on the list is what makes streaming replies audible to a screen reader.
 * Auto-scroll only engages when the user is already near the bottom, so a reader who has scrolled
 * up is never yanked back down.
 */
export function MessageStream({
  messages,
  streamedText,
  streaming,
  emptyHint,
  trailing,
}: MessageStreamProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);

  useEffect(() => {
    const node = containerRef.current;
    if (!node) return;
    const onScroll = () => {
      const distance = node.scrollHeight - node.scrollTop - node.clientHeight;
      stickRef.current = distance < STICK_THRESHOLD;
    };
    node.addEventListener('scroll', onScroll, { passive: true });
    return () => node.removeEventListener('scroll', onScroll);
  }, []);

  useEffect(() => {
    if (!stickRef.current) return;
    const node = containerRef.current;
    if (!node) return;
    node.scrollTop = node.scrollHeight;
  }, [messages.length, streamedText]);

  const isEmpty = messages.length === 0 && !streaming;

  return (
    <div ref={containerRef} className="flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl px-5 pt-20 pb-8">
        {trailing ? <div className="mb-5">{trailing}</div> : null}
        {isEmpty ? (
          emptyHint
        ) : (
          <ul
            className="space-y-6"
            aria-live="polite"
            aria-relevant="additions text"
            aria-busy={streaming}
          >
            {messages.map((message) => (
              <MessageItem key={message.id} message={message} />
            ))}

            {streaming && streamedText ? (
              <li className="flex gap-3">
                <SparkMark size={13} className="mt-2 shrink-0 text-accent" />
                <div className="min-w-0 flex-1 text-[15px] leading-[1.7] text-ink whitespace-pre-wrap">
                  {streamedText}
                  <span
                    aria-hidden="true"
                    className="ml-0.5 inline-block h-[1.05em] w-[2px] translate-y-[3px] bg-accent motion-safe:animate-pulse"
                  />
                </div>
              </li>
            ) : null}
          </ul>
        )}
      </div>
    </div>
  );
}
