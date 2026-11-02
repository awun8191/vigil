import { ChevronDown } from 'lucide-react';
import { useState } from 'react';

interface Props {
  active: boolean;
  summary?: string;
  steps?: Array<{ label: string }>;
}

/** Breathing indicator occupying the position the reply will take. */
export function ThinkingCapsule({ active, summary = 'Thinking it through', steps = [] }: Props) {
  const [open, setOpen] = useState(false);
  if (!active) return null;

  return (
    <div className="mb-3" role="status">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="glass inline-flex items-center gap-2 rounded-[8px] py-1.5 pl-3 pr-2.5 text-[13px] font-medium text-[var(--text-muted)] transition-transform duration-300 ease-spring hover:scale-[1.02]"
      >
        {summary}
        {steps.length > 0 ? (
          <ChevronDown
            size={14}
            strokeWidth={1.5}
            className={`transition-transform duration-300 ease-spring ${open ? 'rotate-180' : ''}`}
          />
        ) : null}
      </button>

      {open && steps.length > 0 ? (
        <ul className="space-y-2 pb-1 pl-3 pt-3">
          {steps.map((s, i) => (
            <li key={i} className="flex items-center gap-2.5 text-[13px] text-[var(--text-muted)]">
              <span className="h-3.5 w-3.5 rounded-full border border-[var(--border)]" />
              {s.label}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export default ThinkingCapsule;
