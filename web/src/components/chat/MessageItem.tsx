import Markdown from 'react-markdown';
import { SparkMark } from '@/components/layout/SparkMark';
import type { ChatMessage } from '@/types';

interface MessageItemProps {
  message: ChatMessage;
}

export function MessageItem({ message }: MessageItemProps) {
  const isUser = message.role === 'user';
  const time = new Date(message.created_at).toLocaleTimeString(undefined, {
    hour: 'numeric',
    minute: '2-digit',
  });

  if (isUser) {
    return (
      <li className="flex justify-end">
        <div className="group max-w-[85%]">
          <div
            className="rounded-[20px] border border-hairline bg-raised px-4 py-2.5 text-[15px] leading-relaxed whitespace-pre-wrap"
            style={{ boxShadow: 'var(--shadow-card)' }}
          >
            {message.content}
          </div>
          <p className="mt-1 pr-1 text-right text-[11px] text-ink-3 opacity-0 transition-opacity duration-[var(--dur-fast)] group-hover:opacity-100">
            {time}
          </p>
        </div>
      </li>
    );
  }

  return (
    <li className="group flex gap-3">
      <SparkMark size={13} className="mt-2 shrink-0 text-accent" />
      <div className="min-w-0 flex-1">
        <div className="prose-vigil text-[15px] leading-[1.7] text-ink">
          <Markdown
            skipHtml
            components={{
              a: ({ href, children }) => (
                <a
                  href={href}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="text-accent underline decoration-accent/40 underline-offset-2"
                >
                  {children}
                </a>
              ),
              code: ({ children, className }) => {
                const isBlock = typeof className === 'string' && className.includes('language-');
                return isBlock ? (
                  <code className="block overflow-x-auto rounded-[var(--r-control)] bg-sunken p-3 font-mono text-[13px]">
                    {children}
                  </code>
                ) : (
                  <code className="rounded bg-sunken px-1 py-0.5 font-mono text-[13px]">
                    {children}
                  </code>
                );
              },
              p: ({ children }) => <p className="mb-3 last:mb-0">{children}</p>,
              ul: ({ children }) => <ul className="mb-3 list-disc pl-5 last:mb-0">{children}</ul>,
              ol: ({ children }) => <ol className="mb-3 list-decimal pl-5 last:mb-0">{children}</ol>,
            }}
          >
            {message.content}
          </Markdown>
        </div>
        <p className="mt-1 text-[11px] text-ink-3 opacity-0 transition-opacity duration-[var(--dur-fast)] group-hover:opacity-100">
          {time}
        </p>
      </div>
    </li>
  );
}
