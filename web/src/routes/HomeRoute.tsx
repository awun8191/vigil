import { useMemo, useState } from 'react';
import { RotateCw, X } from 'lucide-react';
import { BriefingHeader } from '@/components/home/BriefingHeader';
import { CardStack } from '@/components/home/CardStack';
import { EmptyState } from '@/components/home/EmptyState';
import { SkeletonCard } from '@/components/ui/Skeleton';
import { IconButton } from '@/components/ui/IconButton';
import { userMessageOf } from '@/lib/errors';
import { useCards } from '@/queries/cards';

const STALE_THRESHOLD_MS = 3 * 60_000;

export default function HomeRoute() {
  const { data, isLoading, isError, error, refetch, isFetching, dataUpdatedAt } = useCards();
  const [actionError, setActionError] = useState<string | null>(null);

  const cards = useMemo(
    () =>
      (data?.items ?? []).filter(
        (card) => card.status !== 'dismissed' && card.status !== 'done' && card.status !== 'expired',
      ),
    [data],
  );

  const ageMs = dataUpdatedAt ? Date.now() - dataUpdatedAt : 0;
  const staleLabel =
    dataUpdatedAt && ageMs > STALE_THRESHOLD_MS
      ? `Last updated ${Math.floor(ageMs / 60_000)}m ago`
      : null;

  const lastUpdated =
    dataUpdatedAt && ageMs < 60_000 ? 'Checked just now' : staleLabel ?? null;

  return (
    <main className="mx-auto w-full max-w-[680px] px-5 pt-16 pb-[calc(11rem+env(safe-area-inset-bottom))] md:pb-24">
      <BriefingHeader
        cardCount={cards.length}
        staleLabel={staleLabel}
        onRefresh={() => void refetch()}
        refreshing={isFetching}
      />

      {actionError ? (
        <div
          role="alert"
          className="mb-4 flex items-start gap-3 rounded-[var(--r-control)] border border-hairline bg-card px-3.5 py-2.5"
        >
          <p className="flex-1 text-[13px] text-ink-2">{actionError}</p>
          <IconButton label="Dismiss error" size="sm" onClick={() => setActionError(null)}>
            <X size={16} strokeWidth={1.5} aria-hidden="true" />
          </IconButton>
        </div>
      ) : null}

      {isLoading ? (
        <div className="space-y-3" aria-busy="true" aria-label="Loading briefing">
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </div>
      ) : isError ? (
        <div
          role="alert"
          className="flex items-center gap-3 rounded-[var(--r-card)] border border-hairline bg-card px-4 py-3.5"
        >
          <p className="flex-1 text-[14px] text-ink-2">
            Couldn't reach Vigil. {userMessageOf(error)}
          </p>
          <button
            type="button"
            onClick={() => void refetch()}
            className="inline-flex items-center gap-1.5 rounded-[var(--r-pill)] border border-hairline-strong px-3 py-1.5 text-[13px] font-medium text-ink-2 transition-colors duration-[var(--dur-fast)] hover:bg-hairline"
          >
            <RotateCw size={14} strokeWidth={1.5} aria-hidden="true" />
            Retry
          </button>
        </div>
      ) : cards.length === 0 ? (
        <EmptyState lastUpdated={lastUpdated} />
      ) : (
        <CardStack cards={cards} onError={setActionError} />
      )}
    </main>
  );
}
