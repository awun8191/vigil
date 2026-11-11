import {
  Check,
  ChevronDown,
  ChevronRight,
  Database,
  GitBranch,
  Globe,
  Key,
  Lock,
  Plus,
  RefreshCw,
  Server,
  ShieldCheck,
  Smartphone,
  Terminal,
  Trash2,
  X,
} from 'lucide-react';
import { useState } from 'react';
import { userMessageOf } from '@/lib/errors';
import { IS_MOCK } from '@/lib/api';
import type { VaultValidationResult } from '@/types';
import {
  useConnectors,
  useDeleteVaultSecret,
  useHealth,
  useLimits,
  useMCPServers,
  useRegisterMCPServer,
  useSetVaultSecret,
  useSyncConnector,
  useTestVaultSecret,
  useUnregisterMCPServer,
  useUserSettings,
  useValidateVaultSecret,
  useVaultSecrets,
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

const COMMON_VAULT_KEYS = [
  'FIRECRAWL_API_KEY',
  'GROQ_API_KEY',
  'GITHUB_PERSONAL_ACCESS_TOKEN',
  'LINEAR_API_KEY',
  'POSTGRES_PASSWORD',
] as const;

interface MCPGalleryPreset {
  id: string;
  name: string;
  description: string;
  transport: 'stdio' | 'sse';
  command: string;
  requiredKey: string | null;
  icon: typeof Globe;
}

const MCP_PRESETS: MCPGalleryPreset[] = [
  {
    id: 'firecrawl',
    name: 'Firecrawl',
    description: 'Cloud browser web scraper, dynamic JS rendering, and screenshots.',
    transport: 'stdio',
    command: 'npx -y @firecrawl/mcp-server',
    requiredKey: 'FIRECRAWL_API_KEY',
    icon: Globe,
  },
  {
    id: 'github',
    name: 'GitHub',
    description: 'Repository inspection, issue backlog, pull requests, and commit logs.',
    transport: 'stdio',
    command: 'npx -y @modelcontextprotocol/server-github',
    requiredKey: 'GITHUB_PERSONAL_ACCESS_TOKEN',
    icon: GitBranch,
  },
  {
    id: 'filesystem',
    name: 'Filesystem',
    description: 'Secure workspace filesystem read and write access for local runs.',
    transport: 'stdio',
    command: 'npx -y @modelcontextprotocol/server-filesystem /workspace',
    requiredKey: null,
    icon: Terminal,
  },
  {
    id: 'postgres',
    name: 'PostgreSQL',
    description: 'Relational database schema inspection, query execution, and analysis.',
    transport: 'stdio',
    command: 'npx -y @modelcontextprotocol/server-postgres postgresql://localhost:5432/vigil',
    requiredKey: 'POSTGRES_PASSWORD',
    icon: Database,
  },
  {
    id: 'linear',
    name: 'Linear',
    description: 'Issue tracker, project roadmap, cycles, and team backlog management.',
    transport: 'stdio',
    command: 'npx -y @linear/mcp-server',
    requiredKey: 'LINEAR_API_KEY',
    icon: Server,
  },
];

export function ConnectorsSection() {
  const connectors = useConnectors();
  const syncConnector = useSyncConnector();

  // Vault queries and mutations
  const vaultSecrets = useVaultSecrets();
  const setVaultSecret = useSetVaultSecret();
  const deleteVaultSecret = useDeleteVaultSecret();
  const testVaultSecret = useTestVaultSecret();
  const validateVaultSecret = useValidateVaultSecret();

  // MCP servers queries and mutations
  const mcpServers = useMCPServers();
  const registerMCP = useRegisterMCPServer();
  const unregisterMCP = useUnregisterMCPServer();

  // Local state for key vault form
  const [newKeyName, setNewKeyName] = useState('');
  const [newKeyValue, setNewKeyValue] = useState('');
  const [vaultFeedback, setVaultFeedback] = useState<string | null>(null);
  const [vaultTestFeedback, setVaultTestFeedback] = useState<VaultValidationResult | null>(null);
  const [storedTestResults, setStoredTestResults] = useState<
    Record<string, VaultValidationResult>
  >({});

  // Local state for custom studio
  const [studioOpen, setStudioOpen] = useState(false);
  const [customName, setCustomName] = useState('');
  const [customTransport, setCustomTransport] = useState<'stdio' | 'sse'>('stdio');
  const [customTarget, setCustomTarget] = useState('');
  const [studioFeedback, setStudioFeedback] = useState<string | null>(null);

  const handleSaveSecret = (e: React.FormEvent) => {
    e.preventDefault();
    const key = newKeyName.trim();
    const value = newKeyValue.trim();
    if (!key || !value) return;

    setVaultSecret.mutate(
      { body: { key, value }, scope: `vault:set:${key}` },
      {
        onSuccess: () => {
          setNewKeyName('');
          setNewKeyValue('');
          setVaultTestFeedback(null);
          setVaultFeedback(`Encrypted and saved ${key} to vault.`);
          setTimeout(() => setVaultFeedback(null), 4000);
        },
        onError: (err) => {
          setVaultFeedback(userMessageOf(err));
        },
      }
    );
  };

  const handleTestSecret = () => {
    const key = newKeyName.trim();
    const value = newKeyValue.trim();
    if (!key || !value) return;

    setVaultTestFeedback(null);
    testVaultSecret.mutate(
      { body: { key, value }, scope: `vault:test:${key}` },
      {
        onSuccess: (result) => setVaultTestFeedback(result),
        onError: (err) =>
          setVaultTestFeedback({
            key_name: key,
            provider: 'unknown',
            valid: false,
            status_code: null,
            message: userMessageOf(err),
          }),
      }
    );
  };

  const handleTestStoredSecret = (key: string) => {
    validateVaultSecret.mutate(
      { key, scope: `vault:validate:${key}` },
      {
        onSuccess: (result) =>
          setStoredTestResults((previous) => ({ ...previous, [key]: result })),
        onError: (err) =>
          setStoredTestResults((previous) => ({
            ...previous,
            [key]: {
              key_name: key,
              provider: 'unknown',
              valid: false,
              status_code: null,
              message: userMessageOf(err),
            },
          })),
      }
    );
  };

  const handleRegisterCustomMCP = (e: React.FormEvent) => {
    e.preventDefault();
    const name = customName.trim();
    const target = customTarget.trim();
    if (!name || !target) return;

    registerMCP.mutate(
      {
        body: {
          name,
          transport: customTransport,
          command: customTransport === 'stdio' ? target : undefined,
          url: customTransport === 'sse' ? target : undefined,
        },
        scope: `mcp:register:${name}`,
      },
      {
        onSuccess: () => {
          setCustomName('');
          setCustomTarget('');
          setStudioFeedback(`Registered MCP server "${name}". Discovery started.`);
          setTimeout(() => setStudioFeedback(null), 4000);
        },
        onError: (err) => {
          setStudioFeedback(userMessageOf(err));
        },
      }
    );
  };

  return (
    <div className="space-y-10">
      {IS_MOCK ? (
        <p className="max-w-xl rounded-lg bg-[color-mix(in_srgb,var(--text)_4%,transparent)] px-4 py-3 text-[13px] leading-relaxed text-[var(--text-muted)]">
          Simulation mode: the vault list, saved keys, and provider checks on this page are
          fixtures. Nothing reaches a real provider.
        </p>
      ) : null}

      {/* 1. Linked Connectors */}
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

      {/* 2. AES-256-GCM API Key Vault */}
      <div>
        <div className="flex items-center gap-2">
          <Lock size={18} className="text-[var(--text-muted)]" />
          <h3 className="text-[18px] font-semibold tracking-[-0.015em]">API Key Vault (AES-256-GCM)</h3>
        </div>
        <Blurb>
          Encrypted credentials with authenticated AES-256-GCM using your server's master key.
          Plaintext values are never returned by the backend. Use Test Key to check a key against
          its provider. GROQ_API_KEY powers voice transcription.
        </Blurb>

        {/* Stored secrets list */}
        {vaultSecrets.data && vaultSecrets.data.length > 0 ? (
          <ul className="mb-4 max-w-xl space-y-2">
            {vaultSecrets.data.map((secret) => {
              const testResult = storedTestResults[secret.key_name];
              return (
                <li
                  key={secret.key_name}
                  className="flex items-start justify-between gap-3 rounded-lg bg-[color-mix(in_srgb,var(--text)_4%,transparent)] px-4 py-3"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <Key size={14} className="text-[var(--text-muted)]" />
                      <span className="truncate font-mono text-xs font-semibold text-[var(--text)]">
                        {secret.key_name}
                      </span>
                    </div>
                    <div className="mt-1 flex items-center gap-3 text-[11px] text-[var(--text-muted)]">
                      <span className="font-mono text-[12px] text-[var(--text-muted)]">
                        {secret.masked_value}
                      </span>
                      <span>·</span>
                      <span>Saved {new Date(secret.created_at).toLocaleDateString()}</span>
                    </div>
                    {testResult ? (
                      <p className="mt-1.5 flex items-start gap-1.5 text-[11px] leading-snug text-[var(--text-muted)]">
                        {testResult.valid ? (
                          <Check size={12} className="mt-0.5 shrink-0" />
                        ) : (
                          <X size={12} className="mt-0.5 shrink-0" />
                        )}
                        <span>{testResult.message}</span>
                      </p>
                    ) : null}
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <button
                      type="button"
                      aria-label={`Test ${secret.key_name}`}
                      disabled={validateVaultSecret.isPending}
                      onClick={() => handleTestStoredSecret(secret.key_name)}
                      className="flex h-8 w-8 items-center justify-center rounded-lg text-[var(--text-muted)] transition-colors hover:bg-[color-mix(in_srgb,var(--text)_8%,transparent)] hover:text-[var(--text)] disabled:opacity-40"
                    >
                      <ShieldCheck size={14} strokeWidth={1.5} />
                    </button>
                    <button
                      type="button"
                      aria-label={`Delete ${secret.key_name}`}
                      disabled={deleteVaultSecret.isPending}
                      onClick={() =>
                        deleteVaultSecret.mutate({
                          key: secret.key_name,
                          scope: `vault:delete:${secret.key_name}`,
                        })
                      }
                      className="flex h-8 w-8 items-center justify-center rounded-lg text-[var(--text-muted)] transition-colors hover:bg-[color-mix(in_srgb,var(--text)_8%,transparent)] hover:text-[var(--text)] disabled:opacity-40"
                    >
                      <Trash2 size={14} strokeWidth={1.5} />
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="mb-4 max-w-xl rounded-lg bg-[color-mix(in_srgb,var(--text)_4%,transparent)] px-4 py-3 text-[13px] text-[var(--text-muted)]">
            {vaultSecrets.isLoading ? 'Loading vault secrets...' : 'No API keys stored in vault yet.'}
          </p>
        )}

        {/* Add secret form */}
        <form onSubmit={handleSaveSecret} className="max-w-xl rounded-xl border border-[var(--border)] p-4">
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)]">
            Add or Update Secret Key
          </h4>

          {/* Quick preset suggestion buttons */}
          <div className="mb-3 flex flex-wrap gap-1.5">
            {COMMON_VAULT_KEYS.map((k) => (
              <button
                key={k}
                type="button"
                onClick={() => setNewKeyName(k)}
                className="rounded-md bg-[color-mix(in_srgb,var(--text)_6%,transparent)] px-2 py-1 font-mono text-[11px] text-[var(--text-muted)] transition-colors hover:bg-[color-mix(in_srgb,var(--text)_12%,transparent)] hover:text-[var(--text)]"
              >
                {k}
              </button>
            ))}
          </div>

          <div className="space-y-3">
            <div>
              <label htmlFor="vault-key-name" className="mb-1 block text-xs font-medium text-[var(--text-muted)]">
                Key Name
              </label>
              <input
                id="vault-key-name"
                type="text"
                placeholder="e.g. FIRECRAWL_API_KEY"
                value={newKeyName}
                onChange={(e) => setNewKeyName(e.target.value)}
                className="w-full rounded-lg border border-[var(--border)] bg-[color-mix(in_srgb,var(--text)_4%,transparent)] px-3 py-2 font-mono text-xs text-[var(--text)] outline-none placeholder:text-[var(--text-muted)] focus:border-[var(--text)]"
              />
            </div>
            <div>
              <label htmlFor="vault-secret-val" className="mb-1 block text-xs font-medium text-[var(--text-muted)]">
                Secret Value
              </label>
              <input
                id="vault-secret-val"
                type="password"
                placeholder="Paste secret token..."
                value={newKeyValue}
                onChange={(e) => setNewKeyValue(e.target.value)}
                className="w-full rounded-lg border border-[var(--border)] bg-[color-mix(in_srgb,var(--text)_4%,transparent)] px-3 py-2 font-mono text-xs text-[var(--text)] outline-none placeholder:text-[var(--text-muted)] focus:border-[var(--text)]"
              />
            </div>
            <div className="flex flex-wrap items-center justify-between gap-3 pt-1">
              <div className="flex items-center gap-2">
                <button
                  type="submit"
                  disabled={setVaultSecret.isPending || !newKeyName.trim() || !newKeyValue.trim()}
                  className="flex items-center gap-1.5 rounded-lg bg-[var(--text)] px-4 py-2 text-xs font-medium text-[var(--surface)] transition-all hover:opacity-90 disabled:opacity-40"
                >
                  <Lock size={13} />
                  Save Secret
                </button>
                <button
                  type="button"
                  onClick={handleTestSecret}
                  disabled={testVaultSecret.isPending || !newKeyName.trim() || !newKeyValue.trim()}
                  className="flex items-center gap-1.5 rounded-lg border border-[var(--border)] px-4 py-2 text-xs font-medium transition-colors hover:bg-[color-mix(in_srgb,var(--text)_8%,transparent)] disabled:opacity-40"
                >
                  <ShieldCheck size={13} />
                  {testVaultSecret.isPending ? 'Testing...' : 'Test Key'}
                </button>
              </div>
              {vaultFeedback ? (
                <span className="text-[12px] text-[var(--text)]">{vaultFeedback}</span>
              ) : null}
            </div>
            {vaultTestFeedback ? (
              <p className="mt-2 flex items-start gap-1.5 text-[12px] leading-relaxed text-[var(--text-muted)]">
                {vaultTestFeedback.valid ? (
                  <Check size={13} className="mt-0.5 shrink-0" />
                ) : (
                  <X size={13} className="mt-0.5 shrink-0" />
                )}
                <span>{vaultTestFeedback.message}</span>
              </p>
            ) : null}
          </div>
        </form>
      </div>

      {/* 3. 1-Click MCP Preset Gallery */}
      <div>
        <div className="flex items-center gap-2">
          <Server size={18} className="text-[var(--text-muted)]" />
          <h3 className="text-[18px] font-semibold tracking-[-0.015em]">1-Click MCP Preset Gallery</h3>
        </div>
        <Blurb>
          Standard Model Context Protocol servers configured with optimal transports and vault environment variable injection.
        </Blurb>

        <div className="grid max-w-2xl grid-cols-1 gap-3 sm:grid-cols-2">
          {MCP_PRESETS.map((preset) => {
            const Icon = preset.icon;
            const isRegistered = mcpServers.data?.some(
              (s) => s.name === preset.id || s.name === preset.name
            );
            const keyConfigured =
              !preset.requiredKey ||
              vaultSecrets.data?.some((k) => k.key_name === preset.requiredKey);

            return (
              <div
                key={preset.id}
                className="flex flex-col justify-between rounded-xl border border-[var(--border)] bg-[color-mix(in_srgb,var(--text)_3%,transparent)] p-4 transition-colors hover:border-[color-mix(in_srgb,var(--text)_30%,transparent)]"
              >
                <div>
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-[color-mix(in_srgb,var(--text)_8%,transparent)] text-[var(--text)]">
                        <Icon size={16} />
                      </div>
                      <div>
                        <h4 className="text-sm font-semibold">{preset.name}</h4>
                        <span className="rounded bg-[color-mix(in_srgb,var(--text)_8%,transparent)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--text-muted)]">
                          {preset.transport}
                        </span>
                      </div>
                    </div>
                  </div>
                  <p className="mt-2 text-xs leading-relaxed text-[var(--text-muted)]">
                    {preset.description}
                  </p>
                </div>

                <div className="mt-4 border-t border-[var(--border)] pt-3">
                  <div className="mb-2 flex items-center justify-between text-[11px]">
                    <span className="text-[var(--text-muted)]">Requires:</span>
                    {preset.requiredKey ? (
                      <span
                        className={`font-mono text-[10px] ${
                          keyConfigured ? 'text-[var(--text)]' : 'text-[color-mix(in_srgb,var(--text)_50%,transparent)]'
                        }`}
                      >
                        {preset.requiredKey} {keyConfigured ? '✓' : '(missing)'}
                      </span>
                    ) : (
                      <span className="text-[var(--text-muted)]">None</span>
                    )}
                  </div>

                  <button
                    type="button"
                    disabled={registerMCP.isPending || isRegistered}
                    onClick={() =>
                      registerMCP.mutate({
                        body: {
                          name: preset.id,
                          transport: preset.transport,
                          command: preset.command,
                        },
                        scope: `mcp:preset:${preset.id}`,
                      })
                    }
                    className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-[var(--border)] py-1.5 text-xs font-medium transition-colors hover:bg-[color-mix(in_srgb,var(--text)_8%,transparent)] disabled:opacity-50"
                  >
                    {isRegistered ? (
                      <>
                        <Check size={13} />
                        Connected
                      </>
                    ) : (
                      'Connect'
                    )}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* 4. Custom MCP Server Registration Studio */}
      <div>
        <div className="max-w-2xl rounded-xl border border-[var(--border)]">
          <button
            type="button"
            onClick={() => setStudioOpen(!studioOpen)}
            className="flex w-full items-center justify-between px-4 py-3.5 text-left transition-colors hover:bg-[color-mix(in_srgb,var(--text)_2%,transparent)]"
          >
            <div className="flex items-center gap-2">
              <Terminal size={17} className="text-[var(--text-muted)]" />
              <span className="text-sm font-semibold">Custom MCP Server Studio</span>
            </div>
            {studioOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
          </button>

          {studioOpen ? (
            <div className="border-t border-[var(--border)] p-4">
              <p className="mb-4 text-xs text-[var(--text-muted)]">
                Register a bespoke Model Context Protocol server over stdio command execution or remote HTTP Server-Sent Events (SSE).
              </p>

              <form onSubmit={handleRegisterCustomMCP} className="space-y-3">
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <div>
                    <label htmlFor="custom-mcp-name" className="mb-1 block text-xs font-medium text-[var(--text-muted)]">
                      Server Name
                    </label>
                    <input
                      id="custom-mcp-name"
                      type="text"
                      placeholder="e.g. internal-docs-mcp"
                      value={customName}
                      onChange={(e) => setCustomName(e.target.value)}
                      className="w-full rounded-lg border border-[var(--border)] bg-[color-mix(in_srgb,var(--text)_4%,transparent)] px-3 py-2 font-mono text-xs text-[var(--text)] outline-none focus:border-[var(--text)]"
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-xs font-medium text-[var(--text-muted)]">
                      Transport
                    </label>
                    <div className="grid grid-cols-2 gap-1 rounded-lg border border-[var(--border)] p-0.5">
                      <button
                        type="button"
                        onClick={() => setCustomTransport('stdio')}
                        className={`rounded py-1 text-xs font-mono font-medium transition-colors ${
                          customTransport === 'stdio'
                            ? 'bg-[var(--text)] text-[var(--surface)]'
                            : 'text-[var(--text-muted)] hover:text-[var(--text)]'
                        }`}
                      >
                        stdio
                      </button>
                      <button
                        type="button"
                        onClick={() => setCustomTransport('sse')}
                        className={`rounded py-1 text-xs font-mono font-medium transition-colors ${
                          customTransport === 'sse'
                            ? 'bg-[var(--text)] text-[var(--surface)]'
                            : 'text-[var(--text-muted)] hover:text-[var(--text)]'
                        }`}
                      >
                        sse
                      </button>
                    </div>
                  </div>
                </div>

                <div>
                  <label htmlFor="custom-mcp-target" className="mb-1 block text-xs font-medium text-[var(--text-muted)]">
                    {customTransport === 'stdio' ? 'Command' : 'Endpoint URL'}
                  </label>
                  <input
                    id="custom-mcp-target"
                    type="text"
                    placeholder={
                      customTransport === 'stdio'
                        ? 'e.g. npx -y @modelcontextprotocol/server-fetch'
                        : 'e.g. https://mcp.internal.acme.com/sse'
                    }
                    value={customTarget}
                    onChange={(e) => setCustomTarget(e.target.value)}
                    className="w-full rounded-lg border border-[var(--border)] bg-[color-mix(in_srgb,var(--text)_4%,transparent)] px-3 py-2 font-mono text-xs text-[var(--text)] outline-none focus:border-[var(--text)]"
                  />
                </div>

                <div className="flex items-center justify-between pt-2">
                  <button
                    type="submit"
                    disabled={registerMCP.isPending || !customName.trim() || !customTarget.trim()}
                    className="flex items-center gap-1.5 rounded-lg bg-[var(--text)] px-4 py-2 text-xs font-medium text-[var(--surface)] transition-all hover:opacity-90 disabled:opacity-40"
                  >
                    <Plus size={14} />
                    Register Server
                  </button>
                  {studioFeedback ? (
                    <span className="text-[12px] text-[var(--text)]">{studioFeedback}</span>
                  ) : null}
                </div>
              </form>

              {/* Active MCP Servers list */}
              {mcpServers.data && mcpServers.data.length > 0 ? (
                <div className="mt-5 border-t border-[var(--border)] pt-4">
                  <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)]">
                    Active MCP Servers ({mcpServers.data.length})
                  </h4>
                  <ul className="space-y-1.5">
                    {mcpServers.data.map((srv) => (
                      <li
                        key={srv.id}
                        className="flex items-center justify-between rounded-lg bg-[color-mix(in_srgb,var(--text)_3%,transparent)] px-3 py-2"
                      >
                        <div className="min-w-0">
                          <span className="font-mono text-xs font-medium">{srv.name}</span>
                          <span className="ml-2 font-mono text-[10px] text-[var(--text-muted)]">
                            {srv.transport} · {srv.enabled ? 'Enabled' : 'Discovered'}
                          </span>
                        </div>
                        <button
                          type="button"
                          aria-label={`Unregister ${srv.name}`}
                          disabled={unregisterMCP.isPending}
                          onClick={() =>
                            unregisterMCP.mutate({
                              name: srv.name,
                              scope: `mcp:unregister:${srv.name}`,
                            })
                          }
                          className="rounded p-1 text-[var(--text-muted)] transition-colors hover:text-[var(--text)]"
                        >
                          <Trash2 size={13} />
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
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
