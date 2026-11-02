import { useEffect } from 'react';
import { motion } from 'framer-motion';
import { Database, Moon, RefreshCw, Server, Smartphone, Sun, X } from 'lucide-react';
import { userMessageOf } from '@/lib/errors';
import { IS_MOCK } from '@/lib/api';
import {
  useConnectors,
  useHealth,
  useLimits,
  useSyncConnector,
  useUserSettings,
} from '@/queries/settings';
import { useSession } from '@/store/useSession';
import { useTheme } from '@/store/useTheme';

const spring = { type: 'spring' as const, stiffness: 400, damping: 30 };

type Tone = 'ok' | 'degraded' | 'down';

const TONE_COLOR: Record<Tone, string> = {
  ok: '#34d399',
  degraded: '#fbbf24',
  down: '#f87171',
};

const TONE_LABEL: Record<Tone, string> = {
  ok: 'Healthy',
  degraded: 'Degraded',
  down: 'Offline',
};

function Badge({ name, tone, detail }: { name: string; tone: Tone; detail?: string }) {
  return (
    <div className="flex items-center justify-between rounded-2xl bg-[color-mix(in_srgb,var(--text)_5%,transparent)] px-4 py-3">
      <span className="text-sm font-medium">{name}</span>
      <span className="flex items-center gap-2 text-xs text-[var(--text-muted)]">
        {detail ? <span className="tabular">{detail}</span> : null}
        <span className="relative flex h-2 w-2">
          <span
            className="absolute inline-flex h-full w-full animate-ping rounded-full opacity-50"
            style={{ background: TONE_COLOR[tone] }}
          />
          <span
            className="relative inline-flex h-2 w-2 rounded-full"
            style={{ background: TONE_COLOR[tone] }}
          />
        </span>
        {TONE_LABEL[tone]}
      </span>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 text-[13px]">
      <dt className="text-[var(--text-muted)]">{label}</dt>
      <dd className="truncate text-right">{value}</dd>
    </div>
  );
}

