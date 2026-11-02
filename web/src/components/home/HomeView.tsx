import { useState } from 'react';
import { AnimatePresence } from 'framer-motion';
import { RotateCw, X } from 'lucide-react';
import BriefingHeader, { EmptyState } from './BriefingHeader';
import TaskCard from './TaskCard';
import WidgetsRail from './WidgetsRail';
import { userMessageOf } from '@/lib/errors';
import { useCards } from '@/queries/cards';

export function HomeView() {
  const { data, isLoading, isError, error, refetch, isFetching } = useCards();
  const [actionError, setActionError] = useState<string | null>(null);

  const cards = (data?.items ?? []).filter(
    (card) => card.status !== 'dismissed' && card.status !== 'done' && card.status !== 'expired',
  );

  const loaded = !isLoading;

  return (
    <main className="relative mx-auto w-full max-w-5xl overflow-x-clip px-6 pb-36 md:pb-24 md:pl-24">
      <BriefingHeader count={cards.length} loaded={loaded} />

      {actionError ? (
        <div
          role="alert"
          className="card mb-4 flex items-start gap-3 rounded-[10px] px-4 py-3"
        >
          <p className="flex-1 text-[13px] leading-relaxed text-[var(--text-muted)]">{actionError}</p>
          <button
            type="button"
            aria-label="Dismiss error"
            onClick={() => setActionError(null)}
            className="grid h-7 w-7 shrink-0 place-items-center rounded-full text-[var(--text-muted)] transition-colors hover:bg-[color-mix(in_srgb,var(--text)_8%,transparent)] hover:text-[var(--text)]"
          >
            <X size={14} strokeWidth={1.5} />
          </button>
        </div>
      ) : null}

      {isError ? (
        <div
          role="alert"
          className="card flex items-center gap-3 rounded-[10px] px-4 py-3.5"
        >
          <p className="flex-1 text-[14px] leading-relaxed text-[var(--text-muted)]">
            Couldn't reach Vigil. {userMessageOf(error)}
          </p>
          <button type="button" onClick={() => void refetch()} className="btn btn-ghost">
            <RotateCw size={14} strokeWidth={1.5} className={isFetching ? 'animate-spin' : ''} />
            Retry
          </button>
        </div>
      ) : (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_300px] lg:items-start">
          <div className="min-w-0">
            {loaded && cards.length === 0 ? <EmptyState /> : null}

            <div className="flex flex-col gap-4">
              {isLoading ? (
                <>
                  <div className="card h-44 animate-pulse rounded-[14px]" />
                  <div className="card h-40 animate-pulse rounded-[14px]" />
                </>
              ) : (
                <AnimatePresence mode="popLayout">
                  {cards.map((card, i) => (
                    <TaskCard key={card.id} card={card} index={i} onError={setActionError} />
                  ))}
                </AnimatePresence>
              )}
            </div>
          </div>

          <div className="lg:sticky lg:top-8">
            <WidgetsRail />
          </div>
        </div>
      )}
    </main>
  );
}

export default HomeView;
