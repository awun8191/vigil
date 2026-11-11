import { useEffect, useRef } from 'react';
import { Search, X } from 'lucide-react';
import { AnimatePresence, motion } from 'framer-motion';

interface Props {
  open: boolean;
  onClose: () => void;
  onSearch?: (query: string) => void;
}

export function HarkSearchModal({ open, onClose, onSearch }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [open]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        if (open) onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [open, onClose]);

  return (
    <AnimatePresence>
      {open ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-6">
          {/* Heavy Backdrop Blur */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
            className="fixed inset-0 bg-black/40 backdrop-blur-2xl"
          />

          <button
            type="button"
            onClick={onClose}
            aria-label="Close search"
            className="hark-card fixed left-6 top-4 z-20 grid h-10 w-10 place-items-center rounded-full text-[var(--hark-card-fg)] transition-transform hover:scale-105 active:scale-95"
          >
            <X size={17} strokeWidth={1.75} />
          </button>

          {/* Centered Floating Search Pill (Screenshot 6) */}
          <motion.div
            initial={{ opacity: 0, scale: 0.95, y: -10 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: -10 }}
            transition={{ type: 'spring', stiffness: 500, damping: 35 }}
            className="relative z-10 w-full max-w-lg"
          >
            <div className="flex h-12 items-center gap-3 rounded-full bg-[var(--surface-solid)] border border-[var(--border-strong)] px-4 shadow-2xl">
              <Search size={18} className="text-[var(--text-muted)]" />
              <input
                ref={inputRef}
                type="text"
                placeholder="Search"
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    onSearch?.(e.currentTarget.value);
                    onClose();
                  }
                }}
                className="w-full bg-transparent text-[15px] font-normal text-[var(--text)] placeholder-[var(--text-muted)] outline-none"
              />
              <button
                type="button"
                onClick={onClose}
                className="rounded-full p-1 text-[var(--text-muted)] hover:text-[var(--text)]"
              >
                <X size={16} />
              </button>
            </div>
          </motion.div>
        </div>
      ) : null}
    </AnimatePresence>
  );
}

export default HarkSearchModal;
