import { useState } from 'react';
import { ChevronDown, Sparkles } from 'lucide-react';

interface ThinkingCapsuleProps {
  active: boolean;
  steps?: Array<{ label: string; at: number }>;
}

/**
 * Shown in the position the reply will occupy, from the moment a turn is sent until the first
 * token lands. `role="status"` is what announces it: without it the wait is silent.
 *
 * Steps are only rendered when the caller supplies them. The current backend stream emits plain
 * tokens with no step events, so in practice this stays collapsed.
 */
export function ThinkingCapsule({ active, steps = [] }: ThinkingCapsuleProps) {
  const [expanded, setExpanded] = useState(false);
  if (!active) return null;

  const hasSteps = steps.length > 0;

  return (
    <div
      role="status"
      className="inline-flex flex-col gap-2 rounded-[var(--r-pill)] border border-hairline bg-card/70 px-3.5 py-2 backdrop-blur-sm"
    >
      <div className="flex items-center gap-2">
        <Sparkles
          size={14}
          strokeWidth={1.5}
          aria-hidden="true"
          className="text-accent motion-safe:animate-pulse"
        />
        <span className="text-[13px] text-ink-2">Scanning calendar and inbox...</span>

        {hasSteps ? (
          <button
            type="button"
            onClick={() => setExpanded((value) => !value)}
            aria-expanded={expanded}
            aria-label={expanded ? 'Hide steps' : 'Show steps'}
            className="ml-1 grid h-5 w-5 place-items-center rounded-full text-ink-3 transition-colors hover:text-ink-2"
          >
            <ChevronDown
              size={14}
              strokeWidth={1.5}
              aria-hidden="true"
              className={expanded ? 'rotate-180 transition-transform' : 'transition-transform'}
            />
          </button>
        ) : null}
      </div>

      {expanded && hasSteps ? (
        <ol className="space-y-1 pl-5 text-[12px] text-ink-3">
          {steps.map((step) => (
            <li key={`${step.label}-${step.at}`} className="list-disc">
              {step.label}
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}
