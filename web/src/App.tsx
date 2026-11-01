import { Suspense, lazy, useEffect, type ReactNode } from 'react';
import { BrowserRouter, Navigate, Outlet, Route, Routes, useLocation } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AmbientBackground } from '@/components/layout/AmbientBackground';
import { HeaderBrand } from '@/components/layout/HeaderBrand';
import { FloatingDock } from '@/components/layout/FloatingDock';
import { api, IS_MOCK, setAuthFailureHandler } from '@/lib/api';
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
 * `setAuthFailureHandler` is debounced per token in `lib/api.ts`.
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
    return <Navigate to="/pair" replace state={{ from: location.pathname }} />;
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

              <Route element={<AppShell />}>
                <Route index element={<HomeRoute />} />
                <Route path="chat" element={<ChatRoute />} />
                <Route path="chat/:sessionId" element={<ChatRoute />} />
                <Route path="settings" element={<SettingsRoute />} />
              </Route>

              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </Suspense>
        </AuthGate>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
