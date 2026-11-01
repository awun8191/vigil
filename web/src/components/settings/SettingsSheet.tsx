import { Moon, RefreshCw, Sun, X } from 'lucide-react';
import { Sheet } from '@/components/ui/Sheet';
import { IconButton } from '@/components/ui/IconButton';
import { Pill } from '@/components/ui/Pill';
import { userMessageOf } from '@/lib/errors';
import { useConnectors, useHealth, useLimits, useSyncConnector, useUserSettings } from '@/queries/settings';
import { useSession } from '@/store/useSession';
import { useTheme } from '@/store/useTheme';

interface SettingsSheetProps {
  onClose: () => void;
}

export function SettingsSheet({ onClose }: SettingsSheetProps) {
  const theme = useTheme((state) => state.theme);
  const setTheme = useTheme((state) => state.setTheme);
  const device = useSession((state) => state.device);
  const signOut = useSession((state) => state.signOut);

  const health = useHealth();
  const limits = useLimits();
  const settings = useUserSettings();
  const connectors = useConnectors();
  const syncConnector = useSyncConnector();

  return (
    <Sheet onClose={onClose} labelledBy="settings-heading">
      <header className="flex items-center justify-between border-b border-hairline px-5 py-4">
        <h1 id="settings-heading" className="text-[17px] font-semibold tracking-[-0.01em]">
          Settings
        </h1>
        <IconButton label="Close settings" size="sm" onClick={onClose}>
          <X size={16} strokeWidth={1.5} aria-hidden="true" />
        </IconButton>
      </header>

      <div className="flex-1 space-y-7 overflow-y-auto px-5 py-5">
        <section aria-labelledby="settings-appearance">
          <h2 id="settings-appearance" className="mb-2.5 text-[12px] font-medium tracking-[0.04em] uppercase text-ink-3">
            Appearance
          </h2>
          <div className="inline-flex gap-1 rounded-[var(--r-pill)] border border-hairline p-1">
            <ThemeOption
              active={theme === 'light'}
              onClick={() => setTheme('light')}
              icon={<Sun size={16} strokeWidth={1.5} aria-hidden="true" />}
              label="Cream"
            />
            <ThemeOption
              active={theme === 'dark'}
              onClick={() => setTheme('dark')}
              icon={<Moon size={16} strokeWidth={1.5} aria-hidden="true" />}
              label="Navy"
            />
          </div>
        </section>

        <section aria-labelledby="settings-device">
          <h2 id="settings-device" className="mb-2.5 text-[12px] font-medium tracking-[0.04em] uppercase text-ink-3">
            This device
          </h2>
          <dl className="space-y-1.5 text-[13px]">
            <Row label="Name" value={device?.device_name ?? 'Unknown'} />
            <Row label="User" value={device?.user_display_name || 'Paired'} />
            <Row
              label="Last seen"
              value={device ? new Date(device.last_seen_at).toLocaleString() : 'Unknown'}
            />
          </dl>
          <Pill className="mt-3" onClick={() => void signOut()}>
            Unpair this device
          </Pill>
        </section>

        <section aria-labelledby="settings-connectors">
          <h2 id="settings-connectors" className="mb-2.5 text-[12px] font-medium tracking-[0.04em] uppercase text-ink-3">
            Connectors
          </h2>

          {connectors.isLoading ? (
            <p className="text-[13px] text-ink-3">Loading connectors...</p>
          ) : connectors.data && connectors.data.length > 0 ? (
            <ul className="space-y-2">
              {connectors.data.map((account) => (
                <li
                  key={account.id}
                  className="flex items-center gap-3 rounded-[var(--r-control)] border border-hairline px-3.5 py-2.5"
                >
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[14px] text-ink">{account.account_label}</p>
                    <p className="text-[12px] text-ink-3">
                      {account.connector_id} · {account.status.replace(/_/g, ' ')}
                    </p>
                  </div>
                  <IconButton
                    label={`Sync ${account.account_label}`}
                    size="sm"
                    disabled={syncConnector.isPending}
                    onClick={() =>
                      syncConnector.mutate({
                        accountId: account.id,
                        scope: `connector:sync:${account.id}`,
                      })
                    }
                  >
                    <RefreshCw size={14} strokeWidth={1.5} aria-hidden="true" />
                  </IconButton>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-[13px] text-ink-3">No connectors linked yet.</p>
          )}

          {syncConnector.isError ? (
            <p role="alert" className="mt-2 text-[12px] text-critical">
              {userMessageOf(syncConnector.error)}
            </p>
          ) : null}
        </section>

        <section aria-labelledby="settings-health">
          <h2 id="settings-health" className="mb-2.5 text-[12px] font-medium tracking-[0.04em] uppercase text-ink-3">
            Health
          </h2>
          <div className="flex flex-wrap gap-2">
            <Badge
              label="Backend"
              value={health.data?.status ?? (health.isError ? 'unreachable' : 'checking')}
              tone={health.isError ? 'critical' : health.data?.status === 'ok' ? 'ok' : 'neutral'}
            />
            <Badge
              label="Database"
              value={health.data?.database ?? 'unknown'}
              tone={
                health.data?.database === 'connected'
                  ? 'ok'
                  : health.data?.database === 'error'
                    ? 'critical'
                    : 'neutral'
              }
            />
            <Badge label="Version" value={health.data?.version ?? 'unknown'} tone="neutral" />
          </div>
          {health.data?.database === 'disabled' ? (
            <p className="mt-2 text-[12px] leading-relaxed text-ink-3">
              Persistence is disabled. Vigil runs, but nothing is stored between restarts.
            </p>
          ) : null}
        </section>

        <section aria-labelledby="settings-limits">
          <h2 id="settings-limits" className="mb-2.5 text-[12px] font-medium tracking-[0.04em] uppercase text-ink-3">
            Limits
          </h2>
          <dl className="space-y-1.5 text-[13px]">
            <Row label="Steps per run" value={limits.data ? String(limits.data.max_steps_per_run) : 'unknown'} />
            <Row
              label="Cost per run"
              value={limits.data ? `$${limits.data.max_cost_usd_per_run.toFixed(2)}` : 'unknown'}
            />
            <Row
              label="Run timeout"
              value={limits.data ? `${Math.round(limits.data.run_timeout_seconds / 60)} min` : 'unknown'}
            />
            <Row
              label="Monthly cap"
              value={
                settings.data ? `$${settings.data.spending_cap_usd_monthly.toFixed(2)}` : 'unknown'
              }
            />
          </dl>
        </section>
      </div>
    </Sheet>
  );
}

function ThemeOption({
  active,
  onClick,
  icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={[
        'inline-flex items-center gap-1.5 rounded-[var(--r-pill)] px-3 py-1.5 text-[13px] font-medium',
        'transition-colors duration-[var(--dur-base)] ease-[var(--ease-spring)]',
        active ? 'bg-accent-soft text-accent' : 'text-ink-3 hover:text-ink-2',
      ].join(' ')}
    >
      {icon}
      {label}
    </button>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <dt className="text-ink-3">{label}</dt>
      <dd className="truncate text-right text-ink-2">{value}</dd>
    </div>
  );
}

function Badge({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: 'ok' | 'critical' | 'neutral';
}) {
  const dot =
    tone === 'ok' ? 'bg-accent' : tone === 'critical' ? 'bg-critical' : 'bg-ink-3';

  return (
    <span className="inline-flex items-center gap-1.5 rounded-[var(--r-pill)] border border-hairline px-2.5 py-1 text-[12px] text-ink-2">
      <span aria-hidden="true" className={['h-1.5 w-1.5 rounded-full', dot].join(' ')} />
      <span className="text-ink-3">{label}</span>
      <span className="tabular">{value}</span>
    </span>
  );
}
