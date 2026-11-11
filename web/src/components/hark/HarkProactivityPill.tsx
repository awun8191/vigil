import type { ReactNode } from 'react';

interface Props {
  verb: string;
  summary: string;
  avatarIcon?: ReactNode;
  onVerbClick?: () => void;
  onPillClick?: () => void;
}

export function HarkProactivityPill({
  verb,
  summary,
  avatarIcon,
  onVerbClick,
  onPillClick,
}: Props) {
  return (
    <div
      onClick={onPillClick}
      className="hark-card group my-2 flex max-w-xl cursor-pointer items-center justify-between rounded-full p-1.5 transition-all duration-200 hover:border-[var(--border-strong)]"
    >
      <div className="flex min-w-0 items-center gap-2.5 pl-0.5">
        {/* Action Verb Button inside the pill */}
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onVerbClick?.();
          }}
          className="flex h-7 items-center rounded-full bg-[color-mix(in_srgb,var(--text)_12%,transparent)] px-3 text-[12px] font-semibold text-[var(--text)] transition-transform hover:scale-105 active:scale-95"
        >
          {verb}
        </button>

        {avatarIcon ? <div className="shrink-0">{avatarIcon}</div> : null}

        {/* Observation Summary */}
        <span className="truncate text-[13px] font-normal text-[var(--text-secondary)] group-hover:text-[var(--text)]">
          {summary}
        </span>
      </div>

      {/* Trailing Corner Notch ⌝ */}
      <span className="pr-3 text-[14px] font-light text-[var(--text-muted)] group-hover:text-[var(--text)]" aria-hidden="true">
        ⌝
      </span>
    </div>
  );
}

export default HarkProactivityPill;
