import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ArrowUpRight,
  CalendarDays,
  Check,
  Clock3,
  Gauge,
  LayoutGrid,
  Plus,
  ShieldCheck,
  Sparkles,
} from 'lucide-react';

import { compareCards, relativeTime } from '@/lib/format';
import { useCards } from '@/queries/cards';
import { useConnectors, useLimits } from '@/queries/settings';
import type { Card } from '@/types';

interface Props {
  mode: 'widgets' | 'clock' | 'collapsed';
}

type PanelId = 'today' | 'usage' | 'agenda' | 'waiting';

const PANEL_KEY = 'vigil.rail.panels';
const DEFAULT_PANELS: PanelId[] = ['today', 'usage', 'agenda'];
const ALL_PANELS: PanelId[] = ['today', 'usage', 'agenda', 'waiting'];

const PANEL_META: Record<PanelId, { label: string; icon: typeof Clock3 }> = {
  today: { label: 'Today', icon: Clock3 },
  usage: { label: 'Usage', icon: Gauge },
  agenda: { label: 'Calendar', icon: CalendarDays },
  waiting: { label: 'Needs you', icon: ShieldCheck },
};

function readPanels(): PanelId[] {
  try {
    const raw = window.localStorage.getItem(PANEL_KEY);
    if (!raw) return DEFAULT_PANELS;
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return DEFAULT_PANELS;
    const valid = parsed.filter((value): value is PanelId =>
      ALL_PANELS.includes(value as PanelId),
    );
    return valid.length > 0 ? valid : DEFAULT_PANELS;
  } catch {
    return DEFAULT_PANELS;
  }
}

function useMinuteClock(): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 30_000);
    return () => window.clearInterval(timer);
  }, []);
  return now;
}

function PanelHeader({
  icon,
  title,
  trailing,
}: {
  icon: ReactNode;
  title: string;
  trailing?: ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-2">
      <div className="flex min-w-0 items-center gap-2 text-[13px] font-semibold text-[var(--text)]">
        <span className="text-[var(--text-muted)]">{icon}</span>
        <span className="truncate">{title}</span>
      </div>
      {trailing}
    </div>
  );
}

function RailButton({
  children,
  onClick,
}: {
  children: ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="mt-3 inline-flex h-9 w-full items-center justify-center gap-1.5 rounded-full bg-[var(--btn-primary-bg)] px-4 text-[12.5px] font-semibold text-[var(--btn-primary-ink)] transition-transform duration-200 hover:scale-[1.02] active:scale-[0.98]"
    >
      {children}
    </button>
  );
}

function Metric({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="min-w-0 rounded-[13px] bg-[var(--panel-inset)] px-2.5 py-2">
      <div className="truncate text-[10.5px] text-[var(--text-muted)]">{label}</div>
      <div className="mt-0.5 truncate text-[17px] font-semibold tracking-[-0.02em] tabular text-[var(--text)]">
        {value}
      </div>
    </div>
  );
}

/** A compact version of Hark's proactivity observation pill, sized for the rail. */
function ProactivityRow({ card, onClick }: { card: Card; onClick: () => void }) {
  const verb =
    card.verb?.trim() ||
    card.actions[0]?.verb?.trim() ||
    (card.actions[0]?.action_type === 'reply'
      ? 'Draft'
      : card.actions[0]?.action_type === 'dismiss'
        ? 'Clear'
        : 'Review');

  return (
    <button
      type="button"
      onClick={onClick}
      className="group flex w-full items-center gap-2 rounded-full border border-[var(--border)] bg-[var(--panel-inset)] p-[5px] pr-3 text-left transition-colors hover:border-[var(--border-strong)] hover:bg-[var(--panel-inset-strong)]"
    >
      <span className="inline-flex h-7 shrink-0 items-center rounded-full bg-[var(--verb-pill-bg)] px-3 text-[11px] font-semibold text-[var(--verb-pill-text)]">
        {verb}
      </span>
      <span className="min-w-0 flex-1 truncate text-[12px] leading-snug text-[var(--text-secondary)] group-hover:text-[var(--text)]">
        {card.title}
      </span>
      <span
        aria-hidden="true"
        className="shrink-0 font-mono text-[13px] font-light text-[var(--text-muted)]"
      >
        ⌝
      </span>
    </button>
  );
}

