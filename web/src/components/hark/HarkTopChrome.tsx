import { Home, Search, UserRound } from 'lucide-react';
import { motion } from 'framer-motion';

interface Props {
  activeTab: 'chat' | 'projects';
  onTabChange: (tab: 'chat' | 'projects') => void;
  onHomeClick: () => void;
  onSearchClick: () => void;
  onProfileClick: () => void;
  leftRailMode: 'widgets' | 'clock' | 'collapsed';
}

const RAIL_LABEL: Record<Props['leftRailMode'], string> = {
  widgets: 'Panels',
  clock: 'Clock',
  collapsed: 'Hidden',
};

const TAB_SPRING = { type: 'spring' as const, stiffness: 500, damping: 35 };

export function HarkTopChrome({
  activeTab,
  onTabChange,
  onHomeClick,
  onSearchClick,
  onProfileClick,
  leftRailMode,
}: Props) {
  return (
    <header className="pointer-events-none fixed inset-x-0 top-0 z-40 grid grid-cols-[1fr_auto_1fr] items-start gap-2 px-4 py-4 sm:gap-3 sm:px-6 sm:py-5">
      <div className="pointer-events-auto flex items-center">
        <button
          type="button"
          onClick={onHomeClick}
          aria-label={`Change side panel. Current view: ${RAIL_LABEL[leftRailMode]}`}
          title={`Side panel: ${RAIL_LABEL[leftRailMode]}`}
          className="chrome-control flex h-10 w-10 items-center justify-center rounded-[14px] transition-transform duration-200 hover:scale-105 active:scale-95"
        >
          <Home size={18} strokeWidth={1.75} />
        </button>
      </div>

      <div className="pointer-events-auto justify-self-center">
        <div className="chrome-pill flex h-10 items-center rounded-full p-1">
          <button
            type="button"
            aria-pressed={activeTab === 'chat'}
            onClick={() => onTabChange('chat')}
            className="relative z-10 flex h-8 items-center rounded-full px-3 text-[12.5px] font-semibold transition-colors duration-200 sm:px-5 sm:text-[13px]"
          >
            {activeTab === 'chat' ? (
              <motion.span
                layoutId="hark-active-tab"
                transition={TAB_SPRING}
                className="absolute inset-0 rounded-full bg-[var(--chrome-active-bg)] shadow-sm"
              />
            ) : null}
            <span
              className={`relative z-10 ${
                activeTab === 'chat'
                  ? 'text-[var(--chrome-active-ink)]'
                  : 'text-[var(--chrome-inactive)] transition-colors hover:text-[var(--text)]'
              }`}
            >
              Chat
            </span>
          </button>

          <button
            type="button"
            aria-pressed={activeTab === 'projects'}
            onClick={() => onTabChange('projects')}
            className="relative z-10 flex h-8 items-center rounded-full px-3 text-[12.5px] font-semibold transition-colors duration-200 sm:px-5 sm:text-[13px]"
          >
            {activeTab === 'projects' ? (
              <motion.span
                layoutId="hark-active-tab"
                transition={TAB_SPRING}
                className="absolute inset-0 rounded-full bg-[var(--chrome-active-bg)] shadow-sm"
              />
            ) : null}
            <span
              className={`relative z-10 ${
                activeTab === 'projects'
                  ? 'text-[var(--chrome-active-ink)]'
                  : 'text-[var(--chrome-inactive)] transition-colors hover:text-[var(--text)]'
              }`}
            >
              Projects
            </span>
          </button>
        </div>
      </div>

      <div className="pointer-events-auto flex items-center justify-end gap-2 sm:gap-3">
        <button
          type="button"
          onClick={onSearchClick}
          className="chrome-control flex h-10 items-center gap-2 rounded-full px-3 text-[13px] font-medium transition-transform duration-200 hover:scale-105 active:scale-95 sm:px-4"
        >
          <Search size={15} strokeWidth={1.75} />
          <span className="hidden sm:inline">Search</span>
          <span className="sr-only sm:hidden">Search</span>
        </button>

        <button
          type="button"
          onClick={onProfileClick}
          aria-label="Profile and settings"
          className="chrome-control flex h-10 w-10 items-center justify-center overflow-hidden rounded-full transition-transform duration-200 hover:scale-105 active:scale-95"
        >
          <UserRound size={17} strokeWidth={1.5} />
        </button>
      </div>
    </header>
  );
}

export default HarkTopChrome;
