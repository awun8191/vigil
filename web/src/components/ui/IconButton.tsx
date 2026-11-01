import type { ButtonHTMLAttributes, ReactNode } from 'react';

interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** Required: an icon-only control with no accessible name is invisible to a screen reader. */
  label: string;
  children: ReactNode;
  variant?: 'plain' | 'accent' | 'surface';
  size?: 'sm' | 'md';
}

const VARIANTS = {
  plain: 'text-ink-3 hover:bg-hairline hover:text-ink',
  accent: 'bg-accent text-inverse hover:bg-accent-hover',
  surface: 'border border-hairline-strong bg-raised text-ink-2 hover:text-ink',
} as const;

const SIZES = {
  sm: 'h-8 w-8',
  md: 'h-10 w-10',
} as const;

export function IconButton({
  label,
  children,
  variant = 'plain',
  size = 'md',
  className,
  ...rest
}: IconButtonProps) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      {...rest}
      className={[
        'grid place-items-center rounded-[var(--r-pill)]',
        'transition-[background-color,color,opacity] duration-[var(--dur-fast)] ease-[var(--ease-spring)]',
        'disabled:cursor-not-allowed disabled:opacity-40',
        SIZES[size],
        VARIANTS[variant],
        className ?? '',
      ].join(' ')}
    >
      {children}
    </button>
  );
}
