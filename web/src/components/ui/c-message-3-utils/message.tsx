import type { HTMLAttributes, ReactNode } from 'react';
import { cn } from '@/lib/utils';

/*
 * Message primitives adapted from the c-message-3 pattern: a row that places
 * a single content column at one side, with a header slot that stays where the
 * content starts whatever the row does, and a footer slot that follows the row
 * to the end. Both inherit the bubble's horizontal padding.
 */

export type MessageAlign = 'start' | 'end';

interface MessageProps extends HTMLAttributes<HTMLDivElement> {
  align?: MessageAlign;
  children: ReactNode;
}

export function Message({ align = 'start', className, children, ...props }: MessageProps) {
  return (
    <div
      className={cn('flex w-full', align === 'end' ? 'justify-end' : 'justify-start', className)}
      {...props}
    >
      {children}
    </div>
  );
}

/** The content column: as wide as its widest slot, capped for long bubbles. */
export function MessageContent({ className, children, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn('flex min-w-0 max-w-[85%] flex-col gap-1.5', className)} {...props}>
      {children}
    </div>
  );
}

/**
 * Announces who sent what and when. Sits where the content starts, so it lines
 * up with the bubble's leading edge even while the row itself is end-aligned.
 */
export function MessageHeader({ className, children, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn('flex items-center gap-1.5 text-[13px] font-medium text-[var(--text-muted)]', className)}
      {...props}
    >
      {children}
    </div>
  );
}

/**
 * Meta below the bubble, such as delivery state or send time. It inherits the
 * content column's width, so it hugs whatever edge the bubble was placed on.
 */
export function MessageFooter({ className, children, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        'flex items-center justify-start gap-1.5 text-[12px] text-[var(--text-muted)]',
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}

/** Decorative separator dot for metadata rows. Not read aloud with the text. */
export function MetaDot({ className }: { className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={cn('size-1 shrink-0 rounded-full bg-[color-mix(in_srgb,var(--text)_40%,transparent)]', className)}
    />
  );
}
