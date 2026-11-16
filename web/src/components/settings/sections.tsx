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
import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { userMessageOf } from '@/lib/errors';
import { api, IS_MOCK } from '@/lib/api';
import type { BrowserDriverChoice, MCPServerResponse, VaultValidationResult } from '@/types';
import {
  useConnectors,
  useDeleteMemoryChunk,
  useDeleteVaultSecret,
  useExcludeMemoryChunk,
  useHealth,
  useLimits,
  useMCPServerTools,
  useMCPServers,
  useMemoryChunks,
  usePersonalization,
  useRefreshMCPServer,
  useRegisterMCPServer,
  useSearchMemory,
  useSetVaultSecret,
  useSyncConnector,
  useTestVaultSecret,
  useUnregisterMCPServer,
  useUpdateMCPToolLevel,
  useUpdatePersonalization,
  useUpdateSettings,
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
  const setDisplayName = useSession((state) => state.setDisplayName);
  const personalization = usePersonalization();
  const updatePersonalization = useUpdatePersonalization();
  const [nameDraft, setNameDraft] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);

  const currentName =
    nameDraft !== null
      ? nameDraft
      : personalization.data?.display_name || device?.user_display_name || 'Paired user';

  const handleSaveName = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = currentName.trim();
    if (!trimmed) return;
    updatePersonalization.mutate(
      {
        body: { display_name: trimmed },
        scope: `personalization:name:${Date.now()}`,
      },
      {
        onSuccess: (updated) => {
          setNameDraft(null);
          setDisplayName(updated.display_name);
          setFeedback('Saved your name to profile and USER.md.');
          setTimeout(() => setFeedback(null), 3500);
        },
        onError: (err) => setFeedback(userMessageOf(err)),
      },
    );
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-[22px] font-semibold tracking-[-0.015em]">Profile</h2>
        <Blurb>
          Your name is synced with Vigil&apos;s persistent user profile (<code>USER.md</code>) so the
          agent knows who it is collaborating with across every session.
        </Blurb>

        <form
          onSubmit={handleSaveName}
          className="mb-4 max-w-xl rounded-xl border border-[var(--border)] p-4"
        >
          <label
            htmlFor="profile-display-name"
            className="mb-1.5 block text-xs font-medium text-[var(--text-muted)]"
          >
            Preferred Name
          </label>
          <div className="flex items-center gap-2">
            <input
              id="profile-display-name"
              type="text"
              value={currentName}
              onChange={(e) => setNameDraft(e.target.value)}
              placeholder="e.g. Nas"
              className="flex-1 rounded-lg border border-[var(--border)] bg-[color-mix(in_srgb,var(--text)_4%,transparent)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--text)]"
            />
            <button
              type="submit"
              disabled={updatePersonalization.isPending || !currentName.trim()}
              className="rounded-lg bg-[var(--text)] px-4 py-2 text-xs font-medium text-[var(--surface)] transition-opacity hover:opacity-90 disabled:opacity-40"
            >
              {updatePersonalization.isPending ? 'Saving...' : 'Save Name'}
            </button>
          </div>
          {feedback ? (
            <p className="mt-2 text-xs text-[var(--text-muted)]">{feedback}</p>
          ) : null}
        </form>

        <FieldList>
          <Row label="Active Name" value={personalization.data?.display_name || device?.user_display_name || 'Paired user'} />
          <Row label="User ID" value={device?.user_id ?? 'unknown'} />
        </FieldList>
      </div>
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
    id: 'playwright',
    name: 'Playwright CUA',
    description: 'Interactive Chromium browser automation and DOM/coordinate CUA driver over MCP.',
    transport: 'stdio',
    command: 'npx -y @playwright/mcp@latest --headless',
    requiredKey: null,
    icon: Terminal,
  },
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

