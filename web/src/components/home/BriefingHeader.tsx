function greeting(): string {
  const h = new Date().getHours();
  if (h < 12) return 'Good morning';
  if (h < 18) return 'Good afternoon';
  return 'Good evening';
}

interface Props {
  count: number;
  loaded: boolean;
}

export function BriefingHeader({ count, loaded }: Props) {
  const date = new Date().toLocaleDateString(undefined, {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
  });

  return (
    <header className="animate-rise relative pb-10 pt-24 md:pb-14 md:pt-32">
      <p className="text-[12px] font-medium uppercase tracking-[0.18em] text-[var(--text-muted)]">
        {date}
      </p>
      <h1 className="mt-3 max-w-3xl text-[clamp(2.3rem,5.5vw,3.75rem)] font-semibold leading-[1.04] tracking-[-0.03em] text-[var(--text)]">
        {greeting()}
      </h1>
      <p className="mt-3 max-w-sm text-[15px] leading-relaxed text-[var(--text-muted)] md:text-base">
        {!loaded
          ? 'Gathering your briefing...'
          : count > 0
            ? count === 1
              ? 'One thing is waiting for you below.'
              : `${count} things are waiting for you below.`
            : 'Nothing needs you right now.'}
      </p>
    </header>
  );
}

export function EmptyState() {
  return (
    <div className="animate-rise flex flex-col items-center py-20 text-center">
      <h2 className="text-xl font-semibold tracking-heading">You're all caught up.</h2>
      <p className="mt-2 max-w-xs text-[15px] leading-relaxed text-[var(--text-muted)]">
        Vigil is monitoring in the background.
      </p>
    </div>
  );
}

export default BriefingHeader;
