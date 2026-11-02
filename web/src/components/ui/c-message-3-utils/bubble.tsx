import type { HTMLAttributes, ReactNode } from 'react';
import { cn } from '@/lib/utils';

export type BubbleVariant = 'default' | 'muted';

/*
 * Alternating fills so the two parties read apart at a glance, both a wash of
 * the ink colour over the page (ties to either theme without new tokens). The
 * fillet corner shapes the card: a tighter corner on the speaker's side, soft
 * 18px elsewhere.
 */
const variants: Record<BubbleVariant, string> = {
  // Own messages: the stronger wash, shaped toward the bottom-right corner.
  default:
    'rounded-[18px] rounded-br-[6px] bg-[color-mix(in_srgb,var(--text)_18%,transparent)]',
  // The other party: the quieter wash, shaped toward the top-left corner.
  muted:
    'rounded-[18px] rounded-tl-[6px] bg-[color-mix(in_srgb,var(--text)_7%,transparent)]',
};

interface BubbleProps extends HTMLAttributes<HTMLDivElement> {
  variant?: BubbleVariant;
  children?: ReactNode;
}

export function Bubble({ variant = 'default', className, children, ...props }: BubbleProps) {
  return (
    <div
      className={cn(variants[variant], 'px-4 py-3 text-[15px] leading-relaxed', className)}
      {...props}
    >
      {children}
    </div>
  );
}

/** Text content inside a bubble: reserves wrapping without stretching the column. */
export function BubbleContent({ className, children, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn('whitespace-pre-wrap break-words', className)} {...props}>
      {children}
    </div>
  );
}
