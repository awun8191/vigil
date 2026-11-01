import { useNavigate } from 'react-router-dom';
import { HomeView } from '@/components/home/HomeView';
import { SettingsSheet } from '@/components/settings/SettingsSheet';

/**
 * Settings is a route, not local state, so browser back and deep links both work. The briefing
 * renders behind the scrim, matching the way the sheet reads as an overlay on the app.
 */
export default function SettingsRoute() {
  const navigate = useNavigate();

  const close = () => {
    if (window.history.length > 1) {
      navigate(-1);
    } else {
      navigate('/', { replace: true });
    }
  };

  return (
    <>
      <HomeView />
      <SettingsSheet onClose={close} />
    </>
  );
}
