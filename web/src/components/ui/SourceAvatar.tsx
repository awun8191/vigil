import { Bell, CalendarDays, Mail, MessageCircle, ShieldAlert } from 'lucide-react';
import type { ComponentType } from 'react';

type IconType = ComponentType<{ size?: number; strokeWidth?: number }>;

/** What a card came in through: the icon and plain name for its little circular avatar. */
export interface CardSource {
  Icon: IconType;
  name: string;
}

const SOURCE_MAP: Record<string, CardSource> = {
  email: { Icon: Mail, name: 'Email' },
  calendar: { Icon: CalendarDays, name: 'Calendar' },
  chat: { Icon: MessageCircle, name: 'Chat' },
  alert: { Icon: ShieldAlert, name: 'Vigil' },
};

const FALLBACK: CardSource = { Icon: Bell, name: 'Vigil' };

/** Resolve the circular avatar for a card type; unknown types read as Vigil itself. */
export function sourceFor(type: string | null | undefined): CardSource {
  if (!type) return FALLBACK;
  return SOURCE_MAP[type] ?? FALLBACK;
}

interface Props {
  size?: number;
  className?: string;
  source: CardSource;
}

/**
 * The round black-and-white avatar every notification card leads with, the same visual weight as
 * an app badge on a phone. It carries the source, not the status.
 */
export function SourceAvatar({ size = 32, className, source }: Props) {
  return (
    <span
      aria-hidden="true"
      className={className}
      style={{
        width: size,
        height: size,
        borderRadius: '50%',
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        flexShrink: 0,
        color: 'var(--text)',
        background: 'color-mix(in srgb, var(--text) 10%, transparent)',
        border: '1px solid color-mix(in srgb, var(--text) 14%, transparent)',
      }}
    >
      <source.Icon size={Math.round(size * 0.5)} strokeWidth={1.5} />
    </span>
  );
}

export default SourceAvatar;
