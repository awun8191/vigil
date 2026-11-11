import type { HTMLAttributes, ReactNode } from 'react';
import { cn } from '@/lib/utils';

export type BubbleVariant = 'user' | 'assistant' | 'default' | 'muted';

/*
 * Both roles sit on the same soft, tactile surface. The user bubble is end-aligned with a
 * tighter bottom-right corner; Vigil's reply is start-aligned with a tighter bottom-left
 * corner. The asymmetry is the only directional cue, so the palette stays calm.
 */
const variants: Record<BubbleVariant, string> = {
  user: 'bubble-user rounded-[20px] rounded-br-[7px]',
  assistant: 'bubble-assistant rounded-[20px] rounded-bl-[7px]',
  default: 'bubble-user rounded-[20px] rounded-br-[7px]',
  muted:
    'rounded-[18px] rounded-tl-[6px] border border-[var(--border)] bg-[var(--panel-inset)]',
};

interface BubbleProps extends HTMLAttributes<HTMLDivElement> {
  variant?: BubbleVariant;
  children?: ReactNode;
}

export function Bubble({ variant = 'user', className, children, ...props }: BubbleProps) {
  return (
    <div
      className={cn(
        variants[variant],
        'min-w-0 max-w-full px-4 py-3 text-[14.5px] leading-[1.68] text-[var(--text)]',
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}

/**
 * Content wrapper inside a bubble: enforces word/identifier wrapping without
 * applying whitespace-pre-wrap globally (which breaks parsed Markdown block layouts).
 */
export function BubbleContent({ className, children, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn('min-w-0 max-w-full [overflow-wrap:anywhere]', className)} {...props}>
      {children}
    </div>
  );
}
