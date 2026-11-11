import { Suspense, lazy, useEffect, type ReactNode } from 'react';
import { BrowserRouter, Navigate, Outlet, Route, Routes, useLocation } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AmbientBackground } from '@/components/layout/AmbientBackground';
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
      <div id="app-content">
        <Outlet />
      </div>

      {IS_MOCK ? (
        <div className="fixed bottom-4 left-4 z-50 hidden items-center gap-2 rounded-full border border-[var(--border)] bg-[var(--panel-bg)] px-3 py-1.5 text-[10px] font-medium uppercase tracking-[0.08em] text-[var(--text-muted)] shadow-[var(--shadow)] backdrop-blur-md md:flex">
          <span className="h-1.5 w-1.5 rounded-full bg-[var(--text)]" />
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
    if (location.pathname === '/pair') return <>{children}</>;
    // Allow dev/guest access directly to the living canvas
    return <>{children}</>;
  }

  if (location.pathname === '/pair') {
    return <Navigate to="/" replace />;
  }

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
              <Route path="/landing" element={<LandingRoute />} />

              <Route element={<AppShell />}>
                <Route path="/" element={<HomeRoute />} />
                <Route path="/agent" element={<HomeRoute />} />
                <Route path="/agent/chat" element={<ChatRoute />} />
                <Route path="/agent/chat/:sessionId" element={<ChatRoute />} />
                <Route
                  path="/agent/settings"
                  element={<Navigate to="/agent/settings/appearance" replace />}
                />
                <Route path="/agent/settings/:section" element={<SettingsRoute />} />
              </Route>

              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </Suspense>
        </AuthGate>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
