import { SettingsPage } from '@/components/settings/SettingsPage';
import HomeRoute from '@/routes/HomeRoute';

/**
 * Settings is a modal over the home view, deep-linkable at `/agent/settings/<section>`. Home is
 * rendered underneath so opening or closing settings never swaps in a different-looking page.
 */
export default function SettingsRoute() {
  return (
    <>
      <HomeRoute />
      <SettingsPage />
    </>
  );
}
