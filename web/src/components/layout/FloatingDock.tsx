import { Home, MessageSquare, Settings } from 'lucide-react';
import { motion } from 'framer-motion';
import type { ComponentType } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

type IconType = ComponentType<{ size?: number; strokeWidth?: number }>;

interface DockItem {
  id: 'home' | 'chat' | 'settings';
  label: string;
  to: string;
  Icon: IconType;
}

const items: DockItem[] = [
  { id: 'home', label: 'Home', to: '/agent', Icon: Home },
  { id: 'chat', label: 'Chat', to: '/agent/chat', Icon: MessageSquare },
  { id: 'settings', label: 'Settings', to: '/agent/settings', Icon: Settings },
];

const spring = { type: 'spring' as const, stiffness: 400, damping: 30 };

/** Hovering glass pill. Desktop sits in the left margin, mobile along the bottom. */
export function FloatingDock() {
  const { pathname } = useLocation();
  const navigate = useNavigate();

  const activeId: DockItem['id'] = pathname.startsWith('/agent/settings')
    ? 'settings'
    : pathname.startsWith('/agent/chat')
      ? 'chat'
      : 'home';

  return (
    <nav
      aria-label="Primary"
      className="glass fixed bottom-6 left-1/2 z-40 flex -translate-x-1/2 flex-row items-center gap-1 rounded-2xl p-1.5 md:bottom-auto md:left-6 md:top-1/2 md:-translate-y-1/2 md:translate-x-0 md:flex-col"
      style={{ paddingBottom: 'max(0.375rem, env(safe-area-inset-bottom))' }}
    >
      {items.map(({ id, label, to, Icon }) => {
        const active = activeId === id;
        return (
          <button
            key={id}
            type="button"
            onClick={() => navigate(to)}
            aria-label={label}
            aria-current={active ? 'page' : undefined}
            title={label}
            className="group relative flex h-11 w-11 items-center justify-center rounded-full outline-none transition-colors"
            style={{ color: active ? 'var(--text)' : 'var(--text-muted)' }}
          >
            {active && (
              <motion.span
                layoutId="dock-active"
                transition={spring}
                className="absolute inset-0 rounded-full"
                style={{
                  background: 'color-mix(in srgb, var(--text) 12%, transparent)',
                }}
              />
            )}
            <motion.span
              className="relative"
              whileTap={{ scale: 0.88 }}
              whileHover={{ scale: 1.08 }}
              transition={spring}
            >
              <Icon size={18} strokeWidth={1.5} />
            </motion.span>
          </button>
        );
      })}
    </nav>
  );
}

export default FloatingDock;
