import { NavLink, Navigate, useNavigate, useParams } from 'react-router-dom';
import {
  Bell,
  Brain,
  CircleUser,
  Eye,
  Gauge,
  Mic,
  Plug,
  ShieldCheck,
  SlidersHorizontal,
  Sun,
  User,
  UserRound,
  X,
} from 'lucide-react';
import { motion } from 'framer-motion';
import { useSession } from '@/store/useSession';
import type { ComponentType, ReactNode } from 'react';
import {
  AccountSection,
  AppearanceSection,
  ConnectorsSection,
  MemorySection,
  NotificationsSection,
  PersonalizationSection,
  ProfileSection,
  SecuritySection,
  UsageSection,
  VoiceSection,
  WatchingSection,
} from './sections';

type IconType = ComponentType<{ size?: number; strokeWidth?: number }>;

interface SectionDef {
  id: string;
  label: string;
  Icon: IconType;
  body: ReactNode;
}

const SECTIONS: SectionDef[] = [
  { id: 'appearance', label: 'Appearance', Icon: Sun, body: <AppearanceSection /> },
  { id: 'notifications', label: 'Notifications', Icon: Bell, body: <NotificationsSection /> },
  { id: 'profile', label: 'Profile', Icon: UserRound, body: <ProfileSection /> },
  { id: 'security', label: 'Security & login', Icon: ShieldCheck, body: <SecuritySection /> },
  { id: 'account', label: 'Account', Icon: CircleUser, body: <AccountSection /> },
  { id: 'connectors', label: 'Connectors', Icon: Plug, body: <ConnectorsSection /> },
  { id: 'usage', label: 'Usage', Icon: Gauge, body: <UsageSection /> },
  {
    id: 'personalization',
    label: 'Personalization',
    Icon: SlidersHorizontal,
    body: <PersonalizationSection />,
  },
  { id: 'memory', label: 'Memory', Icon: Brain, body: <MemorySection /> },
  { id: 'watching', label: 'Watching', Icon: Eye, body: <WatchingSection /> },
  { id: 'voice', label: 'Voice', Icon: Mic, body: <VoiceSection /> },
];

const SECTION_IDS = new Set(SECTIONS.map((section) => section.id));

/** The full settings page: section rail on the left, one panel on the right. */
export function SettingsPage() {
  const { section } = useParams();
  const navigate = useNavigate();
  const device = useSession((state) => state.device);

  if (!section || !SECTION_IDS.has(section)) {
    return <Navigate to="/agent/settings/appearance" replace />;
  }

  const current = SECTIONS.find((entry) => entry.id === section);
  if (!current) {
    return <Navigate to="/agent/settings/appearance" replace />;
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 md:p-6">
      {/* Same backdrop and surface language as the profile sheet, so the two read as one thing */}
      <div
        onClick={() => navigate('/agent')}
        className="fixed inset-0 bg-black/40 backdrop-blur-xl"
        aria-hidden="true"
      />

      <motion.div
        role="dialog"
        aria-label="Settings"
        initial={{ opacity: 0, scale: 0.96, y: 16 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        transition={{ type: 'spring', stiffness: 450, damping: 32 }}
        className="relative z-10 flex h-[min(760px,100%)] w-full max-w-4xl flex-col overflow-hidden rounded-[24px] border border-[var(--border-strong)] bg-[var(--surface-solid)] shadow-2xl"
      >
        <header className="flex items-center justify-between gap-3 border-b border-[var(--border)] px-5 py-4">
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
            onClick={() => navigate('/agent')}
            aria-label="Close settings"
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[var(--panel-inset)] text-[var(--text-muted)] transition-colors hover:text-[var(--text)]"
          >
            <X size={15} />
          </button>
        </header>

        <div className="flex min-h-0 flex-1 flex-col md:grid md:grid-cols-[210px_minmax(0,1fr)]">
          <nav
            aria-label="Settings sections"
            className="shrink-0 border-b border-[var(--border)] p-2 md:overflow-y-auto md:border-b-0 md:border-r md:p-3"
          >
            <ul className="flex gap-1 overflow-x-auto md:block md:overflow-visible">
              {SECTIONS.map(({ id, label, Icon }) => {
                const active = id === current.id;
                return (
                  <li key={id} className="shrink-0">
                    <NavLink
                      to={`/agent/settings/${id}`}
                      aria-current={active ? 'page' : undefined}
                      className="flex items-center gap-2.5 whitespace-nowrap rounded-[12px] px-3 py-2 text-[13px] transition-colors hover:text-[var(--text)]"
                      style={{
                        color: active ? 'var(--text)' : 'var(--text-muted)',
                        background: active ? 'var(--panel-inset-strong)' : undefined,
                      }}
                    >
                      <Icon size={15} strokeWidth={1.5} aria-hidden="true" />
                      {label}
                    </NavLink>
                  </li>
                );
              })}
            </ul>
          </nav>

          <div className="min-h-0 min-w-0 overflow-y-auto px-6 py-6 md:px-8" key={current.id}>
            {current.body}
          </div>
        </div>
      </motion.div>
    </div>
  );
}

export default SettingsPage;
