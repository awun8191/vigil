import { SettingsPage } from '@/components/settings/SettingsPage';

/**
 * Settings is a full page now, not an overlay: a section rail plus one panel, deep-linkable at
 * `/settings/<section>`. The old sheet-over-the-briefing behaviour is gone entirely.
 */
export default function SettingsRoute() {
  return <SettingsPage />;
}
