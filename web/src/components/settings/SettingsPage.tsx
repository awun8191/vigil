import { NavLink, Navigate, useParams } from 'react-router-dom';
import {
  Bell,
  CircleUser,
  Eye,
  Gauge,
  Mic,
  Plug,
  ShieldCheck,
  SlidersHorizontal,
  Sun,
  UserRound,
} from 'lucide-react';
import type { ComponentType, ReactNode } from 'react';
import {
  AccountSection,
  AppearanceSection,
  ConnectorsSection,
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
  { id: 'watching', label: 'Watching', Icon: Eye, body: <WatchingSection /> },
  { id: 'voice', label: 'Voice', Icon: Mic, body: <VoiceSection /> },
];

const SECTION_IDS = new Set(SECTIONS.map((section) => section.id));

/** The full settings page: section rail on the left, one panel on the right. */
export function SettingsPage() {
  const { section } = useParams();

  if (!section || !SECTION_IDS.has(section)) {
    return <Navigate to="/agent/settings/appearance" replace />;
  }

  const current = SECTIONS.find((entry) => entry.id === section);
  if (!current) {
    return <Navigate to="/agent/settings/appearance" replace />;
  }

  return (
    <main className="mx-auto w-full max-w-5xl px-6 pb-36 pt-20 md:pb-24 md:pl-24 md:pt-28">
      {/* Rail: a left column on wide screens, a chip row up top on narrow ones */}
      <div className="md:grid md:grid-cols-[220px_minmax(0,1fr)] md:gap-12">
        <nav aria-label="Settings sections" className="animate-rise relative md:top-24">
          <ul className="-mx-1 flex gap-1 overflow-x-auto pb-2 md:sticky md:top-24 md:mx-0 md:block md:overflow-visible md:pb-0">
            {SECTIONS.map(({ id, label, Icon }) => {
              const active = id === current.id;
              return (
                <li key={id} className="shrink-0">
                  <NavLink
                    to={`/agent/settings/${id}`}
                    aria-current={active ? 'page' : undefined}
                    className="flex items-center gap-2.5 whitespace-nowrap rounded-[10px] px-3 py-2 text-[14px] transition-colors"
                    style={{
                      color: active ? 'var(--text)' : 'var(--text-muted)',
                      background: active
                        ? 'color-mix(in srgb, var(--text) 8%, transparent)'
                        : undefined,
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

        <div className="animate-rise min-w-0 pt-6 md:pt-3" key={current.id}>
          {current.body}
        </div>
      </div>
    </main>
  );
}

export default SettingsPage;
