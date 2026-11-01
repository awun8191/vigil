import { Home, MessageSquare, Settings } from 'lucide-react';
import { NavLink, useLocation } from 'react-router-dom';

interface DockItem {
  to: string;
  label: string;
  Icon: typeof Home;
  end: boolean;
}

const ITEMS: DockItem[] = [
  { to: '/', label: 'Home', Icon: Home, end: true },
  { to: '/chat', label: 'Chat', Icon: MessageSquare, end: false },
  { to: '/settings', label: 'Settings', Icon: Settings, end: false },
];

/**
 * The hovering side pill dock.
 *
 * Desktop: fixed to the left margin, vertically centred.
 * Mobile: bottom centre, stacked ABOVE the chat input pill. The two never share an offset, which
 * is what the first draft of the spec got wrong.
 */
export function FloatingDock() {
  const { pathname } = useLocation();
  // At most one backdrop-filter layer per viewport: the settings sheet owns the blur when open.
  const suppressBlur = pathname.startsWith('/settings');

  return (
    <nav
      aria-label="Primary"
      className={[
        'fixed z-40 flex items-center gap-1 rounded-[var(--r-pill)] border border-hairline',
        'bg-chrome p-1.5',
        suppressBlur ? '' : 'backdrop-blur-2xl',
        // Mobile: bottom centre, above the input pill.
        'left-1/2 -translate-x-1/2',
        'bottom-[calc(5.5rem+env(safe-area-inset-bottom))]',
        // Desktop: left margin, vertically centred.
        'md:left-6 md:translate-x-0 md:top-1/2 md:-translate-y-1/2 md:bottom-auto',
        'md:flex-col md:gap-1.5',
      ].join(' ')}
      style={{ boxShadow: 'var(--shadow-chrome)' }}
    >
      {ITEMS.map(({ to, label, Icon, end }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          aria-label={label}
          title={label}
          className={({ isActive }) =>
            [
              'relative grid h-11 w-11 place-items-center rounded-[var(--r-pill)]',
              'transition-[background-color,color] duration-[var(--dur-base)] ease-[var(--ease-spring)]',
              'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
              isActive
                ? 'bg-accent-soft text-accent'
                : 'text-ink-3 hover:bg-hairline hover:text-ink-2',
            ].join(' ')
          }
        >
          <Icon size={18} strokeWidth={1.5} aria-hidden="true" />
        </NavLink>
      ))}
    </nav>
  );
}
