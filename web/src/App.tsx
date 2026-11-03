import { Suspense, lazy, useEffect, type ReactNode } from 'react';
import { BrowserRouter, Navigate, Outlet, Route, Routes, useLocation } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AmbientBackground } from '@/components/layout/AmbientBackground';
import { HeaderBrand } from '@/components/layout/HeaderBrand';
import { FloatingDock } from '@/components/layout/FloatingDock';
import { api, IS_MOCK, setAuthFailureHandler } from '@/lib/api';
import LandingRoute from '@/routes/LandingRoute';
import HomeRoute from '@/routes/HomeRoute';
import PairRoute from '@/routes/PairRoute';
import ChatRoute from '@/routes/ChatRoute';
import { useSession } from '@/store/useSession';

const SettingsRoute = lazy(() => import('@/routes/SettingsRoute'));

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
      staleTime: 10_000,
    },
  },
});

function AppShell() {
  return (
    <div className="relative min-h-dvh">
      <HeaderBrand />
      <FloatingDock />

      <div id="main">
        <Outlet />
      </div>

      {IS_MOCK ? (
        <div className="fixed bottom-0 left-0 right-0 z-50 bg-[var(--accent)] px-3 py-1 text-center text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--accent-ink)]">
          Simulation mode, no backend
        </div>
      ) : null}
    </div>
  );
}

/**
 * The shell must never render unauthenticated. A 401 anywhere lands here exactly once, because
 * `setAuthFailureHandler` is debounced per token in `lib/api.ts`. The landing page at `/` stays
 * public: unpaired visitors read it, paired users are sent straight to `/agent`.
 */
function AuthGate({ children }: { children: ReactNode }) {
  const status = useSession((state) => state.status);
  const hydrate = useSession((state) => state.hydrate);
  const expire = useSession((state) => state.expire);
  const location = useLocation();

  useEffect(() => {
    setAuthFailureHandler(() => expire());
    void hydrate(() => api.me());
    return () => setAuthFailureHandler(null);
  }, [hydrate, expire]);

  if (status === 'loading') {
    return (
      <div className="flex min-h-dvh items-center justify-center">
        <span className="text-[13px] text-[var(--text-muted)]">Connecting...</span>
      </div>
    );
  }

  if (status === 'unauthed') {
    if (location.pathname === '/pair' || location.pathname === '/') return <>{children}</>;
    return <Navigate to="/pair" replace state={{ from: location.pathname }} />;
  }

  if (location.pathname === '/pair') {
    return <Navigate to="/agent" replace />;
  }

  // The landing stays reachable for everyone, paired or not, so `Log in` can always be linked.
  return <>{children}</>;
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AmbientBackground />
        <AuthGate>
          <Suspense fallback={null}>
            <Routes>
              <Route path="/pair" element={<PairRoute />} />

              <Route path="/" element={<LandingRoute />} />

              <Route element={<AppShell />}>
                <Route path="/agent" element={<HomeRoute />} />
                <Route path="/agent/chat" element={<ChatRoute />} />
                <Route path="/agent/chat/:sessionId" element={<ChatRoute />} />
                <Route
                  path="/agent/settings"
                  element={<Navigate to="/agent/settings/appearance" replace />}
                />
                <Route path="/agent/settings/:section" element={<SettingsRoute />} />
              </Route>

              <Route path="*" element={<Navigate to="/agent" replace />} />
            </Routes>
          </Suspense>
        </AuthGate>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
