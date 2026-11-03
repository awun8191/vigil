import { useEffect, useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { ArrowUpRight, CalendarDays, Mail, RefreshCw, ShieldHalf } from 'lucide-react';
import { SourceAvatar, sourceFor } from '@/components/ui/SourceAvatar';

interface DemoAction {
  label: string;
  primary?: boolean;
}

interface DemoCard {
  source: string;
  meta: string;
  title: string;
  detail?: string;
  actions?: DemoAction[];
}

const DEMO_CARDS: DemoCard[] = [
  {
    source: 'email',
    meta: 'Email · waiting on you',
    title: 'Reply drafted for Sarah regarding the Q4 budget',
    detail:
      'Sarah asked for the revised line items before end of day. The reply is written and waits for a word from you.',
    actions: [{ label: 'Review and send', primary: true }, { label: 'Dismiss' }],
  },
  {
    source: 'calendar',
    meta: 'Calendar · in 30 minutes',
    title: 'Design sync starts soon',
    detail: 'Prep notes are ready in memory. A nudge, the way you asked.',
  },
  {
    source: 'alert',
    meta: 'Vigil · for the record',
    title: 'Hosting renews on Friday',
    detail: 'Same provider as the last three years. Nothing to do unless the price changed.',
  },
];

const CARD_TONE: Record<string, number> = { email: 0, calendar: 1, alert: 2 };

const pillSpring = { type: 'spring' as const, stiffness: 320, damping: 24 };

function MiniPill({ card, open }: { card: DemoCard; open: boolean }) {
  const source = sourceFor(card.source);

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 18, scale: 0.97 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: -8, scale: 0.97 }}
      transition={pillSpring}
      className="card rounded-[14px]"
    >
      <div className="flex items-center gap-3 px-4 py-3">
        <SourceAvatar size={30} source={source} />
        <div className="min-w-0 flex-1">
          <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--text-muted)]">
            {card.meta}
          </p>
          <p className="mt-0.5 truncate text-[13px] font-semibold tracking-[-0.01em]">{card.title}</p>
        </div>
        <motion.span
          animate={{ rotate: open ? 180 : 0 }}
          transition={pillSpring}
          className="grid h-6 w-6 shrink-0 place-items-center rounded-full text-[var(--text-muted)]"
        >
          <ArrowUpRight size={13} strokeWidth={1.5} style={{ display: open ? 'none' : undefined }} />
        </motion.span>
      </div>

      <AnimatePresence initial={false}>
        {open && card.detail ? (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.24, ease: [0.4, 0, 0.2, 1] }}
            className="overflow-hidden"
          >
            <div className="border-t border-[var(--border)] px-4 pb-3.5 pt-3">
              <p className="text-[12px] leading-relaxed text-[var(--text-muted)]">{card.detail}</p>
              {card.actions ? (
                <div className="mt-2.5 flex items-center gap-2">
                  {card.actions.map((action) => (
                    <span
                      key={action.label}
                      className={
                        action.primary
                          ? 'inline-flex h-7 items-center rounded-[8px] bg-[var(--text)] px-2.5 text-[11px] font-medium text-[var(--bg-from)]'
                          : 'inline-flex h-7 items-center rounded-[8px] border border-[var(--border)] px-2.5 text-[11px] font-medium text-[var(--text-muted)]'
                      }
                    >
                      {action.label}
                    </span>
                  ))}
                </div>
              ) : null}
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </motion.div>
  );
}

/** The hero product shot. Loops forever unless the visitor prefers reduced motion. */
export function HeroMockup() {
  const reduced = useReducedMotion();
  const [visible, setVisible] = useState(reduced ? 3 : 1);
  const [openIndex, setOpenIndex] = useState<number | null>(null);

  useEffect(() => {
    if (reduced) return;

    const timeline = window.setInterval(() => {
      setOpenIndex(null);
      setVisible((current) => {
        if (current >= DEMO_CARDS.length) {
          window.setTimeout(() => {
            setVisible(1);
            window.setTimeout(() => setOpenIndex(0), 700);
          }, 2200);
          return 3;
        }
        return current + 1;
      });
    }, 1600);

    const opener = window.setInterval(() => {
      setOpenIndex((current) => (current === null ? 0 : null));
    }, 4800);

    return () => {
      window.clearInterval(timeline);
      window.clearInterval(opener);
    };
  }, [reduced]);

  return (
    <motion.div
      initial={{ opacity: 0, y: 40 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ type: 'spring', stiffness: 120, damping: 20, delay: 0.5 }}
      className="relative mx-auto mt-14 w-full max-w-3xl md:mt-16"
    >
      <div className="card overflow-hidden rounded-t-[16px] border-b-0 md:rounded-t-[18px]">
        {/* Window bar */}
        <div className="flex items-center justify-between border-b border-[var(--border)] px-4 py-2.5">
          <span className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--text-muted)]">
            <ShieldHalf size={12} strokeWidth={1.5} />
            Vigil
          </span>
          <span className="text-[11px] text-[var(--text-muted)]">Tuesday · Live briefing</span>
        </div>

        <div className="grid gap-3 p-4 md:grid-cols-[minmax(0,1fr)_200px]">
          <div className="space-y-3">
            <AnimatePresence>
              {DEMO_CARDS.slice(0, visible).map((card) => (
                <MiniPill
                  key={card.source}
                  card={card}
                  open={openIndex === CARD_TONE[card.source]}
                />
              ))}
            </AnimatePresence>
          </div>

          {/* Widgets rail miniature */}
          <div className="hidden space-y-3 md:block">
            <div className="card rounded-[14px] p-3.5">
              <p className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-[var(--text-muted)]">
                <CalendarDays size={11} strokeWidth={1.5} /> Today
              </p>
              <p className="mt-1.5 text-[13px] font-semibold">2 events, none before noon</p>
            </div>
            <div className="card rounded-[14px] p-3.5">
              <p className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-[var(--text-muted)]">
                <Mail size={11} strokeWidth={1.5} /> Inbox
              </p>
              <p className="mt-1.5 text-[13px] font-semibold">Quiet. One reply drafted.</p>
            </div>
            <div className="card rounded-[14px] p-3.5">
              <p className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-[var(--text-muted)]">
                <RefreshCw size={11} strokeWidth={1.5} /> Connectors
              </p>
              <p className="mt-1.5 text-[13px] font-semibold">All synced 6 minutes ago</p>
            </div>
          </div>
        </div>
      </div>

      {/* Bottom fade so the mockup reads as the start of the real product */}
      <div
        className="pointer-events-none absolute inset-x-0 bottom-0 h-24"
        style={{
          background:
            'linear-gradient(180deg, transparent, color-mix(in srgb, var(--bg-from) 88%, transparent))',
        }}
      />
    </motion.div>
  );
}

export default HeroMockup;