function MCPServerItem({
  server,
  onUnregister,
  unregisterPending,
}: {
  server: MCPServerResponse;
  onUnregister: (name: string) => void;
  unregisterPending: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const toolsQuery = useMCPServerTools(expanded ? server.name : null);
  const updateToolLevel = useUpdateMCPToolLevel();
  const refreshServer = useRefreshMCPServer();

  return (
    <li className="rounded-lg bg-[color-mix(in_srgb,var(--text)_3%,transparent)] px-3 py-2.5">
      <div className="flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
        >
          {expanded ? (
            <ChevronDown size={13} className="shrink-0 text-[var(--text-muted)]" />
          ) : (
            <ChevronRight size={13} className="shrink-0 text-[var(--text-muted)]" />
          )}
          <span className="truncate font-mono text-xs font-medium">{server.name}</span>
          <span className="shrink-0 font-mono text-[10px] text-[var(--text-muted)]">
            {server.transport} · {server.enabled ? 'Enabled' : 'Discovered'}
          </span>
        </button>
        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            aria-label={`Refresh tools for ${server.name}`}
            title="Refresh tools"
            disabled={refreshServer.isPending}
            onClick={() =>
              refreshServer.mutate({
                name: server.name,
                scope: `mcp:refresh:${server.name}`,
              })
            }
            className="rounded p-1 text-[var(--text-muted)] transition-colors hover:text-[var(--text)] disabled:opacity-40"
          >
            <RefreshCw size={12} className={refreshServer.isPending ? 'animate-spin' : undefined} />
          </button>
          <button
            type="button"
            aria-label={`Unregister ${server.name}`}
            disabled={unregisterPending}
            onClick={() => onUnregister(server.name)}
            className="rounded p-1 text-[var(--text-muted)] transition-colors hover:text-[var(--text)] disabled:opacity-40"
          >
            <Trash2 size={13} />
          </button>
        </div>
      </div>

      {expanded ? (
        <div className="mt-2.5 border-t border-[var(--border)] pt-2.5">
          {toolsQuery.isLoading ? (
            <p className="text-[11px] text-[var(--text-muted)]">Loading discovered tools...</p>
          ) : toolsQuery.data && toolsQuery.data.length > 0 ? (
            <ul className="space-y-1.5">
              {toolsQuery.data.map((tool) => {
                const isLocal = tool.level === 'local' || tool.level === 'read';
                return (
                  <li
                    key={tool.name}
                    className="flex items-center justify-between gap-3 rounded bg-[color-mix(in_srgb,var(--text)_3%,transparent)] px-2.5 py-1.5"
                  >
                    <div className="min-w-0">
                      <span className="block truncate font-mono text-[11px] font-medium text-[var(--text)]">
                        {tool.name}
                      </span>
                      {tool.description ? (
                        <span className="block truncate text-[10px] text-[var(--text-muted)]">
                          {tool.description}
                        </span>
                      ) : null}
                    </div>
                    <button
                      type="button"
                      disabled={updateToolLevel.isPending}
                      onClick={() =>
                        updateToolLevel.mutate({
                          serverName: server.name,
                          toolName: tool.name,
                          body: { level: isLocal ? 'external' : 'local' },
                          scope: `mcp:level:${server.name}:${tool.name}`,
                        })
                      }
                      className={`shrink-0 rounded px-2 py-0.5 font-mono text-[10px] font-medium transition-colors ${
                        isLocal
                          ? 'bg-emerald-500/15 text-emerald-400 hover:bg-emerald-500/25'
                          : 'bg-[color-mix(in_srgb,var(--text)_8%,transparent)] text-[var(--text-muted)] hover:text-[var(--text)]'
                      }`}
                    >
                      {isLocal ? 'Auto (Local)' : 'Approve (External)'}
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="text-[11px] text-[var(--text-muted)]">
              No tools discovered yet. Click refresh to re-run discovery.
            </p>
          )}
        </div>
      ) : null}
    </li>
  );
}

export function ConnectorsSection() {
  const queryClient = useQueryClient();
  const connectors = useConnectors();
  const syncConnector = useSyncConnector();
  const [gmailConnecting, setGmailConnecting] = useState(false);
  const [connectorFeedback, setConnectorFeedback] = useState<string | null>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const connected = params.get('connected');
    const oauthError = params.get('oauth_error');
    if (connected) {
      setConnectorFeedback(`Connected ${connected} account successfully. Initial sync started.`);
      void queryClient.invalidateQueries({ queryKey: ['connectors'] });
    } else if (oauthError) {
      setConnectorFeedback(`OAuth connection error: ${oauthError}`);
    }
  }, [queryClient]);

  const handleConnectGmail = async () => {
    setGmailConnecting(true);
    setConnectorFeedback(null);
    try {
      const res = await api.authorizeGoogleConnector('gmail');
      if (res.authorization_url && res.authorization_url !== '#mock-google-oauth') {
        window.location.href = res.authorization_url;
      } else {
        setConnectorFeedback('Simulation mode: Google OAuth redirect skipped.');
      }
    } catch (err) {
      setConnectorFeedback(userMessageOf(err));
    } finally {
      setGmailConnecting(false);
    }
  };

  const handleDisconnectAccount = async (accountId: string) => {
    try {
      await api.deleteConnectorAccount(accountId, `connector:delete:${accountId}`);
      void queryClient.invalidateQueries({ queryKey: ['connectors'] });
    } catch (err) {
      setConnectorFeedback(userMessageOf(err));
    }
  };
  const userSettings = useUserSettings();
  const updateSettings = useUpdateSettings();

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

      {/* 0. Default Browser & CUA Driver */}
      <section aria-labelledby="browser-driver-heading" className="space-y-4">
        <div className="space-y-1">
          <h3 id="browser-driver-heading" className="text-[14px] font-medium text-[var(--text)]">
            Default Browser &amp; CUA Driver
          </h3>
          <p className="text-[13px] text-[var(--text-muted)]">
            Choose which browser backend the agent uses by default for built-in{' '}
            <code className="font-mono text-[12px]">browser_*</code> tools, and enable or disable
            individual drivers. Connected MCP browser tools (such as Playwright MCP) also remain
            separately callable by the agent.
          </p>
        </div>

        <div className="rounded-lg border border-[color-mix(in_srgb,var(--text)_10%,transparent)] bg-[color-mix(in_srgb,var(--text)_2%,transparent)] p-4 space-y-4">
          <div className="space-y-2">
            <label
              htmlFor="default-browser-driver"
              className="block text-[12px] font-medium text-[var(--text)]"
            >
              Default browser driver
            </label>
            <select
              id="default-browser-driver"
              aria-label="Default browser driver"
              value={userSettings.data?.default_browser_driver ?? 'auto'}
              disabled={userSettings.isLoading || updateSettings.isPending}
              onChange={(e) => {
                const next = e.target.value as BrowserDriverChoice;
                updateSettings.mutate({
                  body: { default_browser_driver: next },
                  scope: `browser-driver-${next}`,
                });
              }}
              className="w-full max-w-md rounded-md border border-[color-mix(in_srgb,var(--text)_14%,transparent)] bg-[var(--surface)] px-3 py-2 text-[13px] text-[var(--text)] focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
            >
              <option value="auto">Auto (Firecrawl if key set → Local CUA/CDP → MCP)</option>
              <option value="cdp">Local Chromium CUA / CDP (interactive local browser)</option>
              <option value="firecrawl">Firecrawl Cloud Browser (/v2/interact)</option>
              <option value="mcp">MCP Browser Bridge (delegates to Playwright/Puppeteer MCP)</option>
              <option value="disabled">Disabled (turn off built-in browser_* tools)</option>
            </select>
          </div>

          <div className="space-y-2 pt-1 border-t border-[color-mix(in_srgb,var(--text)_8%,transparent)]">
            <div className="text-[12px] font-medium text-[var(--text)]">
              Enabled browser drivers
            </div>
            <div className="grid gap-2 sm:grid-cols-3">
              {(
                [
                  {
                    id: 'cdp',
                    label: 'Local CUA / CDP',
                    desc: 'Built-in Chromium interactive driver',
                  },
                  {
                    id: 'firecrawl',
                    label: 'Firecrawl Browser',
                    desc: 'Cloud CUA via FIRECRAWL_API_KEY',
                  },
                  {
                    id: 'mcp',
                    label: 'MCP Browser Bridge',
                    desc: 'Route browser_* to connected MCP',
                  },
                ] as const
              ).map((driver) => {
                const disabledList = userSettings.data?.disabled_browser_drivers ?? [];
                const isEnabled = !disabledList.includes(driver.id);
                return (
                  <label
                    key={driver.id}
                    className="flex items-start gap-2.5 rounded-md border border-[color-mix(in_srgb,var(--text)_10%,transparent)] px-3 py-2.5 cursor-pointer hover:bg-[color-mix(in_srgb,var(--text)_3%,transparent)]"
                  >
                    <input
                      type="checkbox"
                      checked={isEnabled}
                      disabled={userSettings.isLoading || updateSettings.isPending}
                      onChange={() => {
                        const nextDisabled = isEnabled
                          ? [...disabledList, driver.id]
                          : disabledList.filter((d) => d !== driver.id);
                        updateSettings.mutate({
                          body: { disabled_browser_drivers: nextDisabled },
                          scope: `browser-disabled-${driver.id}-${!isEnabled}`,
                        });
                      }}
                      className="mt-0.5 h-4 w-4 rounded border-[color-mix(in_srgb,var(--text)_25%,transparent)]"
                    />
                    <span className="space-y-0.5">
                      <span className="block text-[12px] font-medium text-[var(--text)]">
                        {driver.label}
                      </span>
                      <span className="block text-[11px] text-[var(--text-muted)]">
                        {driver.desc}
                      </span>
                    </span>
                  </label>
                );
              })}
            </div>
          </div>
        </div>
      </section>

      {/* 1. Linked Connectors */}
      <div>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-[22px] font-semibold tracking-[-0.015em]">Connectors</h2>
          <button
            type="button"
            disabled={gmailConnecting}
            onClick={() => void handleConnectGmail()}
            className="inline-flex h-9 items-center gap-1.5 rounded-full bg-[var(--btn-primary-bg)] px-4 text-[12.5px] font-semibold text-[var(--btn-primary-ink)] transition-transform hover:scale-[1.02] active:scale-95 disabled:opacity-50"
          >
            <Plus size={14} strokeWidth={2} />
            {gmailConnecting ? 'Connecting...' : 'Connect Gmail'}
          </button>
        </div>
        <Blurb>
          Linked accounts feed Vigil the signals it watches over. Refreshing asks the backend to re-poll
          the connector now.
        </Blurb>

        {connectorFeedback ? (
          <p className="mb-4 max-w-xl rounded-lg border border-[var(--border)] bg-[color-mix(in_srgb,var(--text)_4%,transparent)] px-4 py-2.5 text-[12.5px] text-[var(--text)]">
            {connectorFeedback}
          </p>
        ) : null}

        {connectors.data && connectors.data.length > 0 ? (
          <ul className="max-w-xl space-y-2">
            {connectors.data.map((account) => (
              <li
                key={account.id}
                className="flex items-center justify-between gap-2 rounded-lg bg-[color-mix(in_srgb,var(--text)_4%,transparent)] px-4 py-3"
              >
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium">{account.account_label}</span>
                  <span className="block text-[12px] text-[var(--text-muted)]">
                    {account.connector_id} · {account.status.replace(/_/g, ' ')}
                  </span>
                </span>
                <div className="flex shrink-0 items-center gap-1">
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
                  <button
                    type="button"
                    aria-label={`Disconnect ${account.account_label}`}
                    onClick={() => void handleDisconnectAccount(account.id)}
                    className="flex h-8 w-8 items-center justify-center rounded-full text-[var(--text-muted)] transition-colors hover:bg-[color-mix(in_srgb,var(--text)_8%,transparent)] hover:text-rose-500"
                  >
                    <Trash2 size={13} strokeWidth={1.5} />
                  </button>
                </div>
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
            </div>
          ) : null}
        </div>
      </div>

      {/* 5. Connected MCP Servers & Tool Permissions */}
      {mcpServers.data && mcpServers.data.length > 0 ? (
        <div className="max-w-2xl">
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)]">
            Connected MCP Servers ({mcpServers.data.length})
          </h4>
          <ul className="space-y-2">
            {mcpServers.data.map((srv) => (
              <MCPServerItem
                key={srv.id}
                server={srv}
                unregisterPending={unregisterMCP.isPending}
                onUnregister={(name) =>
                  unregisterMCP.mutate({
                    name,
                    scope: `mcp:unregister:${name}`,
                  })
                }
              />
            ))}
          </ul>
        </div>
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

function MemoryEntriesEditor({
  title,
  subtitle,
  badge,
  entries,
  usage,
  placeholder,
  isSaving,
  onSaveEntries,
}: {
  title: string;
  subtitle: string;
  badge: string;
  entries: string[];
  usage: { current: number; limit: number };
  placeholder: string;
  isSaving: boolean;
  onSaveEntries: (nextEntries: string[]) => void;
}) {
  const [newEntry, setNewEntry] = useState('');
  const [editingIdx, setEditingIdx] = useState<number | null>(null);
  const [editingText, setEditingText] = useState('');

  const pct = Math.min(100, Math.round((usage.current / Math.max(1, usage.limit)) * 100));

  const handleAdd = (e: React.FormEvent) => {
    e.preventDefault();
    const cleaned = newEntry.trim();
    if (!cleaned) return;
    onSaveEntries([...entries, cleaned]);
    setNewEntry('');
  };

  const handleDelete = (idx: number) => {
    const next = entries.filter((_, i) => i !== idx);
    onSaveEntries(next);
  };

  const handleSaveEdit = (idx: number) => {
    const cleaned = editingText.trim();
    if (!cleaned) return;
    const next = entries.map((item, i) => (i === idx ? cleaned : item));
    onSaveEntries(next);
    setEditingIdx(null);
    setEditingText('');
  };

  return (
    <div className="max-w-2xl rounded-xl border border-[var(--border)] bg-[color-mix(in_srgb,var(--text)_2%,transparent)] p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-[16px] font-semibold tracking-[-0.01em]">{title}</h3>
            <span className="rounded bg-[color-mix(in_srgb,var(--text)_8%,transparent)] px-2 py-0.5 font-mono text-[11px] text-[var(--text-muted)]">
              {badge}
            </span>
          </div>
          <p className="mt-1 text-xs leading-relaxed text-[var(--text-muted)]">{subtitle}</p>
        </div>
        <div className="text-right font-mono text-[11px] text-[var(--text-muted)]">
          {usage.current} / {usage.limit} chars ({pct}%)
        </div>
      </div>

      {/* Character budget bar */}
      <div className="mt-2.5 h-1.5 w-full overflow-hidden rounded-full bg-[color-mix(in_srgb,var(--text)_8%,transparent)]">
        <div
          className="h-full rounded-full transition-all"
          style={{
            width: `${pct}%`,
            background: 'var(--text)',
            opacity: pct > 90 ? 0.95 : 0.65,
          }}
        />
      </div>

      {/* Entries list */}
      {entries.length > 0 ? (
        <ul className="mt-4 space-y-2">
          {entries.map((entry, idx) => (
            <li
              key={`${idx}-${entry.slice(0, 24)}`}
              className="rounded-lg border border-[var(--border)] bg-[var(--surface-solid)] px-3.5 py-2.5 text-xs"
            >
              {editingIdx === idx ? (
                <div className="space-y-2">
                  <textarea
                    rows={2}
                    value={editingText}
                    onChange={(e) => setEditingText(e.target.value)}
                    className="w-full rounded-md border border-[var(--border)] bg-[color-mix(in_srgb,var(--text)_4%,transparent)] p-2 font-mono text-xs text-[var(--text)] outline-none focus:border-[var(--text)]"
                  />
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      disabled={isSaving || !editingText.trim()}
                      onClick={() => handleSaveEdit(idx)}
                      className="rounded-md bg-[var(--text)] px-3 py-1 text-[11px] font-medium text-[var(--surface)]"
                    >
                      Save
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setEditingIdx(null);
                        setEditingText('');
                      }}
                      className="rounded-md border border-[var(--border)] px-3 py-1 text-[11px] text-[var(--text-muted)]"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <div className="flex items-start justify-between gap-3">
                  <p className="whitespace-pre-wrap leading-relaxed text-[var(--text)]">{entry}</p>
                  <div className="flex shrink-0 items-center gap-1">
                    <button
                      type="button"
                      disabled={isSaving}
                      onClick={() => {
                        setEditingIdx(idx);
                        setEditingText(entry);
                      }}
                      className="rounded px-2 py-1 text-[11px] text-[var(--text-muted)] transition-colors hover:bg-[color-mix(in_srgb,var(--text)_8%,transparent)] hover:text-[var(--text)]"
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      aria-label={`Remove entry ${idx + 1}`}
                      disabled={isSaving}
                      onClick={() => handleDelete(idx)}
                      className="rounded p-1 text-[var(--text-muted)] transition-colors hover:bg-[color-mix(in_srgb,var(--text)_8%,transparent)] hover:text-[var(--text)]"
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                </div>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-4 rounded-lg bg-[color-mix(in_srgb,var(--text)_4%,transparent)] px-3.5 py-3 text-xs text-[var(--text-muted)]">
          No entries recorded yet. Vigil adds entries automatically as it learns about you, or you can add one below.
        </p>
      )}

      {/* Add new entry form */}
      <form onSubmit={handleAdd} className="mt-3 flex items-center gap-2">
        <input
          type="text"
          value={newEntry}
          onChange={(e) => setNewEntry(e.target.value)}
          placeholder={placeholder}
          className="flex-1 rounded-lg border border-[var(--border)] bg-[color-mix(in_srgb,var(--text)_4%,transparent)] px-3 py-2 text-xs text-[var(--text)] outline-none placeholder:text-[var(--text-muted)] focus:border-[var(--text)]"
        />
        <button
          type="submit"
          disabled={isSaving || !newEntry.trim()}
          className="flex shrink-0 items-center gap-1 rounded-lg bg-[var(--text)] px-3.5 py-2 text-xs font-medium text-[var(--surface)] transition-opacity hover:opacity-90 disabled:opacity-40"
        >
          <Plus size={13} />
          Add Entry
        </button>
      </form>
    </div>
  );
}

export function PersonalizationSection() {
  const settings = useUserSettings();
  const personalization = usePersonalization();
  const updatePersonalization = useUpdatePersonalization();
  const setDisplayName = useSession((state) => state.setDisplayName);

  const [soulDraft, setSoulDraft] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);

  if (!settings.data && settings.isLoading) {
    return (
      <div>
        <h2 className="text-[22px] font-semibold tracking-[-0.015em]">Personalization</h2>
        <Blurb>Checking personalization and memory stores...</Blurb>
      </div>
    );
  }

  const pData = personalization.data;
  const activeSoul = soulDraft !== null ? soulDraft : (pData?.soul_md ?? '');
  const soulLimit = pData?.soul_usage.limit ?? 8000;
  const approvals = settings.data ? Object.entries(settings.data.approval_defaults) : [];

  const handleSaveSoul = (e: React.FormEvent) => {
    e.preventDefault();
    if (!activeSoul.trim()) return;
    updatePersonalization.mutate(
      {
        body: { soul_md: activeSoul },
        scope: `personalization:soul:${Date.now()}`,
      },
      {
        onSuccess: () => {
          setSoulDraft(null);
          setFeedback('Saved SOUL.md personality markdown. Active on the next turn.');
          setTimeout(() => setFeedback(null), 4000);
        },
        onError: (err) => setFeedback(userMessageOf(err)),
      },
    );
  };

  const handleResetSoul = () => {
    updatePersonalization.mutate(
      {
        body: { reset_soul: true },
        scope: `personalization:soul:reset:${Date.now()}`,
      },
      {
        onSuccess: (updated) => {
          setSoulDraft(updated.soul_md);
          setFeedback('Reset SOUL.md to the default Vigil persona.');
          setTimeout(() => setFeedback(null), 4000);
        },
        onError: (err) => setFeedback(userMessageOf(err)),
      },
    );
  };

  const handleSaveUserEntries = (nextEntries: string[]) => {
    updatePersonalization.mutate(
      {
        body: { user_entries: nextEntries },
        scope: `personalization:user:${Date.now()}`,
      },
      {
        onSuccess: (updated) => {
          if (updated.display_name) {
            setDisplayName(updated.display_name);
          }
          setFeedback('Updated USER.md profile & opinion store.');
          setTimeout(() => setFeedback(null), 3500);
        },
        onError: (err) => setFeedback(userMessageOf(err)),
      },
    );
  };

  const handleSaveMemoryEntries = (nextEntries: string[]) => {
    updatePersonalization.mutate(
      {
        body: { memory_entries: nextEntries },
        scope: `personalization:memory:${Date.now()}`,
      },
      {
        onSuccess: () => {
          setFeedback('Updated MEMORY.md agent notes.');
          setTimeout(() => setFeedback(null), 3500);
        },
        onError: (err) => setFeedback(userMessageOf(err)),
      },
    );
  };

  return (
    <div className="space-y-8">
      <div>
        <h2 className="text-[22px] font-semibold tracking-[-0.015em]">Personalization &amp; Memory</h2>
        <Blurb>
          Hermes-style persistent identity and curated memory. <code>SOUL.md</code> defines Vigil&apos;s
          personality and voice in Slot #1 of the system prompt. <code>USER.md</code> holds your
          profile and Vigil&apos;s evolving opinion of how you work, and <code>MEMORY.md</code> stores
          long-term environment facts across sessions.
        </Blurb>
        {feedback ? (
          <p className="mb-4 max-w-2xl rounded-lg bg-[color-mix(in_srgb,var(--text)_6%,transparent)] px-3.5 py-2 text-xs text-[var(--text)]">
            {feedback}
          </p>
        ) : null}
      </div>

      {/* 1. SOUL.md — Configurable Personality Markdown */}
      <form
        onSubmit={handleSaveSoul}
        className="max-w-2xl rounded-xl border border-[var(--border)] bg-[color-mix(in_srgb,var(--text)_2%,transparent)] p-4"
      >
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-[16px] font-semibold tracking-[-0.01em]">
                Agent Personality &amp; Identity
              </h3>
              <span className="rounded bg-[color-mix(in_srgb,var(--text)_8%,transparent)] px-2 py-0.5 font-mono text-[11px] text-[var(--text-muted)]">
                ~/.vigil/SOUL.md
              </span>
            </div>
            <p className="mt-1 text-xs leading-relaxed text-[var(--text-muted)]">
              Injected into Slot #1 of the system prompt at the start of every run. You can edit it
              here or ask Vigil in chat to adjust its personality.
            </p>
          </div>
          <div className="font-mono text-[11px] text-[var(--text-muted)]">
            {activeSoul.length} / {soulLimit} chars
          </div>
        </div>

        {/* Persona starter presets */}
        {pData?.presets && pData.presets.length > 0 ? (
          <div className="mt-3 flex flex-wrap gap-1.5">
            {pData.presets.map((preset) => (
              <button
                key={preset.id}
                type="button"
                title={preset.description}
                onClick={() => setSoulDraft(preset.content)}
                className="rounded-md border border-[var(--border)] bg-[color-mix(in_srgb,var(--text)_4%,transparent)] px-2.5 py-1 text-[11px] font-medium text-[var(--text-muted)] transition-colors hover:border-[var(--text)] hover:text-[var(--text)]"
              >
                {preset.name}
              </button>
            ))}
          </div>
        ) : null}

        <textarea
          aria-label="SOUL.md Personality Markdown"
          rows={10}
          value={activeSoul}
          onChange={(e) => setSoulDraft(e.target.value)}
          placeholder="# Vigil — Core Identity & Persona..."
          className="mt-3 w-full rounded-lg border border-[var(--border)] bg-[var(--surface-solid)] p-3 font-mono text-xs leading-relaxed text-[var(--text)] outline-none focus:border-[var(--text)]"
        />

        <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <button
              type="submit"
              disabled={updatePersonalization.isPending || !activeSoul.trim()}
              className="rounded-lg bg-[var(--text)] px-4 py-2 text-xs font-medium text-[var(--surface)] transition-opacity hover:opacity-90 disabled:opacity-40"
            >
              {updatePersonalization.isPending ? 'Saving...' : 'Save SOUL.md'}
            </button>
            <button
              type="button"
              disabled={updatePersonalization.isPending}
              onClick={handleResetSoul}
              className="rounded-lg border border-[var(--border)] px-3.5 py-2 text-xs font-medium text-[var(--text-muted)] transition-colors hover:bg-[color-mix(in_srgb,var(--text)_8%,transparent)] hover:text-[var(--text)]"
            >
              Reset to Default
            </button>
          </div>
        </div>
      </form>

      {/* 2. USER.md — User Profile & Evolving Opinion */}
      <MemoryEntriesEditor
        title="User Profile & Agent's Opinion"
        subtitle="Facts about you (name, role, preferences) and Vigil's candid, evolving impression of how you think and work."
        badge="~/.vigil/memories/USER.md"
        entries={pData?.user_entries ?? []}
        usage={pData?.user_usage ?? { current: 0, limit: 1500 }}
        placeholder="e.g. Name: Nas, or Prefers direct architectural trade-offs over summaries..."
        isSaving={updatePersonalization.isPending}
        onSaveEntries={handleSaveUserEntries}
      />

      {/* 3. MEMORY.md — Curated Agent Notes */}
      <MemoryEntriesEditor
        title="Curated Agent Notes"
        subtitle="Durable environment facts, project conventions, tool quirks, and lessons learned across sessions."
        badge="~/.vigil/memories/MEMORY.md"
        entries={pData?.memory_entries ?? []}
        usage={pData?.memory_usage ?? { current: 0, limit: 2200 }}
        placeholder="e.g. Backend tests run with uv run pytest..."
        isSaving={updatePersonalization.isPending}
        onSaveEntries={handleSaveMemoryEntries}
      />

      {/* 4. Engine Tier & Approval Defaults */}
      {settings.data ? (
        <div className="max-w-2xl">
          <h3 className="mb-2 text-[15px] font-semibold tracking-[-0.01em]">
            Engine Tier &amp; Approval Defaults
          </h3>
          <FieldList>
            <Row label="Preferred model tier" value={settings.data.preferred_model_tier} />
            {approvals.map(([tool, choice]) => (
              <Row key={tool} label={`Ask first, ${tool}`} value={choice.replace(/_/g, ' ')} />
            ))}
          </FieldList>
        </div>
      ) : null}
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

/* ----------------------------------------------------------------------- Memory ---------- */

export function MemorySection() {
  const personalization = usePersonalization();
  const updatePersonalization = useUpdatePersonalization();
  const chunks = useMemoryChunks(50, 0);
  const excludeChunk = useExcludeMemoryChunk();
  const deleteChunk = useDeleteMemoryChunk();

  const [searchInput, setSearchInput] = useState('');
  const searchResults = useSearchMemory(searchInput);
  const [feedback, setFeedback] = useState<string | null>(null);

  const pData = personalization.data;
  const userEntries = pData?.user_entries ?? [];
  const memoryEntries = pData?.memory_entries ?? [];

  const handleSaveUserEntries = (nextEntries: string[]) => {
    updatePersonalization.mutate(
      {
        body: { user_entries: nextEntries },
        scope: `memory:user:${Date.now()}`,
      },
      {
        onSuccess: () => {
          setFeedback('Updated USER.md profile & opinion store.');
          setTimeout(() => setFeedback(null), 3500);
        },
        onError: (err) => setFeedback(userMessageOf(err)),
      },
    );
  };

  const handleSaveMemoryEntries = (nextEntries: string[]) => {
    updatePersonalization.mutate(
      {
        body: { memory_entries: nextEntries },
        scope: `memory:notes:${Date.now()}`,
      },
      {
        onSuccess: () => {
          setFeedback('Updated MEMORY.md agent notes.');
          setTimeout(() => setFeedback(null), 3500);
        },
        onError: (err) => setFeedback(userMessageOf(err)),
      },
    );
  };

  return (
    <div className="space-y-8">
      <div>
        <h2 className="text-[22px] font-semibold tracking-[-0.015em]">Memory</h2>
        <Blurb>
          Everything Vigil remembers about you across sessions: curated profile &amp; opinion notes (
          <code>USER.md</code>), persistent agent notes (<code>MEMORY.md</code>), and indexed
          long-term semantic conversation chunks.
        </Blurb>
        {feedback ? (
          <p className="mb-4 max-w-2xl rounded-lg bg-[color-mix(in_srgb,var(--text)_6%,transparent)] px-3.5 py-2 text-xs text-[var(--text)]">
            {feedback}
          </p>
        ) : null}
      </div>

      {/* 1. Curated USER.md */}
      <MemoryEntriesEditor
        title="User Profile & Agent's Opinion"
        subtitle="Facts about you (name, role, preferences) and Vigil's evolving impression of how you think and work."
        badge="USER.md"
        entries={userEntries}
        usage={pData?.user_usage ?? { current: 0, limit: 1500 }}
        placeholder="e.g. Name: Nas, or Prefers direct architectural trade-offs..."
        isSaving={updatePersonalization.isPending}
        onSaveEntries={handleSaveUserEntries}
      />

      {/* 2. Curated MEMORY.md */}
      <MemoryEntriesEditor
        title="Curated Agent Notes"
        subtitle="Durable environment facts, project conventions, tool quirks, and lessons learned."
        badge="MEMORY.md"
        entries={memoryEntries}
        usage={pData?.memory_usage ?? { current: 0, limit: 2200 }}
        placeholder="e.g. Backend tests run with uv run pytest..."
        isSaving={updatePersonalization.isPending}
        onSaveEntries={handleSaveMemoryEntries}
      />

      {/* 3. Semantic Long-Term Memory Chunks (pgvector) */}
      <div className="max-w-2xl space-y-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div>
            <h3 className="text-[16px] font-semibold tracking-[-0.01em]">
              Long-Term Semantic Memory
            </h3>
            <p className="mt-1 text-xs leading-relaxed text-[var(--text-muted)]">
              Indexed conversation chunks searched by semantic similarity and exponential time-decay.
            </p>
          </div>
          <span className="font-mono text-[11px] text-[var(--text-muted)]">
            {chunks.data ? `${chunks.data.total} chunks` : chunks.isLoading ? 'Loading...' : '0 chunks'}
          </span>
        </div>

        {/* Semantic search bar */}
        <div className="flex items-center gap-2">
          <input
            type="search"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Search long-term semantic memory (min 2 chars)..."
            aria-label="Search long-term semantic memory"
            className="flex-1 rounded-lg border border-[var(--border)] bg-[color-mix(in_srgb,var(--text)_4%,transparent)] px-3 py-2 text-xs text-[var(--text)] outline-none placeholder:text-[var(--text-muted)] focus:border-[var(--text)]"
          />
          {searchInput ? (
            <button
              type="button"
              onClick={() => setSearchInput('')}
              className="rounded-lg border border-[var(--border)] px-3 py-2 text-xs text-[var(--text-muted)] hover:text-[var(--text)]"
            >
              Clear
            </button>
          ) : null}
        </div>

        {/* Search results view when querying */}
        {searchInput.trim().length >= 2 ? (
          <div className="rounded-xl border border-[var(--border)] bg-[color-mix(in_srgb,var(--text)_2%,transparent)] p-4">
            <div className="mb-3 flex items-center justify-between text-xs text-[var(--text-muted)]">
              <span>
                Semantic matches for <strong className="text-[var(--text)]">&ldquo;{searchInput.trim()}&rdquo;</strong>
              </span>
              {searchResults.data?.path ? (
                <span className="font-mono text-[11px]">path: {searchResults.data.path}</span>
              ) : null}
            </div>
            {searchResults.isLoading ? (
              <p className="text-xs text-[var(--text-muted)]">Searching memory...</p>
            ) : searchResults.data && searchResults.data.results.length > 0 ? (
              <ul className="space-y-2">
                {searchResults.data.results.map((item) => (
                  <li
                    key={item.id}
                    className="rounded-lg border border-[var(--border)] bg-[var(--surface-solid)] p-3 text-xs"
                  >
                    <div className="mb-1.5 flex flex-wrap items-center gap-2 font-mono text-[10px] text-[var(--text-muted)]">
                      <span>score: {item.final_score.toFixed(3)}</span>
                      <span>·</span>
                      <span>sim: {item.similarity.toFixed(3)}</span>
                      <span>·</span>
                      <span>age: {item.age_days.toFixed(1)}d</span>
                    </div>
                    {item.summary ? (
                      <p className="mb-1 font-medium text-[var(--text)]">{item.summary}</p>
                    ) : null}
                    <p className="whitespace-pre-wrap leading-relaxed text-[var(--text-secondary)]">
                      {item.content}
                    </p>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-[var(--text-muted)]">No matching semantic chunks found.</p>
            )}
          </div>
        ) : null}

        {/* Full stored chunks list */}
        {chunks.isLoading ? (
          <p className="rounded-lg bg-[color-mix(in_srgb,var(--text)_4%,transparent)] px-4 py-3 text-xs text-[var(--text-muted)]">
            Loading stored memory chunks...
          </p>
        ) : chunks.data && chunks.data.items.length > 0 ? (
          <ul className="space-y-2">
            {chunks.data.items.map((chunk) => (
              <li
                key={chunk.id}
                className={`rounded-xl border border-[var(--border)] bg-[color-mix(in_srgb,var(--text)_2%,transparent)] p-3.5 text-xs ${
                  chunk.excluded ? 'opacity-55' : ''
                }`}
              >
                <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2 font-mono text-[10px] text-[var(--text-muted)]">
                    <span className="rounded bg-[color-mix(in_srgb,var(--text)_8%,transparent)] px-1.5 py-0.5 text-[var(--text)]">
                      {chunk.scope}
                    </span>
                    <span>{new Date(chunk.created_at).toLocaleString()}</span>
                    <span>·</span>
                    <span>{chunk.embedding_model}</span>
                    {chunk.excluded ? (
                      <span className="rounded border border-[var(--border)] px-1.5 py-0.5 text-[var(--text-muted)]">
                        excluded
                      </span>
                    ) : null}
                  </div>
                  <div className="flex items-center gap-1.5">
                    <button
                      type="button"
                      disabled={excludeChunk.isPending}
                      onClick={() =>
                        excludeChunk.mutate({
                          chunkId: chunk.id,
                          excluded: !chunk.excluded,
                          scope: `memory:exclude:${chunk.id}:${Date.now()}`,
                        })
                      }
                      className="rounded-md border border-[var(--border)] px-2 py-1 text-[11px] text-[var(--text-muted)] transition-colors hover:border-[var(--text)] hover:text-[var(--text)]"
                    >
                      {chunk.excluded ? 'Include' : 'Exclude'}
                    </button>
                    <button
                      type="button"
                      aria-label={`Delete chunk ${chunk.id}`}
                      disabled={deleteChunk.isPending}
                      onClick={() =>
                        deleteChunk.mutate({
                          chunkId: chunk.id,
                          scope: `memory:delete:${chunk.id}:${Date.now()}`,
                        })
                      }
                      className="rounded-md p-1 text-[var(--text-muted)] transition-colors hover:bg-[color-mix(in_srgb,var(--text)_8%,transparent)] hover:text-[var(--text)]"
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                </div>
                {chunk.summary ? (
                  <p className="mb-1 font-medium text-[var(--text)]">{chunk.summary}</p>
                ) : null}
                <p className="whitespace-pre-wrap leading-relaxed text-[var(--text)]">
                  {chunk.content}
                </p>
              </li>
            ))}
          </ul>
        ) : (
          <div className="rounded-xl border border-[var(--border)] px-6 py-10 text-center">
            <p className="text-sm font-medium">No semantic memory chunks indexed yet.</p>
            <p className="mt-1 text-xs text-[var(--text-muted)]">
              Completed chat sessions are automatically chunked and embedded into long-term memory.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
