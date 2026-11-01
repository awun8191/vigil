import { SparkMark } from '@/components/layout/SparkMark';

interface EmptyStateProps {
  lastUpdated: string | null;
}

export function EmptyState({ lastUpdated }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-24 text-center">
      <SparkMark size={22} className="text-ink-3/60" />
      <p className="mt-4 text-[15px] text-ink-2">You're all caught up. Vigil is still watching.</p>
      {lastUpdated ? <p className="mt-1.5 text-[13px] text-ink-3">{lastUpdated}</p> : null}
    </div>
  );
}
