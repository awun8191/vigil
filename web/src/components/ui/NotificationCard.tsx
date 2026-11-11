import { ChevronDown } from 'lucide-react';
import { AnimatePresence, motion } from 'framer-motion';
import { useState, type ReactNode } from 'react';
import type { Urgency } from '@/types';

const spring = { type: 'spring' as const, stiffness: 400, damping: 30 };

interface Props {
  /** Round source avatar, from SourceAvatar. */
  avatar: ReactNode;
  /** Small plain-language line above the description, e.g. "Email · 2m ago". */
  meta: ReactNode;
  /** The one description line always visible while closed. */
  title: ReactNode;
  /** Everything the card reveals when opened. */
  children: ReactNode;
  /** Start open; defaults closed like a phone notification stack. */
  defaultOpen?: boolean;
  /** Accessible label for the reveal region. */
  bodyId: string;
  /** Urgency level determining 3px left urgency rail color. */
  urgency?: Urgency | string | null;
  /** Proactivity tag string (e.g. 'Urgent Action', 'Decision Needed'). */
  proactivityTag?: string | null;
  /** Pinned action verb for the Hark Pro floating proactivity pill (e.g. 'Draft', 'Accept', 'Review'). */
  verb?: string | null;
  /** Optional summary or observation text for the proactivity pill. Defaults to title. */
  pillText?: ReactNode;
  /** Callback when the pinned action verb button is clicked. */
  onVerbClick?: (e: React.MouseEvent) => void;
  /** Callback when the trailing corner notch ⌝ or observation pill text is clicked. */
  onNotchClick?: (e: React.MouseEvent) => void;
}

/**
 * The standardized card shell every surface reuses: a phone-notification pill that opens downward
 * into its details. Monochrome, sharp-cornered at the shell scale, animated with one spring.
 */
export function NotificationCard({
  avatar,
  meta,
  title,
  children,
  defaultOpen = false,
  bodyId,
  urgency,
  proactivityTag,
  verb,
  pillText,
  onVerbClick,
  onNotchClick,
}: Props) {
  const [open, setOpen] = useState(defaultOpen);

  const urgencyRailClass =
    urgency === 'critical'
      ? 'border-l-[3px] !border-l-[var(--urgency-critical)]'
      : urgency === 'high'
      ? 'border-l-[3px] !border-l-[var(--urgency-high)]'
      : urgency === 'medium'
      ? 'border-l-[3px] !border-l-[var(--urgency-medium)]'
      : urgency === 'low'
      ? 'border-l-[3px] !border-l-[var(--urgency-low)]'
      : '';

  return (
    <motion.section
      layout="position"
      className={`card rounded-squircle-card overflow-hidden ${urgencyRailClass}`.trim()}
      initial={false}
    >
      <div className="px-4 py-3.5 md:px-5">
        <div className="flex w-full items-center gap-3.5">
          {avatar}
          <div
            role="button"
            tabIndex={0}
            onClick={() => setOpen((value) => !value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                setOpen((value) => !value);
              }
            }}
            aria-expanded={open}
            aria-controls={open ? bodyId : undefined}
            className="min-w-0 flex-1 cursor-pointer select-none text-left"
          >
            <span className="flex flex-wrap items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted)]">
              {meta}
              {proactivityTag ? (
                <span className="rounded-full border border-[var(--tag-proactive-border)] bg-[var(--tag-proactive-bg)] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.06em] text-[var(--tag-proactive-text)]">
                  {proactivityTag}
                </span>
              ) : null}
            </span>
            <span className="mt-0.5 block truncate text-[15px] font-semibold leading-snug tracking-[-0.01em] text-[var(--text)]">
              {title}
            </span>
          </div>
          <button
            type="button"
            onClick={() => setOpen((value) => !value)}
            aria-expanded={open}
            aria-controls={open ? bodyId : undefined}
            aria-label={open ? 'Collapse notification' : 'Expand notification'}
            className="grid h-8 w-8 shrink-0 place-items-center rounded-full text-[var(--text-muted)] transition-colors hover:bg-[var(--card-hover)] hover:text-[var(--text)]"
          >
            <motion.span
              animate={{ rotate: open ? 180 : 0 }}
              transition={spring}
              className="grid place-items-center"
            >
              <ChevronDown size={16} strokeWidth={1.5} />
            </motion.span>
          </button>
        </div>

        {/* Hark Pro Floating Proactivity Observation Pill */}
        {verb ? (
          <div
            role="group"
            aria-label="Actionable observation pill"
            className="mt-3 flex min-h-[44px] w-full items-center justify-between gap-3 rounded-full border border-[var(--border)] bg-[var(--verb-pill-secondary-bg)] p-[6px] pl-[6px] pr-4 shadow-[var(--shadow-rim)] backdrop-blur-md transition-all hover:border-[var(--border-strong)] hover:bg-[var(--verb-pill-secondary-hover-bg)]"
          >
            <div
              className="flex min-w-0 flex-1 items-center gap-3 cursor-pointer"
              onClick={onNotchClick}
            >
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onVerbClick?.(e);
                }}
                className="inline-flex h-[32px] shrink-0 items-center justify-center rounded-full bg-[var(--verb-pill-bg)] px-3.5 text-[13px] font-semibold text-[var(--verb-pill-text)] shadow-sm transition-all hover:bg-[var(--verb-pill-hover-bg)] active:scale-95"
              >
                {verb}
              </button>
              <span className="truncate text-[13px] font-medium leading-normal text-[var(--text)]">
                {pillText ?? title}
              </span>
            </div>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onNotchClick?.(e);
              }}
              className="shrink-0 p-1 font-mono text-[16px] font-light leading-none text-[var(--text-muted)] transition-colors hover:text-[var(--text)]"
              title="Deep-link to action"
              aria-label="Deep-link to action"
            >
              ⌝
            </button>
          </div>
        ) : null}
      </div>

      <AnimatePresence initial={false}>
        {open ? (
          <motion.div
            id={bodyId}
            key="body"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.28, ease: [0.4, 0, 0.2, 1] }}
            className="overflow-hidden"
          >
            <div className="border-t border-[var(--border)] px-4 pb-4 pt-4 md:px-5">
              {children}
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </motion.section>
  );
}

export default NotificationCard;
