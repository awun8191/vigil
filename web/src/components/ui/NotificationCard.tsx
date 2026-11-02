import { ChevronDown } from 'lucide-react';
import { AnimatePresence, motion } from 'framer-motion';
import { useState, type ReactNode } from 'react';

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
}

/**
 * The standardized card shell every surface reuses: a phone-notification pill that opens downward
 * into its details. Monochrome, sharp-cornered at the shell scale, animated with one spring.
 */
export function NotificationCard({ avatar, meta, title, children, defaultOpen = false, bodyId }: Props) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <motion.section layout="position" className="card rounded-[18px]" initial={false}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-controls={open ? bodyId : undefined}
        className="flex w-full items-center gap-3.5 px-4 py-3.5 text-left md:px-5"
      >
        {avatar}
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted)]">
            {meta}
          </span>
          <span className="mt-0.5 block truncate text-[15px] font-semibold leading-snug tracking-[-0.01em] text-[var(--text)]">
            {title}
          </span>
        </span>
        <motion.span
          animate={{ rotate: open ? 180 : 0 }}
          transition={spring}
          className="grid h-8 w-8 shrink-0 place-items-center rounded-full text-[var(--text-muted)]"
        >
          <ChevronDown size={16} strokeWidth={1.5} />
        </motion.span>
      </button>

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
