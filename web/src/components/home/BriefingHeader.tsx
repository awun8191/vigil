import { SparkMark } from '../layout/SparkMark';

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
    <header className="animate-rise relative pb-12 pt-28 text-center md:pb-16 md:pt-36">
      <p className="text-[13px] font-medium uppercase tracking-[0.16em] text-[var(--text-muted)]">
        {date}
      </p>
      <h1
        className="mx-auto mt-5 max-w-4xl bg-clip-text pb-2 text-[clamp(2.9rem,8.6vw,6rem)] font-semibold leading-[0.98] tracking-[-0.045em] text-transparent"
        style={{
          backgroundImage:
            'linear-gradient(180deg, var(--text) 35%, color-mix(in srgb, var(--text) 52%, transparent))',
        }}
      >
        {greeting()}.
      </h1>
      <p className="mx-auto mt-5 max-w-md text-base leading-relaxed text-[var(--text-muted)] md:text-[17px]">
        {!loaded
          ? 'Gathering your briefing...'
          : count > 0
            ? `${count} ${count === 1 ? 'thing is' : 'things are'} ready for you. Everything else is handled.`
            : 'Nothing needs your attention right now.'}
      </p>
    </header>
  );
}

export function EmptyState() {
  return (
    <div className="animate-rise flex flex-col items-center py-20 text-center">
      <div
        className="flex h-16 w-16 items-center justify-center rounded-full"
        style={{
          background: 'color-mix(in srgb, var(--accent) 12%, transparent)',
          boxShadow: '0 0 48px color-mix(in srgb, var(--accent) 28%, transparent)',
          color: 'var(--accent)',
        }}
      >
        <SparkMark size={22} />
      </div>
      <h2 className="mt-6 text-xl font-semibold tracking-heading">You're all caught up.</h2>
      <p className="mt-2 max-w-xs text-[15px] leading-relaxed text-[var(--text-muted)]">
        Vigil is monitoring in the background.
      </p>
    </div>
  );
}

export default BriefingHeader;
