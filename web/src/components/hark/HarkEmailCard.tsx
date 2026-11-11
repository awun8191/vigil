import { Maximize2 } from 'lucide-react';

interface Props {
  title: string;
  to: string;
  subject: string;
  body: string;
  promptQuestion?: string;
  onSend: () => void;
}

export function HarkEmailCard({
  title,
  to,
  subject,
  body,
  promptQuestion,
  onSend,
}: Props) {
  return (
    <div className="my-3 max-w-2xl">
      {/* Email Card Container */}
      <div className="hark-card rounded-[22px] p-4">
        <div className="flex items-center justify-between pb-3 border-b border-[var(--border)]">
          <span className="text-[13.5px] font-semibold text-[var(--text)]">{title}</span>
          <button
            type="button"
            onClick={onSend}
            className="text-[var(--text-muted)] hover:text-[var(--text)]"
          >
            <Maximize2 size={13} />
          </button>
        </div>

        <div className="mt-3 space-y-1.5 text-[12.5px] text-[var(--text-secondary)]">
          <div>
            <span className="text-[var(--text-muted)]">To: </span>
            <span className="text-[var(--text)]">{to}</span>
          </div>
          <div>
            <span className="text-[var(--text-muted)]">Subject: </span>
            <span className="text-[var(--text)]">{subject}</span>
          </div>
        </div>

        <div className="mt-3.5 whitespace-pre-wrap rounded-xl bg-[color-mix(in_srgb,var(--text)_4%,transparent)] p-3 text-[13px] leading-relaxed text-[var(--text)]">
          {body}
        </div>
      </div>

      {/* Follow-up question & action prompt */}
      {promptQuestion ? (
        <div className="hark-card mt-2.5 rounded-xl p-3 text-[13px] leading-relaxed text-[var(--text-secondary)]">
          {promptQuestion}
        </div>
      ) : null}
    </div>
  );
}

export default HarkEmailCard;
