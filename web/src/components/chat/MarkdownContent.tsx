import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';
import { Check, Copy, ArrowUpRight, Terminal, FileCode2 } from 'lucide-react';

interface CodeBlockProps {
  language: string;
  code: string;
}

export function CodeBlock({ language, code }: CodeBlockProps) {
  const [copied, setCopied] = useState(false);

  const lines = code.split('\n');
  const lineCount = lines.length;
  const isShell = ['bash', 'sh', 'zsh', 'shell', 'console', 'terminal'].includes(
    language.toLowerCase(),
  );

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      const textarea = document.createElement('textarea');
      textarea.value = code;
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand('copy');
      document.body.removeChild(textarea);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <div className="hark-card my-4 w-full max-w-full min-w-0 overflow-hidden rounded-squircle-md p-1">
      <div className="overflow-hidden rounded-squircle-sm border border-[var(--border)] bg-[color-mix(in_srgb,var(--hark-card-fg)_4%,var(--hark-card-bg))]">
        <div className="flex items-center justify-between border-b border-[var(--border)] bg-[color-mix(in_srgb,var(--text)_4%,transparent)] px-3.5 py-2">
          <div className="flex items-center gap-2 min-w-0">
            {isShell ? (
              <Terminal size={13} strokeWidth={1.6} className="shrink-0 text-[var(--text-muted)]" />
            ) : (
              <FileCode2 size={13} strokeWidth={1.6} className="shrink-0 text-[var(--text-muted)]" />
            )}
            <span className="truncate font-mono text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text)]">
              {language || 'text'}
            </span>
            {lineCount > 1 ? (
              <span className="rounded-full bg-[color-mix(in_srgb,var(--text)_7%,transparent)] px-2 py-0.5 font-mono text-[10px] tabular-nums text-[var(--text-muted)]">
                {lineCount} lines
              </span>
            ) : null}
          </div>

          <button
            type="button"
            onClick={handleCopy}
            aria-label={copied ? 'Copied code' : 'Copy code to clipboard'}
            className="inline-flex shrink-0 cursor-pointer items-center gap-1.5 rounded-[7px] border border-transparent px-2.5 py-1 text-[11px] font-medium text-[var(--text-muted)] transition-all duration-150 hover:border-[var(--border)] hover:bg-[color-mix(in_srgb,var(--text)_8%,transparent)] hover:text-[var(--text)] active:scale-95"
          >
            {copied ? (
              <>
                <Check size={12} strokeWidth={2} className="text-emerald-400" />
                <span className="text-emerald-400">Copied</span>
              </>
            ) : (
              <>
                <Copy size={12} strokeWidth={1.5} />
                <span>Copy</span>
              </>
            )}
          </button>
        </div>

        <pre className="scroll-soft max-w-full overflow-x-auto p-3.5 font-mono text-[13px] leading-[1.65] text-[var(--text)]">
          <code className="block min-w-0">{code}</code>
        </pre>
      </div>
    </div>
  );
}

interface MarkdownContentProps {
  content: string;
  className?: string;
}

