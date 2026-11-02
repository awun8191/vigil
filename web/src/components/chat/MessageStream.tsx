import { useEffect, useMemo, useRef } from 'react';
import type { ReactNode } from 'react';
import type { ChatMessage } from '@/types';
import MessageItem from './MessageItem';

interface Props {
  messages: ChatMessage[];
  streamedText: string;
  streaming: boolean;
  trailing?: ReactNode;
  emptyHint: ReactNode;
}

const STICK_THRESHOLD = 80;

function dayLabel(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' });
}

export function MessageStream({ messages, streamedText, streaming, trailing, emptyHint }: Props) {
  const endRef = useRef<HTMLDivElement>(null);
  const scrollerRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);

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
              <MessageItem message={message} divider={dividers[i]} />
            </li>
          ))}

          {streaming && streamedText ? (
            <li className="flex gap-3">
              <div className="relative mt-[5px] flex w-3.5 shrink-0 justify-center">
                <span className="absolute mt-[7px] h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--accent)]" />
              </div>
              <div className="min-w-0 flex-1 text-[15px] leading-relaxed whitespace-pre-wrap">
                {streamedText}
                <span
                  aria-hidden
                  className="ml-0.5 inline-block h-[1.05em] w-[2px] translate-y-[3px] animate-pulse bg-[var(--accent)]"
                />
              </div>
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
