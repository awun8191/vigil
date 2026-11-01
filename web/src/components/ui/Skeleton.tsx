interface SkeletonProps {
  className?: string;
}

/** Loading placeholder. Pulses only when the user has not asked for reduced motion. */
export function Skeleton({ className }: SkeletonProps) {
  return (
    <div
      aria-hidden="true"
      className={[
        'rounded-[var(--r-inner)] bg-hairline-strong/40',
        'motion-safe:animate-pulse',
        className ?? '',
      ].join(' ')}
    />
  );
}

export function SkeletonCard() {
  return (
    <div className="rounded-[var(--r-card)] border border-hairline bg-card p-4">
      <div className="flex items-start gap-3">
        <Skeleton className="h-5 w-5 rounded-full" />
        <div className="flex-1 space-y-2">
          <Skeleton className="h-4 w-[70%]" />
          <Skeleton className="h-3 w-full" />
          <Skeleton className="h-3 w-[85%]" />
        </div>
      </div>
      <div className="mt-4 flex gap-2">
        <Skeleton className="h-7 w-28 rounded-[var(--r-pill)]" />
        <Skeleton className="h-7 w-20 rounded-[var(--r-pill)]" />
      </div>
    </div>
  );
}
