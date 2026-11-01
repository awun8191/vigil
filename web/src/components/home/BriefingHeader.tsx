import { timeOfDayGreeting } from '@/lib/format';

interface BriefingHeaderProps {
  cardCount: number;
  staleLabel: string | null;
  onRefresh: () => void;
  refreshing: boolean;
}

export function BriefingHeader({
  cardCount,
  staleLabel,
  onRefresh,
  refreshing,
}: BriefingHeaderProps) {
  const watching =
    cardCount === 0
      ? 'Nothing needs you right now.'
      : cardCount === 1
        ? 'One thing is waiting on you.'
        : `${cardCount} things are waiting on you.`;

  return (
    <header className="mb-8">
      <h1 className="text-[clamp(1.75rem,1.2rem+2vw,2.5rem)] font-semibold leading-[1.15] tracking-[-0.02em] text-balance">
        {timeOfDayGreeting()}.
      </h1>
      <p className="mt-1.5 text-[15px] text-ink-3">{watching}</p>

      {staleLabel ? (
        <button
          type="button"
          onClick={onRefresh}
          disabled={refreshing}
          className="mt-3 rounded-control text-[13px] text-ink-3 underline decoration-hairline-strong underline-offset-4 transition-colors duration-[var(--dur-fast)] hover:text-ink-2 disabled:opacity-50"
        >
          {refreshing ? 'Refreshing...' : staleLabel}
        </button>
      ) : null}
    </header>
  );
}
