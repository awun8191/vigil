import { ChevronRight, CreditCard, Globe, LogOut, Shield, Sliders, User, X } from 'lucide-react';
import { AnimatePresence, motion } from 'framer-motion';
import { useSession } from '@/store/useSession';

interface Props {
  open: boolean;
  onClose: () => void;
  onNavigate?: (section: string) => void;
  onSignOut?: () => void;
}

/** Every id here must be a real section in SettingsPage, or the link falls back to Appearance. */
const GROUPS = [
  [
    { id: 'profile', label: 'Profile', Icon: User },
    { id: 'connectors', label: 'Accounts & Vault', Icon: Globe },
    { id: 'usage', label: 'Usage', Icon: CreditCard },
  ],
  [
    { id: 'appearance', label: 'System Preferences', Icon: Sliders },
    { id: 'security', label: 'Security', Icon: Shield },
  ],
];

export function HarkProfileSheet({ open, onClose, onNavigate, onSignOut }: Props) {
  const device = useSession((state) => state.device);

  return (
    <AnimatePresence>
      {open ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-6">
          {/* Backdrop Blur */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
            className="fixed inset-0 bg-black/40 backdrop-blur-xl"
          />

          {/* Centered Floating Profile Sheet (Screenshot 7) */}
          <motion.div
            initial={{ opacity: 0, scale: 0.94, y: 16 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.94, y: 16 }}
            transition={{ type: 'spring', stiffness: 450, damping: 32 }}
            className="relative z-10 w-full max-w-sm rounded-[24px] border border-[var(--border-strong)] bg-[var(--surface-solid)] p-5 shadow-2xl"
          >
            {/* Identity header */}
            <div className="flex items-start justify-between gap-3 pb-4">
              <div className="flex min-w-0 items-center gap-3">
                <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-[var(--panel-inset)] text-[var(--text)]">
                  <User size={18} strokeWidth={1.6} />
                </span>
                <div className="min-w-0">
                  <div className="truncate text-[14px] font-semibold text-[var(--text)]">
                    {device?.user_display_name || 'Paired user'}
                  </div>
                  <div className="mt-0.5 truncate text-[11px] text-[var(--text-muted)]">
                    {device?.device_name || 'This device'} · Self-hosted
                  </div>
                </div>
              </div>
              <button
                type="button"
                onClick={onClose}
                aria-label="Close settings"
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[var(--panel-inset)] text-[var(--text-muted)] transition-colors hover:text-[var(--text)]"
              >
                <X size={15} />
              </button>
            </div>

            <div className="flex flex-col gap-2.5">
              {GROUPS.map((group, index) => (
                <div
                  key={index}
                  className="divide-y divide-[var(--border)] rounded-[18px] border border-[var(--border)] bg-[var(--panel-inset)] px-3.5"
                >
                  {group.map(({ id, label, Icon }) => (
                    <button
                      key={id}
                      type="button"
                      onClick={() => onNavigate?.(id)}
                      className="flex w-full items-center justify-between py-2.5 text-left text-[13px] text-[var(--text-secondary)] transition-colors hover:text-[var(--text)]"
                    >
                      <span className="flex items-center gap-2.5">
                        <Icon size={15} className="text-[var(--text-muted)]" />
                        {label}
                      </span>
                      <ChevronRight size={14} />
                    </button>
                  ))}
                </div>
              ))}

              {/* Sign Out Action */}
              <button
                type="button"
                onClick={onSignOut}
                className="flex w-full items-center gap-2 rounded-[16px] border border-[var(--border)] bg-[var(--panel-inset)] px-3.5 py-2.5 text-[13px] font-medium text-[var(--text-muted)] transition-colors hover:bg-[var(--panel-inset-strong)] hover:text-[var(--text)]"
              >
                <LogOut size={15} />
                <span>Sign Out</span>
              </button>
            </div>
          </motion.div>
        </div>
      ) : null}
    </AnimatePresence>
  );
}

export default HarkProfileSheet;
