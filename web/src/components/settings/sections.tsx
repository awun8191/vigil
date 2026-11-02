import { RefreshCw, Smartphone } from 'lucide-react';
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

type Tone = 'ok' | 'degraded' | 'down';

const TONE_COLOR: Record<Tone, string> = {
  ok: 'var(--text)',
  degraded: 'color-mix(in srgb, var(--text) 45%, transparent)',
  down: 'color-mix(in srgb, var(--text) 22%, transparent)',
};

const TONE_LABEL: Record<Tone, string> = {
  ok: 'Healthy',
  degraded: 'Degraded',
  down: 'Offline',
};

/** One black-and-white status line: the label carries the meaning, the dot just marks it. */
function StatusLine({ name, tone, detail }: { name: string; tone: Tone; detail?: string }) {
  return (
    <li className="flex items-center justify-between rounded-lg bg-[color-mix(in_srgb,var(--text)_5%,transparent)] px-4 py-3">
      <span className="text-sm font-medium">{name}</span>
      <span className="flex items-center gap-2.5 text-xs text-[var(--text-muted)]">
        {detail ? <span className="tabular">{detail}</span> : null}
        <span
          className="inline-flex h-2 w-2 rounded-full"
          style={{ background: TONE_COLOR[tone] }}
        />
        {TONE_LABEL[tone]}
      </span>
    </li>
  );
}

/** A read-only fact row, the default for values the backend owns. */
function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 px-4 py-2 text-[14px] first:pt-3 last:pb-3">
      <dt className="text-[var(--text-muted)]">{label}</dt>
      <dd className="min-w-0 truncate text-right tabular">{value}</dd>
    </div>
  );
}

function FieldList({ children }: { children: React.ReactNode }) {
  return (
    <dl className="rounded-lg bg-[color-mix(in_srgb,var(--text)_4%,transparent)]">{children}</dl>
  );
}

function Blurb({ children }: { children: React.ReactNode }) {
  return <p className="mb-6 mt-2 max-w-xl text-[14px] leading-relaxed text-[var(--text-muted)]">{children}</p>;
}

/* ------------------------------------------------------------------ Appearance ---------- */

export function AppearanceSection() {
  const theme = useTheme((state) => state.theme);
  const setTheme = useTheme((state) => state.setTheme);

  return (
    <div>
      <h2 className="text-[22px] font-semibold tracking-[-0.015em]">Appearance</h2>
      <Blurb>Pick the surface colour. The choice is saved on this device only.</Blurb>

      <fieldset className="max-w-md">
        <legend className="mb-2 text-[13px] font-medium">Theme</legend>
        <div className="grid grid-cols-2 gap-2" role="radiogroup" aria-label="Theme">
          {(
            [
              ['light', 'Light'],
              ['dark', 'Dark'],
            ] as const
          ).map(([id, label]) => {
            const active = theme === id;
            return (
              <button
                key={id}
                type="button"
                role="radio"
                aria-checked={active}
                onClick={() => setTheme(id)}
                className="flex h-11 items-center justify-center rounded-[10px] border text-[14px] font-medium transition-colors"
                style={{
                  borderColor: active ? 'var(--text)' : 'var(--border)',
                  background: active
                    ? 'color-mix(in srgb, var(--text) 8%, transparent)'
                    : 'transparent',
                  color: active ? 'var(--text)' : 'var(--text-muted)',
                }}
              >
                {label}
              </button>
            );
          })}
        </div>
      </fieldset>
    </div>
  );
}

/* --------------------------------------------------------------- Notifications ---------- */

export function NotificationsSection() {
  const health = useHealth();
  const settings = useUserSettings();

  const backendTone: Tone = health.isError
    ? 'down'
    : health.data?.status === 'ok'
      ? 'ok'
      : 'degraded';
  const dbTone: Tone =
    health.data?.database === 'connected'
      ? 'ok'
      : health.data?.database === 'error'
        ? 'down'
        : 'degraded';

  return (
    <div>
      <h2 className="text-[22px] font-semibold tracking-[-0.015em]">Notifications</h2>
      <Blurb>
        Vigil checks the backend on a fixed cadence for anything that needs your attention. Every
        pass can raise a card: a task worth doing, a gentle reminder, or something urgent.
      </Blurb>

      <FieldList>
        <Row
          label="Check interval"
          value={
            settings.data
              ? `Every ${settings.data.polling_interval_seconds} seconds`
              : settings.isLoading
                ? 'Checking...'
                : 'unknown'
          }
        />
      </FieldList>

      <h3 className="mb-3 mt-8 text-[13px] font-medium">Delivery depends on</h3>
      <ul className="max-w-md space-y-2">
        <StatusLine name="Backend" tone={backendTone} detail={health.data?.version} />
        <StatusLine name="Database" tone={dbTone} detail={health.data?.database?.replace('_', ' ')} />
      </ul>
    </div>
  );
}