export function HarkLeftRail({ mode }: Props) {
  const navigate = useNavigate();
  const now = useMinuteClock();
  const connectors = useConnectors();
  const limits = useLimits();
  const cardsQuery = useCards();

  const [panels, setPanels] = useState<PanelId[]>(readPanels);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    try {
      window.localStorage.setItem(PANEL_KEY, JSON.stringify(panels));
    } catch {
      // Panel choice is a convenience, not a requirement.
    }
  }, [panels]);

  useEffect(() => {
    if (!menuOpen) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setMenuOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMenuOpen(false);
    };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [menuOpen]);

  const activeCards = useMemo(
    () =>
      (cardsQuery.data?.items ?? []).filter(
        (card) => !['done', 'dismissed', 'expired'].includes(card.status),
      ),
    [cardsQuery.data],
  );

  const waitingCards = useMemo(
    () =>
      [...activeCards]
        .filter((card) => card.status !== 'seen')
        .sort(compareCards)
        .slice(0, 3),
    [activeCards],
  );

  const agendaCards = useMemo(
    () =>
      [...activeCards]
        .filter((card) => card.type === 'calendar' && Boolean(card.due_at))
        .sort(
          (a, b) =>
            new Date(a.due_at as string).getTime() - new Date(b.due_at as string).getTime(),
        )
        .slice(0, 2),
    [activeCards],
  );

  const dueSoon = activeCards.filter((card) => {
    if (!card.due_at) return false;
    const due = new Date(card.due_at).getTime();
    return due >= now.getTime() && due <= now.getTime() + 24 * 60 * 60 * 1000;
  }).length;

  const waitingCount = activeCards.filter(
    (card) => card.status === 'awaiting_approval' || card.status === 'new',
  ).length;

  const accountCount = connectors.data?.length ?? 0;

  if (mode === 'collapsed') return null;

  if (mode === 'clock') {
    return (
      <aside
        aria-label="Current time"
        className="fixed left-6 top-24 z-30 hidden select-none md:block"
      >
        <div className="text-[36px] font-light leading-[1.14] tracking-[-0.03em] text-[var(--text)]">
          <div>{now.toLocaleDateString(undefined, { weekday: 'short' })}</div>
          <div>{now.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}</div>
          <div className="mt-1 font-normal tabular">
            {now.toLocaleTimeString(undefined, {
              hour: 'numeric',
              minute: '2-digit',
              hour12: true,
            })}
          </div>
        </div>
      </aside>
    );
  }

  const togglePanel = (panel: PanelId) => {
    setPanels((current) =>
      current.includes(panel) ? current.filter((id) => id !== panel) : [...current, panel],
    );
  };

  const revealWaiting = () => {
    setPanels((current) => (current.includes('waiting') ? current : [...current, 'waiting']));
  };

  const openCard = (card: Card) => {
    navigate('/agent/chat', {
      state: { seed: `Tell me about: ${card.title}`, cardId: card.id },
    });
  };

  return (
    <aside
      aria-label="Vigil panels"
      className="scroll-soft fixed left-4 top-[76px] z-30 flex max-h-[calc(100dvh-11rem)] w-[300px] max-w-[calc(100vw-2rem)] flex-col gap-3 overflow-y-auto pb-2 pr-1 md:left-6 md:top-24 md:max-h-[calc(100dvh-8rem)]"
    >
      {panels.includes('today') ? (
        <section className="rail-card p-4">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="truncate text-[11.5px] font-medium uppercase tracking-[0.09em] text-[var(--text-muted)]">
                {now.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })}
              </p>
              <p className="mt-1.5 text-[30px] font-medium leading-none tracking-[-0.035em] tabular text-[var(--text)]">
                {now.toLocaleTimeString(undefined, {
                  hour: 'numeric',
                  minute: '2-digit',
                  hour12: true,
                })}
              </p>
            </div>
            <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-[var(--panel-inset)] text-[var(--text-muted)]">
              <Clock3 size={16} strokeWidth={1.5} />
            </span>
          </div>

          <div className="mt-3.5 grid grid-cols-3 gap-1.5">
            <Metric label="Waiting" value={waitingCount} />
            <Metric label="Due soon" value={dueSoon} />
            <Metric label="Watching" value={accountCount} />
          </div>

          <p className="mt-2.5 text-[11px] leading-relaxed text-[var(--text-muted)]">
            Based on this device&apos;s local time.
          </p>

          <RailButton onClick={waitingCount > 0 ? revealWaiting : () => navigate('/agent/chat')}>
            {waitingCount > 0 ? 'Review what needs you' : 'Ask Vigil'}
            <ArrowUpRight size={13} strokeWidth={1.75} />
          </RailButton>
        </section>
      ) : null}

      {panels.includes('usage') ? (
        <section className="rail-card p-4">
          <PanelHeader
            icon={<Gauge size={15} strokeWidth={1.6} />}
            title="Vigil usage"
            trailing={
              <span className="rounded-full bg-[var(--panel-inset)] px-2 py-0.5 text-[10px] font-medium text-[var(--text-muted)]">
                Self-hosted
              </span>
            }
          />

          <div className="rail-inset mt-3 rounded-[14px] p-3">
            <p className="text-[12px] leading-relaxed text-[var(--text-secondary)]">
              No plan limit. The backend enforces the run budget below.
            </p>
            <dl className="mt-2.5 space-y-1.5">
              {[
                ['Steps / run', limits.data ? String(limits.data.max_steps_per_run) : '—'],
                [
                  'Cost / run',
                  limits.data ? `$${limits.data.max_cost_usd_per_run.toFixed(2)}` : '—',
                ],
                [
                  'Timeout',
                  limits.data ? `${Math.round(limits.data.run_timeout_seconds / 60)} min` : '—',
                ],
              ].map(([label, value]) => (
                <div key={label} className="flex items-center justify-between gap-3 text-[11.5px]">
                  <dt className="text-[var(--text-muted)]">{label}</dt>
                  <dd className="font-medium tabular text-[var(--text)]">{value}</dd>
                </div>
              ))}
            </dl>
          </div>

          <RailButton onClick={() => navigate('/agent/settings/usage')}>Manage limits</RailButton>
        </section>
      ) : null}

      {panels.includes('agenda') ? (
        <section className="rail-card p-4">
          <PanelHeader
            icon={<CalendarDays size={15} strokeWidth={1.6} />}
            title="Calendar"
          />

          {agendaCards.length > 0 ? (
            <ul className="mt-3 space-y-1.5">
              {agendaCards.map((card) => (
                <li key={card.id}>
                  <button
                    type="button"
                    onClick={() => openCard(card)}
                    className="flex w-full items-center gap-2.5 rounded-[12px] px-2 py-2 text-left transition-colors hover:bg-[var(--panel-inset)]"
                  >
                    <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--text)]" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[12.5px] font-medium text-[var(--text)]">
                        {card.title}
                      </span>
                      <span className="block truncate text-[11px] text-[var(--text-muted)]">
                        {relativeTime(card.due_at, now.getTime())}
                      </span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <div className="rail-inset mt-3 rounded-[14px] px-3 py-4 text-center">
              <Sparkles size={16} strokeWidth={1.5} className="mx-auto text-[var(--text-muted)]" />
              <p className="mt-2 text-[12px] leading-relaxed text-[var(--text-secondary)]">
                Nothing on the calendar. Vigil will surface what matters.
              </p>
            </div>
          )}

          <RailButton onClick={() => navigate('/agent/settings/connectors')}>
            {accountCount > 0 ? 'Manage calendar' : 'Connect calendar'}
          </RailButton>
        </section>
      ) : null}

      {panels.includes('waiting') && waitingCards.length > 0 ? (
        <section className="rail-card p-4">
          <PanelHeader
            icon={<ShieldCheck size={15} strokeWidth={1.6} />}
            title="Needs you"
            trailing={
              <span className="rounded-full bg-[var(--panel-inset)] px-2 py-0.5 text-[10px] font-semibold tabular text-[var(--text)]">
                {waitingCards.length}
              </span>
            }
          />
          <div className="mt-3 space-y-1.5">
            {waitingCards.map((card) => (
              <ProactivityRow key={card.id} card={card} onClick={() => openCard(card)} />
            ))}
          </div>
        </section>
      ) : null}

      <div ref={menuRef} className="relative">
        <button
          type="button"
          onClick={() => setMenuOpen((open) => !open)}
          aria-expanded={menuOpen}
          aria-haspopup="menu"
          className="rail-card flex h-11 w-full items-center justify-center gap-2 rounded-full px-4 text-[13px] font-medium text-[var(--text)] transition-transform duration-200 hover:scale-[1.01] active:scale-[0.99]"
        >
          <Plus size={15} strokeWidth={1.75} />
          Create a new panel
        </button>

        {menuOpen ? (
          <div
            role="menu"
            aria-label="Choose side panels"
            className="chrome-pill absolute bottom-full left-0 right-0 z-40 mb-2 overflow-hidden rounded-[18px] p-1.5"
          >
            {ALL_PANELS.map((panel) => {
              const meta = PANEL_META[panel];
              const Icon = meta.icon;
              const selected = panels.includes(panel);
              return (
                <button
                  key={panel}
                  type="button"
                  role="menuitemcheckbox"
                  aria-checked={selected}
                  onClick={() => togglePanel(panel)}
                  className="flex w-full items-center justify-between gap-2 rounded-[13px] px-3 py-2 text-left text-[12.5px] text-[var(--text-secondary)] transition-colors hover:bg-[var(--panel-inset)] hover:text-[var(--text)]"
                >
                  <span className="flex items-center gap-2.5">
                    <Icon size={14} strokeWidth={1.6} className="text-[var(--text-muted)]" />
                    {meta.label}
                  </span>
                  {selected ? (
                    <Check size={13} strokeWidth={2} className="text-[var(--text)]" />
                  ) : (
                    <span className="h-3 w-3" />
                  )}
                </button>
              );
            })}
            <div className="mt-1 flex items-center justify-between border-t border-[var(--border)] px-3 py-1.5 text-[10.5px] text-[var(--text-muted)]">
              <span className="inline-flex items-center gap-1.5">
                <LayoutGrid size={11} strokeWidth={1.6} />
                Panels save on this device
              </span>
              <button
                type="button"
                onClick={() => setMenuOpen(false)}
                className="font-medium text-[var(--text)]"
              >
                Done
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </aside>
  );
}

export default HarkLeftRail;
