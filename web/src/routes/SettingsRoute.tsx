import { useNavigate } from 'react-router-dom';
import { SettingsSheet } from '@/components/settings/SettingsSheet';

/** Settings is a route, not local state, so browser back and deep links both work. */
export default function SettingsRoute() {
  const navigate = useNavigate();

  const close = () => {
    if (window.history.length > 1) {
      navigate(-1);
    } else {
      navigate('/', { replace: true });
    }
  };

  return <SettingsSheet onClose={close} />;
}