/* --------------------------------------------------------------------- Profile ---------- */

export function ProfileSection() {
  const device = useSession((state) => state.device);

  return (
    <div>
      <h2 className="text-[22px] font-semibold tracking-[-0.015em]">Profile</h2>
      <Blurb>
        Your name was set on the machine running Vigil when the backend was configured. It travels
        with every pairing.
      </Blurb>

      <FieldList>
        <Row label="Name" value={device?.user_display_name || 'Paired user'} />
        <Row label="User ID" value={device?.user_id ?? 'unknown'} />
      </FieldList>
    </div>
  );
}

/* ------------------------------------------------------------------- Security ------------ */

export function SecuritySection() {
  const device = useSession((state) => state.device);
  const signOut = useSession((state) => state.signOut);

  const fmtDate = (iso: string) => new Date(iso).toLocaleString();

  return (
    <div>
      <h2 className="text-[22px] font-semibold tracking-[-0.015em]">Security &amp; login</h2>
      <Blurb>
        Devices pair with a single-use code that expires 10 minutes after boot. Each pairing issues
        its own key, and removing the pairing cuts the key at the backend.
      </Blurb>

      <div className="flex items-center gap-4 rounded-lg bg-[color-mix(in_srgb,var(--text)_4%,transparent)] p-4">
        <div
          className="flex h-12 w-12 shrink-0 items-center justify-center rounded-[8px]"
          style={{
            background: 'color-mix(in srgb, var(--text) 8%, transparent)',
            color: 'var(--text)',
          }}
        >
          <Smartphone size={20} strokeWidth={1.5} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold">
            {device?.device_name ?? 'Unknown device'}
          </div>
          <p className="mt-0.5 text-[13px] leading-relaxed text-[var(--text-muted)]">
            {device ? `Last seen ${fmtDate(device.last_seen_at)}` : 'Not paired'}
          </p>
        </div>
      </div>

      <FieldList>
        <Row label="Paired" value={device ? fmtDate(device.created_at) : 'unknown'} />
        <Row label="Device ID" value={device?.device_id ?? 'unknown'} />
      </FieldList>

      {!IS_MOCK ? (
        <button
          type="button"
          onClick={() => void signOut()}
          className="btn btn-ghost mt-4"
        >
          Unpair this device
        </button>
      ) : null}
    </div>
  );
}

/* --------------------------------------------------------------------- Account ---------- */

