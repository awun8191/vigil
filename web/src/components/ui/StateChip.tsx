import type { CardStatus, Urgency } from '@/types';

const STATUS_LABEL: Record<CardStatus, string> = {
  new: 'New',
  seen: 'Seen',
  in_progress: 'In progress',
  awaiting_approval: 'Awaiting approval',
  done: 'Done',
  dismissed: 'Dismissed',
  expired: 'Expired',
};

/**
 * Only `awaiting_approval` earns a filled accent chip. It is the single status where the user is
 * the blocker, so it is the only one worth calling attention to.
 */
export function StateChip({ status }: { status: CardStatus }) {
  const isBlocking = status === 'awaiting_approval';

  return (
    <span
      className={[
        'shrink-0 rounded-[var(--r-pill)] px-2 py-0.5 text-[11px] font-medium tracking-[0.04em] uppercase',
        isBlocking
          ? 'bg-accent-soft text-accent'
          : 'border border-hairline text-ink-3',
      ].join(' ')}
    >
      {STATUS_LABEL[status] ?? status}
    </span>
  );
}

/** Urgency is a rail plus text in the accessible name, never hue alone. */
export function UrgencyRail({ urgency }: { urgency: Urgency }) {
  const colour =
    urgency === 'critical' || urgency === 'high' ? 'bg-high' : 'bg-hairline-strong';

  return (
    <span
      aria-hidden="true"
      className={['absolute inset-y-3 left-0 w-[3px] rounded-full', colour].join(' ')}
    />
  );
}
