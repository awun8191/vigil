import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  AnimatePresence,
  motion,
  useInView,
  useReducedMotion,
  type Variants,
} from 'framer-motion';
import {
  ArrowRight,
  BellRing,
  CalendarDays,
  ChevronDown,
  CircleCheck,
  Handshake,
  ReceiptText,
  Sprout,
  Wifi,
} from 'lucide-react';
import type { ComponentType, ReactNode } from 'react';
import { HeroMockup } from '@/components/landing/HeroMockup';
import { VigilSpark } from '@/components/chat/MessageItem';

const bounce = { type: 'spring' as const, stiffness: 260, damping: 17 };
const rise = { type: 'spring' as const, stiffness: 140, damping: 20 };

/** Scroll reveal used by every section: quiet fade-and-rise, bouncing where it pops. */
function Reveal({
  children,
  delay = 0,
  y = 28,
  className,
}: {
  children: ReactNode;
  delay?: number;
  y?: number;
  className?: string;
}) {
  const reduced = useReducedMotion();
  return (
    <motion.div
      initial={{ opacity: 0, y: reduced ? 0 : y }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.3 }}
      transition={{ ...rise, delay }}
      className={className}
    >
      {children}
    </motion.div>
  );
}

/** A number that counts itself up the first time it scrolls into view. */
function CountUp({ to, suffix = '' }: { to: number; suffix?: string }) {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true, amount: 0.6 });
  const [value, setValue] = useState(0);

  useEffect(() => {
    if (!inView) return;
    const start = performance.now();
    const duration = 900;
    let frame = 0;
    const step = (now: number) => {
      const progress = Math.min((now - start) / duration, 1);
      setValue(Math.round(to * (1 - Math.pow(1 - progress, 3))));
      if (progress < 1) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [inView, to]);

  return (
    <span ref={ref} className="tabular">
      {value}
      {suffix}
    </span>
  );
}

/* ----------------------------------------------------------------------------------- Nav -- */

const NAV_LINKS: Array<[string, string]> = [
  ['How it works', '#how'],
  ['What it watches', '#watching'],
  ['FAQ', '#faq'],
];

function LandingNav() {
  return (
    <motion.header
      initial={{ opacity: 0, y: -16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={rise}
      className="fixed inset-x-0 top-0 z-40 border-b border-[var(--border)] bg-[color-mix(in_srgb,var(--bg-from)_85%,transparent)] backdrop-blur-xl"
    >
      <div className="mx-auto flex h-14 w-full max-w-5xl items-center px-6">
        <span className="flex items-center gap-2">
          <span className="text-[15px] font-semibold tracking-heading">Vigil</span>
          <span className="flex items-center text-[var(--accent)]" aria-hidden="true">
            <VigilSpark className="h-4 w-4" />
          </span>
        </span>
        <nav aria-label="Site" className="ml-6 hidden items-center gap-5 md:flex">
          {NAV_LINKS.map(([label, href]) => (
            <a
              key={href}
              href={href}
              className="text-[13px] font-medium text-[var(--text-muted)] transition-colors hover:text-[var(--text)]"
            >
              {label}
            </a>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-2.5">
          <Link
            to="/pair"
            className="hidden h-9 items-center rounded-[8px] border border-[var(--border)] px-3 text-[13px] font-medium text-[var(--text-muted)] transition-colors hover:border-[var(--border-strong)] hover:text-[var(--text)] sm:inline-flex"
          >
            Log in
          </Link>
          <Link
            to="/pair"
            className="inline-flex h-9 items-center gap-1.5 rounded-[8px] bg-[var(--text)] px-3.5 text-[13px] font-medium text-[var(--bg-from)] transition-opacity hover:opacity-85"
          >
            Pair a device
            <ArrowRight size={14} strokeWidth={1.75} />
          </Link>
        </div>
      </div>
    </motion.header>
  );
}

/* ---------------------------------------------------------------------------------- Hero -- */

const HEADLINE_PHRASES = ['Someone is always watching.', 'So the small things never slip.'];

const wordVariants: Variants = {
  hidden: { opacity: 0, y: 26, filter: 'blur(8px)' },
  show: { opacity: 1, y: 0, filter: 'blur(0px)', transition: rise },
  exit: { opacity: 0, y: -18, filter: 'blur(5px)', transition: { duration: 0.26, ease: 'easeIn' } },
};

/**
 * The hero headline is a single slot that swaps between phrases: words rise in with blur, the
 * line holds for a beat, then lifts out and the next phrase staggers in. Loops forever.
 */
function RotatingHeadline({ phrases }: { phrases: string[] }) {
  const reduced = useReducedMotion();
  const [index, setIndex] = useState(0);

  useEffect(() => {
    if (reduced || phrases.length < 2) return;
    const timer = window.setInterval(() => {
      setIndex((current) => (current + 1) % phrases.length);
    }, 4200);
    return () => window.clearInterval(timer);
  }, [reduced, phrases.length]);

  const lineVariants: Variants = {
    show: { transition: { staggerChildren: 0.055, delayChildren: index === 0 ? 0.25 : 0.05 } },
    exit: { transition: { staggerChildren: 0.018, staggerDirection: -1 } },
  };

  const words = (phrases[index] ?? phrases[0] ?? '').split(' ');

  return (
    <span className="block min-h-[1.08em]">
      <AnimatePresence mode="wait">
        <motion.span
          key={phrases[index]}
          variants={lineVariants}
          initial="hidden"
          animate="show"
          exit="exit"
          className="block"
        >
          {words.map((word, wordIndex) => (
            <motion.span
              key={`${word}-${wordIndex}`}
              variants={wordVariants}
              className="mr-[0.26em] inline-block"
            >
              {word}
            </motion.span>
          ))}
        </motion.span>
      </AnimatePresence>
    </span>
  );
}

function Hero() {
  return (
    <section className="relative pt-32 text-center md:pt-40">
      <h1 className="mx-auto max-w-5xl text-center text-[clamp(2.8rem,7.5vw,5.6rem)] font-semibold leading-[1.05] tracking-[-0.035em]">
        <RotatingHeadline phrases={HEADLINE_PHRASES} />
      </h1>

      <motion.p
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ ...rise, delay: 1.15 }}
        className="mx-auto mt-6 max-w-[520px] px-6 text-[15px] leading-relaxed text-[var(--text-muted)] md:text-[16px]"
      >
        Vigil reads the signals your accounts give off, watches for the things you asked it to care
        about, and only speaks up when something needs you.
      </motion.p>

      <motion.div
        initial={{ opacity: 0, y: 14, scale: 0.97 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ ...bounce, delay: 1.35 }}
        className="mt-8 flex items-center justify-center gap-3 px-6"
      >
        <Link
          to="/pair"
          className="inline-flex h-11 items-center gap-2 rounded-[10px] bg-[var(--text)] px-5 text-[14px] font-medium text-[var(--bg-from)] transition-opacity hover:opacity-85"
        >
          Pair a device
          <ArrowRight size={15} strokeWidth={1.75} />
        </Link>
        <a
          href="#how"
          className="inline-flex h-11 items-center rounded-[10px] border border-[var(--border)] px-5 text-[14px] font-medium text-[var(--text)] transition-colors hover:border-[var(--border-strong)]"
        >
          How it works
        </a>
      </motion.div>

      <HeroMockup />
    </section>
  );
}


/* -------------------------------------------------------------------------- How it works -- */

const STEPS: Array<{ icon: ComponentType<{ size?: number; strokeWidth?: number }>; title: string; body: string }> = [
  {
    icon: Handshake,
    title: 'Pair',
    body: 'The backend boots and prints a code. You type that code in once, and this device is trusted from then on.',
  },
  {
    icon: Wifi,
    title: 'Connect',
    body: 'Link the accounts you want watched. Everything stays on your hardware, your tokens included.',
  },
  {
    icon: Sprout,
    title: 'Breathe',
    body: 'After that Vigil works quietly in the background and delivers a short briefing only when there is something worth reading.',
  },
];

function HowItWorks() {
  return (
    <section id="how" className="scroll-mt-24 pt-28 md:pt-36">
      <Reveal>
        <p className="text-[12px] font-semibold uppercase tracking-[0.18em] text-[var(--text-muted)]">
          How it works
        </p>
        <h2 className="mt-3 max-w-xl text-[clamp(1.8rem,4vw,2.6rem)] font-semibold leading-tight tracking-[-0.02em]">
          Three steps, then it takes over.
        </h2>
      </Reveal>

      <div className="mt-10 grid gap-4 md:grid-cols-3">
        {STEPS.map((step, index) => (
          <Reveal key={step.title} delay={index * 0.12}>
            <motion.div whileHover={{ y: -4 }} className="card h-full rounded-[16px] p-6">
              <span className="grid h-10 w-10 place-items-center rounded-full bg-[color-mix(in_srgb,var(--text)_8%,transparent)] text-[var(--text)]">
                <step.icon size={18} strokeWidth={1.5} />
              </span>
              <h3 className="mt-4 text-[17px] font-semibold tracking-[-0.01em]">
                <span className="mr-1.5 text-[var(--text-muted)]">{index + 1}.</span>
                {step.title}
              </h3>
              <p className="mt-2 text-[14px] leading-relaxed text-[var(--text-muted)]">{step.body}</p>
            </motion.div>
          </Reveal>
        ))}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------------ What it watches -- */

const WATCHED: Array<{ icon: ComponentType<{ size?: number; strokeWidth?: number }>; title: string; body: string; stat: number; suffix?: string }> = [
  {
    icon: BellRing,
    title: 'Live briefings',
    body: 'Cards arrive like phone notifications: small, readable, and asking nothing until you open them.',
    stat: 90,
    suffix: '%',
  },
  {
    icon: CalendarDays,
    title: 'Calendar',
    body: 'Declined meetings that matter, double bookings, and the gaps you should protect.',
    stat: 30,
  },
  {
    icon: ReceiptText,
    title: 'Invoices & renewals',
    body: 'The charges coming quietly and the subscriptions you stopped noticing.',
    stat: 70,
    suffix: '%',
  },
  {
    icon: CircleCheck,
    title: 'Actions on approval',
    body: 'It can draft the reply and book the thing, but nothing moves until you say so.',
    stat: 100,
    suffix: '%',
  },
];

function WhatItWatches() {
  return (
    <section id="watching" className="scroll-mt-24 pt-28 md:pt-36">
      <Reveal>
        <p className="text-[12px] font-semibold uppercase tracking-[0.18em] text-[var(--text-muted)]">
          What it watches
        </p>
        <h2 className="mt-3 max-w-xl text-[clamp(1.8rem,4vw,2.6rem)] font-semibold leading-tight tracking-[-0.02em]">
          Signal in, calm out.
        </h2>
      </Reveal>

      <div className="mt-10 grid gap-4 sm:grid-cols-2">
        {WATCHED.map((item, index) => (
          <Reveal key={item.title} delay={index * 0.1}>
            <div className="card flex h-full items-start gap-4 rounded-[16px] p-6">
              <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-[color-mix(in_srgb,var(--text)_8%,transparent)] text-[var(--text)]">
                <item.icon size={18} strokeWidth={1.5} />
              </span>
              <div className="min-w-0">
                <h3 className="text-[16px] font-semibold tracking-[-0.01em]">{item.title}</h3>
                <p className="mt-1.5 text-[14px] leading-relaxed text-[var(--text-muted)]">
                  {item.body}
                </p>
              </div>
              <span className="ml-auto shrink-0 text-[22px] font-semibold tabular tracking-[-0.02em] text-[var(--text-muted)]">
                <CountUp to={item.stat} suffix={item.suffix} />
              </span>
            </div>
          </Reveal>
        ))}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------------------------- FAQ -- */

const FAQS: Array<[string, string]> = [
  [
    'How does a device pair?',
    'When the backend starts it prints a single-use pairing code that lives for ten minutes. You enter it once on this device, and it is remembered until you unpair.',
  ],
  [
    'Where does my data live?',
    'On the machine running the backend. Vigil is not a hosted service: the accounts connect to it, it does all the watching locally, and nothing is forwarded anywhere else.',
  ],
  [
    'What happens when something urgent shows up?',
    'Urgent cards surface first and loudest, everything else waits for you to have a moment. Nothing is ever actioned without a run that you can trace.',
  ],
  [
    'Can it do things, or just tell me?',
    'Both, on your terms. Each card carries the action it is asking for, some tools can be set to run without asking, and every action leaves a trail you can review.',
  ],
];

function Faq() {
  const [open, setOpen] = useState<number | null>(0);

  return (
    <section id="faq" className="scroll-mt-24 pt-28 md:pt-36">
      <Reveal>
        <p className="text-[12px] font-semibold uppercase tracking-[0.18em] text-[var(--text-muted)]">
          FAQ
        </p>
        <h2 className="mt-3 text-[clamp(1.8rem,4vw,2.6rem)] font-semibold leading-tight tracking-[-0.02em]">
          The straight answers.
        </h2>
      </Reveal>

      <div className="mt-10 max-w-3xl space-y-3">
        {FAQS.map(([question, answer], index) => {
          const isOpen = open === index;
          return (
            <Reveal key={question} delay={index * 0.06}>
              <div className="card overflow-hidden rounded-[14px]">
                <button
                  type="button"
                  aria-expanded={isOpen}
                  onClick={() => setOpen(isOpen ? null : index)}
                  className="flex w-full items-center justify-between gap-4 px-5 py-4 text-left"
                >
                  <span className="text-[15px] font-semibold tracking-[-0.01em]">{question}</span>
                  <motion.span
                    animate={{ rotate: isOpen ? 180 : 0 }}
                    transition={bounce}
                    className="shrink-0 text-[var(--text-muted)]"
                  >
                    <ChevronDown size={16} strokeWidth={1.5} />
                  </motion.span>
                </button>
                <AnimatePresence initial={false}>
                  {isOpen ? (
                    <motion.div
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: 'auto', opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      transition={{ duration: 0.26, ease: [0.4, 0, 0.2, 1] }}
                      className="overflow-hidden"
                    >
                      <p className="border-t border-[var(--border)] px-5 py-4 text-[14px] leading-relaxed text-[var(--text-muted)]">
                        {answer}
                      </p>
                    </motion.div>
                  ) : null}
                </AnimatePresence>
              </div>
            </Reveal>
          );
        })}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------------------- Final CTA -- */

function FinalCta() {
  return (
    <section className="pt-28 md:pt-36">
      <Reveal>
        <div className="card relative overflow-hidden rounded-[20px] px-6 py-14 text-center md:py-16">
          <h2 className="mx-auto max-w-2xl text-[clamp(1.9rem,4.5vw,2.8rem)] font-semibold leading-tight tracking-[-0.02em]">
            Let it take the night shift.
          </h2>
          <p className="mx-auto mt-4 max-w-md text-[15px] leading-relaxed text-[var(--text-muted)]">
            Boot the backend, enter the code, and tomorrow morning starts with a briefing instead of
            ten browser tabs.
          </p>
          <motion.div
            initial={{ opacity: 0, scale: 0.96 }}
            whileInView={{ opacity: 1, scale: 1 }}
            viewport={{ once: true }}
            transition={bounce}
            className="mt-8 flex justify-center"
          >
            <Link
              to="/pair"
              className="inline-flex h-12 items-center gap-2 rounded-[10px] bg-[var(--text)] px-6 text-[15px] font-medium text-[var(--bg-from)] transition-opacity hover:opacity-85"
            >
              Pair a device
              <ArrowRight size={16} strokeWidth={1.75} />
            </Link>
          </motion.div>
        </div>
      </Reveal>

      <footer className="flex flex-col items-center justify-between gap-3 py-10 text-[12px] text-[var(--text-muted)] md:flex-row">
        <span>Vigil. Calm by default.</span>
        <nav aria-label="Footer" className="flex items-center gap-4">
          {NAV_LINKS.map(([label, href]) => (
            <a key={href} href={href} className="transition-colors hover:text-[var(--text)]">
              {label}
            </a>
          ))}
        </nav>
        <span>{new Date().getFullYear()}</span>
      </footer>
    </section>
  );
}

/* -------------------------------------------------------------------------------------------- */

/** The public landing page at `/`: the product pitch, unauthenticated and self-demoing. */
export function LandingRoute() {
  return (
    <main className="mx-auto w-full max-w-5xl overflow-x-clip px-6">
      <LandingNav />
      <Hero />
      <HowItWorks />
      <WhatItWatches />
      <Faq />
      <FinalCta />
    </main>
  );
}

export default LandingRoute;