export function AccountSection() {
  const device = useSession((state) => state.device);

  return (
    <div>
      <h2 className="text-[22px] font-semibold tracking-[-0.015em]">Account</h2>
      <Blurb>
        One account lives on the backend. Device keys are separate, so pairing and unpairing never
        touches the account itself.
      </Blurb>

      <FieldList>
        <Row label="Mode" value={IS_MOCK ? 'Simulation' : 'Live'} />
        <Row label="User ID" value={device?.user_id ?? 'unknown'} />
        <Row label="Devices" value="This device" />
      </FieldList>

      {IS_MOCK ? (
        <p className="mt-4 rounded-lg bg-[color-mix(in_srgb,var(--text)_4%,transparent)] px-4 py-3 text-[13px] leading-relaxed text-[var(--text-muted)]">
          Simulation mode runs on fixture data. Nothing is sent to a backend and any pairing code is
          accepted.
        </p>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------ Connectors ---------- */

export function ConnectorsSection() {
  const connectors = useConnectors();
  const syncConnector = useSyncConnector();

  return (
    <div>
      <h2 className="text-[22px] font-semibold tracking-[-0.015em]">Connectors</h2>
      <Blurb>
        Linked accounts feed Vigil the signals it watches over. Refreshing asks the backend to re-poll
        the connector now.
      </Blurb>

      {connectors.data && connectors.data.length > 0 ? (
        <ul className="max-w-xl space-y-2">
          {connectors.data.map((account) => (
            <li
              key={account.id}
              className="flex items-center justify-between rounded-lg bg-[color-mix(in_srgb,var(--text)_4%,transparent)] px-4 py-3"
            >
              <span className="min-w-0">
                <span className="block truncate text-sm font-medium">{account.account_label}</span>
                <span className="block text-[12px] text-[var(--text-muted)]">
                  {account.connector_id} · {account.status.replace(/_/g, ' ')}
                </span>
              </span>
              <button
                type="button"
                aria-label={`Refresh ${account.account_label}`}
                disabled={syncConnector.isPending}
                onClick={() =>
                  syncConnector.mutate({
                    accountId: account.id,
                    scope: `connector:sync:${account.id}`,
                  })
                }
                className="flex h-8 shrink-0 items-center gap-1.5 rounded-full px-3 text-[12px] font-medium text-[var(--text-muted)] transition-colors hover:bg-[color-mix(in_srgb,var(--text)_8%,transparent)] hover:text-[var(--text)] disabled:opacity-40"
              >
                <RefreshCw
                  size={13}
                  strokeWidth={1.5}
                  className={syncConnector.isPending ? 'animate-spin' : undefined}
                />
                Refresh
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="max-w-xl rounded-lg bg-[color-mix(in_srgb,var(--text)_4%,transparent)] px-4 py-3 text-[13px] text-[var(--text-muted)]">
          {connectors.isLoading ? 'Checking connectors...' : 'No connectors linked yet.'}
        </p>
      )}

      {syncConnector.isError ? (
        <p role="alert" className="mt-3 max-w-xl text-[12px] text-[var(--text)]">
          {userMessageOf(syncConnector.error)}
        </p>
      ) : null}
    </div>
  );
}

/* ----------------------------------------------------------------------- Usage ---------- */

export function UsageSection() {
  const limits = useLimits();
  const settings = useUserSettings();

  return (
    <div>
      <h2 className="text-[22px] font-semibold tracking-[-0.015em]">Usage</h2>
      <Blurb>
        Caps keep a misbehaving run from quietly eating the budget. The backend enforces them no
        matter what a client asks for.
      </Blurb>

      <FieldList>
        <Row
          label="Monthly spend cap"
          value={
            settings.data ? `$${settings.data.spending_cap_usd_monthly.toFixed(2)}` : settings.isLoading ? 'Checking...' : 'unknown'
          }
        />
        <Row
          label="Steps per run"
          value={limits.data ? String(limits.data.max_steps_per_run) : limits.isLoading ? 'Checking...' : 'unknown'}
        />
        <Row
          label="Cost cap per run"
          value={limits.data ? `$${limits.data.max_cost_usd_per_run.toFixed(2)}` : limits.isLoading ? 'Checking...' : 'unknown'}
        />
        <Row
          label="Run timeout"
          value={limits.data ? `${limits.data.run_timeout_seconds} seconds` : limits.isLoading ? 'Checking...' : 'unknown'}
        />
        <Row
          label="Sandbox memory"
          value={limits.data ? limits.data.docker_memory_limit : limits.isLoading ? 'Checking...' : 'unknown'}
        />
      </FieldList>
    </div>
  );
}

/* ------------------------------------------------------------ Personalisation ----------- */

export function PersonalizationSection() {
  const settings = useUserSettings();

  if (!settings.data) {
    return (
      <div>
        <h2 className="text-[22px] font-semibold tracking-[-0.015em]">Personalization</h2>
        <Blurb>{settings.isLoading ? 'Checking settings...' : 'Settings are not available.'}</Blurb>
      </div>
    );
  }

  const approvals = Object.entries(settings.data.approval_defaults);

  return (
    <div>
      <h2 className="text-[22px] font-semibold tracking-[-0.015em]">Personalization</h2>
      <Blurb>
        How the engine picks its tools and when it stops to ask you first, as configured on the
        backend.
      </Blurb>

      <FieldList>
        <Row label="Preferred model tier" value={settings.data.preferred_model_tier} />
        {approvals.map(([tool, choice]) => (
          <Row key={tool} label={`Ask first, ${tool}`} value={choice.replace(/_/g, ' ')} />
        ))}
      </FieldList>
    </div>
  );
}

/* -------------------------------------------------------------------- Watching ---------- */

export function WatchingSection() {
  return (
    <div>
      <h2 className="text-[22px] font-semibold tracking-[-0.015em]">Watching</h2>
      <Blurb>
        The things Vigil keeps guard over, one entry each. Managing them from here arrives with a
        later backend release.
      </Blurb>

      <div className="flex max-w-xl flex-col items-center rounded-lg border border-[var(--border)] px-6 py-14 text-center">
        <p className="text-[15px] font-medium">Nothing to watch yet.</p>
        <p className="mt-1.5 max-w-sm text-[14px] leading-relaxed text-[var(--text-muted)]">
          Connectors will present the items they watch once the backend exposes item management.
        </p>
      </div>
    </div>
  );
}

/* ----------------------------------------------------------------------- Voice ---------- */

export function VoiceSection() {
  return (
    <div>
      <h2 className="text-[22px] font-semibold tracking-[-0.015em]">Voice</h2>
      <Blurb>
        The chat composer takes speech through its microphone button. It listens, shows the live
        waveform, and hands the transcript to the message field, where you can edit it before
        sending. There is nothing to configure here yet.
      </Blurb>
    </div>
  );
}
