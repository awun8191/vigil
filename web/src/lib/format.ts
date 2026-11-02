import type { Card, Urgency } from '@/types';

const URGENCY_RANK: Record<Urgency, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
};

export function urgencyRank(urgency: Urgency): number {
  return URGENCY_RANK[urgency] ?? 2;
}

/** `in 2h`, `overdue 40m`, `in 3d`. Short, calm, no exclamation. */
export function relativeTime(iso: string | null, now: number = Date.now()): string {
  if (!iso) return '';
  const target = new Date(iso).getTime();
  if (Number.isNaN(target)) return '';

  const deltaMs = target - now;
  const overdue = deltaMs < 0;
  const abs = Math.abs(deltaMs);

  const minute = 60_000;
  const hour = 60 * minute;
  const day = 24 * hour;

  let magnitude: string;
  if (abs < minute) {
    magnitude = 'now';
  } else if (abs < hour) {
    magnitude = `${Math.round(abs / minute)}m`;
  } else if (abs < day) {
    magnitude = `${Math.round(abs / hour)}h`;
  } else if (abs < 7 * day) {
    magnitude = `${Math.round(abs / day)}d`;
  } else {
    return new Date(target).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }

  if (magnitude === 'now') return overdue ? 'just now' : 'in a moment';
  return overdue ? `overdue ${magnitude}` : `in ${magnitude}`;
}

export function absoluteTime(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleString(undefined, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

export function timeOfDayGreeting(date: Date = new Date()): string {
  const h = date.getHours();
  if (h < 5) return 'Late night';
  if (h < 12) return 'Good morning';
  if (h < 17) return 'Good afternoon';
  return 'Good evening';
}

export type CardGroupKey = 'today' | 'tomorrow' | 'later' | 'none';

export const CARD_GROUP_LABEL: Record<CardGroupKey, string> = {
  today: 'Today',
  tomorrow: 'Tomorrow',
  later: 'Later',
  none: 'No date',
};

export function groupForCard(card: Card, now: Date = new Date()): CardGroupKey {
  if (!card.due_at) return 'none';
  const due = new Date(card.due_at);
  if (Number.isNaN(due.getTime())) return 'none';

  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const dayMs = 86_400_000;
  const dueTime = due.getTime();

  if (dueTime < startOfToday + dayMs) return 'today';
  if (dueTime < startOfToday + 2 * dayMs) return 'tomorrow';
  return 'later';
}

/** Urgency first, then soonest due, then newest. Matches the spec ordering. */
export function compareCards(a: Card, b: Card): number {
  const byUrgency = urgencyRank(a.urgency) - urgencyRank(b.urgency);
  if (byUrgency !== 0) return byUrgency;

  const aDue = a.due_at ? new Date(a.due_at).getTime() : Number.POSITIVE_INFINITY;
  const bDue = b.due_at ? new Date(b.due_at).getTime() : Number.POSITIVE_INFINITY;
  if (aDue !== bDue) return aDue - bDue;

  return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
}

export function groupCards(cards: Card[], now: Date = new Date()): Array<{ key: CardGroupKey; cards: Card[] }> {
  const buckets: Record<CardGroupKey, Card[]> = { today: [], tomorrow: [], later: [], none: [] };

  for (const card of [...cards].sort(compareCards)) {
    buckets[groupForCard(card, now)].push(card);
  }

  const order: CardGroupKey[] = ['today', 'tomorrow', 'later', 'none'];
  return order
    .map((key) => ({ key, cards: buckets[key] }))
    .filter((group) => group.cards.length > 0);
}

export function platformDeviceName(): string {
  const ua = navigator.userAgent;
  if (/iPad|Tablet/i.test(ua)) return 'Tablet browser';
  if (/iPhone|Android.*Mobile/i.test(ua)) return 'Phone browser';
  if (/Macintosh/i.test(ua)) return 'Mac browser';
  if (/Windows/i.test(ua)) return 'Windows browser';
  if (/Linux/i.test(ua)) return 'Linux browser';
  return 'Web browser';
}

/** Formats raw keystrokes into `VIG-XXXXXX`. */
export function formatPairingCode(raw: string): string {
  const stripped = raw.toUpperCase().replace(/[^A-Z0-9]/g, '');
  const body = stripped.startsWith('VIG') ? stripped.slice(3) : stripped;
  const clipped = body.slice(0, 6);
  if (clipped.length === 0) return '';
  return `VIG-${clipped}`;
}
