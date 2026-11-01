import type { ButtonHTMLAttributes, ReactNode } from 'react';

type Variant = 'primary' | 'ghost' | 'quiet';

interface PillProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  icon?: ReactNode;
  children: ReactNode;
}

const BASE =
  'inline-flex items-center justify-center gap-1.5 rounded-[var(--r-pill)] ' +
  'px-3.5 py-1.5 text-[13px] font-medium leading-none whitespace-nowrap ' +
  'transition-[background-color,color,opacity] duration-[var(--dur-base)] ease-[var(--ease-spring)] ' +
  'disabled:cursor-not-allowed disabled:opacity-45';

const VARIANTS: Record<Variant, string> = {
  primary: 'bg-accent text-inverse hover:bg-accent-hover',
  ghost: 'border border-hairline-strong text-ink-2 hover:bg-hairline hover:text-ink',
  quiet: 'text-ink-3 hover:bg-hairline hover:text-ink-2',
};

export function Pill({ variant = 'ghost', icon, children, className, ...rest }: PillProps) {
  return (
    <button
      type="button"
      {...rest}
      className={[BASE, VARIANTS[variant], className ?? ''].join(' ')}
    >
      {icon}
      {children}
    </button>
  );
}