export function SettingsSheet({ onClose }: { onClose: () => void }) {
  const theme = useTheme((state) => state.theme);
  const setTheme = useTheme((state) => state.setTheme);
  const device = useSession((state) => state.device);
  const signOut = useSession((state) => state.signOut);

  const health = useHealth();
  const limits = useLimits();
  const settings = useUserSettings();
  const connectors = useConnectors();
  const syncConnector = useSyncConnector();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    document.body.style.overflow = 'hidden';
    return () => {
      window.removeEventListener('keydown', onKey);
      document.body.style.overflow = '';
    };
  }, [onClose]);

  const backendTone: Tone = health.isError
    ? 'down'
    : health.data?.status === 'ok'
      ? 'ok'
      : health.data
        ? 'degraded'
        : 'degraded';

  const dbTone: Tone =
    health.data?.database === 'connected' ? 'ok' : health.data?.database === 'error' ? 'down' : 'degraded';

  return (
    <>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        onClick={onClose}
        className="fixed inset-0 z-[35] bg-[color-mix(in_srgb,var(--bg-from)_45%,transparent)] backdrop-blur-[3px]"
      />
      <div className="pointer-events-none fixed inset-0 z-[36] flex items-start justify-center px-4 pb-28 pt-20 md:items-center md:justify-start md:p-0 md:pl-24">
        <motion.section
          role="dialog"
          aria-modal="true"
          aria-labelledby="settings-heading"
          initial={{ scale: 0.94, y: 20, opacity: 0 }}
          animate={{ scale: 1, y: 0, opacity: 1 }}
          transition={spring}
          className="glass scroll-soft pointer-events-auto max-h-full w-full overflow-y-auto rounded-[32px] p-7 backdrop-blur-3xl md:max-h-[84vh] md:w-[420px]"
        >
          <div className="flex items-center justify-between">
            <h2 id="settings-heading" className="text-xl font-semibold tracking-heading">
              Settings
            </h2>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close settings"
              autoFocus
              className="flex h-9 w-9 items-center justify-center rounded-full text-[var(--text-muted)] transition-colors hover:bg-[color-mix(in_srgb,var(--text)_8%,transparent)] hover:text-[var(--text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
            >
              <X size={16} strokeWidth={1.5} />
            </button>
          </div>

          {/* Appearance */}
          <h3 className="mb-3 mt-7 text-xs font-semibold uppercase tracking-[0.12em] text-[var(--text-muted)]">
            Appearance
          </h3>
          <div className="relative grid grid-cols-2 gap-1 rounded-full bg-[color-mix(in_srgb,var(--text)_6%,transparent)] p-1">
            {(
              [
                ['light', 'Cream & White', Sun],
                ['dark', 'Deep Navy & Sky', Moon],
              ] as const
            ).map(([id, label, Icon]) => {
              const active = theme === id;
              return (
                <button
                  key={id}
                  type="button"
                  onClick={() => setTheme(id)}
                  aria-pressed={active}
                  className="relative flex h-10 items-center justify-center gap-2 rounded-full text-[13px] font-semibold outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
                  style={{ color: active ? 'var(--text)' : 'var(--text-muted)' }}
                >
                  {active && (
                    <motion.span
                      layoutId="theme-pill"
                      transition={spring}
                      className="absolute inset-0 rounded-full bg-[var(--card-strong)]"
                      style={{ boxShadow: '0 4px 14px rgba(0,0,0,0.18)' }}
                    />
                  )}
                  <span className="relative flex items-center gap-2">
                    <Icon size={16} strokeWidth={1.5} />
                    {label}
                  </span>
                </button>
              );
            })}
          </div>

          {/* This device */}
          <h3 className="mb-3 mt-8 text-xs font-semibold uppercase tracking-[0.12em] text-[var(--text-muted)]">
            This device
          </h3>
          <div className="flex items-center gap-4 rounded-3xl bg-[color-mix(in_srgb,var(--text)_5%,transparent)] p-4">
            <div
              className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl"
              style={{
                background: 'color-mix(in srgb, var(--accent) 12%, transparent)',
                color: 'var(--accent)',
              }}
            >
              <Smartphone size={20} strokeWidth={1.5} />
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-semibold">
                {device?.device_name ?? 'Unknown device'}
              </div>
              <p className="mt-0.5 text-[13px] leading-relaxed text-[var(--text-muted)]">
                {device
                  ? `Last seen ${new Date(device.last_seen_at).toLocaleString()}`
                  : 'Not paired'}
              </p>
            </div>
          </div>

          <dl className="mt-3 space-y-1.5 rounded-2xl bg-[color-mix(in_srgb,var(--text)_5%,transparent)] px-4 py-3">
            <Row label="User" value={device?.user_display_name || 'Paired'} />
            <Row label="Mode" value={IS_MOCK ? 'Simulation' : 'Live'} />
            <Row
              label="Monthly cap"
              value={
                settings.data ? `$${settings.data.spending_cap_usd_monthly.toFixed(2)}` : 'unknown'
              }
            />
          </dl>

          {!IS_MOCK ? (
            <button
              type="button"
              onClick={() => void signOut()}
              className="btn btn-ghost mt-3 w-full"
            >
              Unpair this device
            </button>
          ) : null}

          {/* Connectors */}
          <h3 className="mb-3 mt-8 text-xs font-semibold uppercase tracking-[0.12em] text-[var(--text-muted)]">
            Connectors
          </h3>
          {connectors.data && connectors.data.length > 0 ? (
            <ul className="space-y-2">
              {connectors.data.map((account) => (
                <li
                  key={account.id}
                  className="flex items-center justify-between rounded-2xl bg-[color-mix(in_srgb,var(--text)_5%,transparent)] px-4 py-2.5"
                >
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-medium">
                      {account.account_label}
                    </span>
                    <span className="block text-[12px] text-[var(--text-muted)]">
                      {account.connector_id} · {account.status.replace(/_/g, ' ')}
                    </span>
                  </span>
                  <button
                    type="button"
                    aria-label={`Sync ${account.account_label}`}
                    disabled={syncConnector.isPending}
                    onClick={() =>
                      syncConnector.mutate({
                        accountId: account.id,
                        scope: `connector:sync:${account.id}`,
                      })
                    }
                    className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[var(--text-muted)] transition-colors hover:bg-[color-mix(in_srgb,var(--text)_8%,transparent)] hover:text-[var(--text)] disabled:opacity-40"
                  >
                    <RefreshCw
                      size={14}
                      strokeWidth={1.5}
                      className={syncConnector.isPending ? 'animate-spin' : undefined}
                    />
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="rounded-2xl bg-[color-mix(in_srgb,var(--text)_5%,transparent)] px-4 py-3 text-[13px] text-[var(--text-muted)]">
              {connectors.isLoading ? 'Checking connectors...' : 'No connectors linked yet.'}
            </p>
          )}

          {syncConnector.isError ? (
            <p role="alert" className="mt-2 px-2 text-[12px] text-[var(--urgency-critical)]">
              {userMessageOf(syncConnector.error)}
            </p>
          ) : null}

          {/* Telemetry & health */}
          <div className="mb-3 mt-8 flex items-center justify-between">
            <h3 className="text-xs font-semibold uppercase tracking-[0.12em] text-[var(--text-muted)]">
              Telemetry &amp; health
            </h3>
            <span className="text-[11px] text-[var(--text-muted)]">
              {IS_MOCK ? 'Simulation mode' : 'Live'}
            </span>
          </div>
          <div className="space-y-2">
            <Badge
              name="FastAPI backend"
              tone={backendTone}
              detail={health.data?.version}
            />
            <Badge name="Database" tone={dbTone} detail={health.data?.database} />
            <Badge
              name="Run limits"
              tone={limits.isError ? 'down' : 'ok'}
              detail={
                limits.data
                  ? `${limits.data.max_steps_per_run} steps · $${limits.data.max_cost_usd_per_run.toFixed(2)}`
                  : undefined
              }
            />
          </div>

          <p className="mt-6 flex items-center gap-1.5 text-[11px] text-[var(--text-muted)]">
            <Server size={12} strokeWidth={1.5} />
            <Database size={12} strokeWidth={1.5} />
            {IS_MOCK
              ? 'Fixture data. Nothing is sent to a backend.'
              : 'Connected to the Vigil backend.'}
          </p>
        </motion.section>
      </div>
    </>
  );
}

export default SettingsSheet;