export function MarkdownContent({ content, className = '' }: MarkdownContentProps) {
  return (
    <div
      className={`w-full max-w-full min-w-0 text-[14px] leading-[1.35] text-[var(--text)] [overflow-wrap:anywhere] ${className}`}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{
          code({ className: codeClass, children, ...props }) {
            const match = /language-([\w-]+)/.exec(codeClass || '');
            const raw = String(children ?? '');
            const isBlock = Boolean(match) || raw.includes('\n');
            if (isBlock) {
              const codeString = raw.replace(/\n$/, '');
              return <CodeBlock language={match?.[1] ?? ''} code={codeString} />;
            }
            return (
              <code
                className="rounded-[6px] border border-[var(--border)] bg-[color-mix(in_srgb,var(--text)_7%,transparent)] px-1.5 py-0.5 font-mono text-[0.86em] font-medium text-[var(--text)] [overflow-wrap:anywhere]"
                {...props}
              >
                {children}
              </code>
            );
          },
          pre({ children }) {
            return <>{children}</>;
          },
          p({ children }) {
            return <p className="mb-2.5 last:mb-0 leading-[1.35] [overflow-wrap:anywhere]">{children}</p>;
          },
          ul({ children }) {
            return (
              <ul className="mb-2.5 last:mb-0 list-disc space-y-1 pl-5 marker:text-[var(--text-muted)]">
                {children}
              </ul>
            );
          },
          ol({ children }) {
            return (
              <ol className="mb-2.5 last:mb-0 list-decimal space-y-1 pl-5 marker:font-medium marker:text-[var(--text-muted)]">
                {children}
              </ol>
            );
          },
          li({ children }) {
            return <li className="leading-[1.35] pl-0.5 [overflow-wrap:anywhere]">{children}</li>;
          },
          h1({ children }) {
            return (
              <h1 className="mb-3 mt-6 first:mt-0 text-[1.35em] font-semibold tracking-[-0.02em] text-[var(--text)]">
                {children}
              </h1>
            );
          },
          h2({ children }) {
            return (
              <h2 className="mb-2.5 mt-5 first:mt-0 border-b border-[var(--border)] pb-1.5 text-[1.18em] font-semibold tracking-[-0.015em] text-[var(--text)]">
                {children}
              </h2>
            );
          },
          h3({ children }) {
            return (
              <h3 className="mb-2 mt-4 first:mt-0 text-[1.06em] font-semibold tracking-[-0.01em] text-[var(--text)]">
                {children}
              </h3>
            );
          },
          h4({ children }) {
            return (
              <h4 className="mb-1.5 mt-3 first:mt-0 text-[0.96em] font-semibold uppercase tracking-[0.04em] text-[var(--text-muted)]">
                {children}
              </h4>
            );
          },
          blockquote({ children }) {
            return (
              <blockquote className="my-3.5 rounded-r-[10px] border-l-[3px] border-[var(--border-strong)] bg-[color-mix(in_srgb,var(--text)_3.5%,transparent)] py-2.5 pl-4 pr-3 text-[0.96em] text-[var(--text-muted)]">
                {children}
              </blockquote>
            );
          },
          hr() {
            return <hr className="my-5 border-0 border-t border-[var(--border)]" />;
          },
          a({ href, children }) {
            const isExternal = Boolean(href && /^https?:\/\//i.test(href));
            return (
              <a
                href={href}
                target={isExternal ? '_blank' : undefined}
                rel={isExternal ? 'noreferrer noopener' : undefined}
                className="inline-flex items-baseline gap-0.5 font-medium text-[var(--text)] underline decoration-[var(--border-strong)] underline-offset-4 transition-colors hover:decoration-[var(--text)]"
              >
                <span className="[overflow-wrap:anywhere]">{children}</span>
                {isExternal ? (
                  <ArrowUpRight
                    size={12}
                    strokeWidth={1.75}
                    className="inline-block shrink-0 translate-y-[1px] text-[var(--text-muted)]"
                  />
                ) : null}
              </a>
            );
          },
          table({ children }) {
            return (
              <div className="my-4 w-full max-w-full overflow-hidden rounded-[12px] border border-[var(--border)] bg-[color-mix(in_srgb,var(--text)_2.5%,transparent)] shadow-xs">
                <div className="scroll-soft max-w-full overflow-x-auto">
                  <table className="w-full border-collapse text-left text-[13.5px] tabular-nums">
                    {children}
                  </table>
                </div>
              </div>
            );
          },
          thead({ children }) {
            return (
              <thead className="border-b border-[var(--border)] bg-[color-mix(in_srgb,var(--text)_5%,transparent)]">
                {children}
              </thead>
            );
          },
          th({ children }) {
            return (
              <th className="px-3.5 py-2.5 text-left text-[11.5px] font-semibold uppercase tracking-[0.06em] text-[var(--text)]">
                {children}
              </th>
            );
          },
          td({ children }) {
            return (
              <td className="border-t border-[var(--border)] px-3.5 py-2.5 align-top text-[var(--text-muted)] [overflow-wrap:anywhere]">
                {children}
              </td>
            );
          },
          strong({ children }) {
            return <strong className="font-semibold text-[var(--text)]">{children}</strong>;
          },
          input({ type, checked, ...props }) {
            if (type === 'checkbox') {
              return (
                <input
                  type="checkbox"
                  checked={checked}
                  readOnly
                  className="mr-2 h-3.5 w-3.5 translate-y-[1px] rounded accent-[var(--hark-control-bg)]"
                  {...props}
                />
              );
            }
            return <input type={type} {...props} />;
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

export default MarkdownContent;
