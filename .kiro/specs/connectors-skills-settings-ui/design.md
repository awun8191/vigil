# Design Document

## Overview

This document describes the technical design for unifying MCP server management with connector accounts, and adding full skill management capabilities to the Vigil settings interface. The feature extends the existing settings architecture with new sections, components, and API integrations while maintaining consistency with established patterns.

## Architecture

### Data Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Settings Page                                   │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐         │
│  │  Navigation     │    │  Section Router │    │  Active Section │         │
│  │  (SECTIONS[])   │───▶│  (useParams)    │───▶│  Component      │         │
│  └─────────────────┘    └─────────────────┘    └─────────────────┘         │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           React Query Layer                                  │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐         │
│  │  useConnectors  │    │  useMCPServers  │    │    useSkills    │         │
│  │  (settings.ts)  │    │  (settings.ts)  │    │   (skills.ts)   │         │
│  └────────┬────────┘    └────────┬────────┘    └────────┬────────┘         │
│           │                      │                      │                   │
│  ┌────────┴────────┐    ┌────────┴────────┐    ┌────────┴────────┐         │
│  │ useSyncConnector│    │useRegisterMCPSvr│    │ useInstallSkill │         │
│  │ useUnregister...│    │                 │    │ useToggleSkill  │         │
│  └─────────────────┘    └─────────────────┘    └─────────────────┘         │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              VigilApi Layer                                  │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  contract.ts (interface)  ◀──────── api.ts / mock.ts (impl)         │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  Methods:                                                                    │
│  • listConnectors() → ConnectorAccount[]                                    │
│  • listMCPServers() → MCPServerResponse[]                                   │
│  • registerMCPServer(req) → MCPDiscoveryResponse (202)                      │
│  • unregisterMCPServer(name) → void (204)                                   │
│  • listSkills() → SkillResponse[]                                           │
│  • installSkill(req) → SkillResponse (202)                                  │
│  • toggleSkill(id, req) → SkillResponse                                     │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Backend API                                     │
│  GET    /api/v1/connectors/accounts         → ConnectorAccount[]            │
│  POST   /api/v1/connectors/accounts/{id}/sync → SyncTriggerResponse         │
│  GET    /api/v1/connectors/mcp-servers      → MCPServerResponse[]           │
│  POST   /api/v1/connectors/mcp-servers      → MCPDiscoveryResponse (202)    │
│  DELETE /api/v1/connectors/mcp-servers/{name} → void (204)                  │
│  GET    /api/v1/skills                      → SkillResponse[]               │
│  POST   /api/v1/skills                      → SkillResponse (202)           │
│  PATCH  /api/v1/skills/{id}                 → SkillResponse                 │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Component Hierarchy

```
SettingsPage
├── Navigation Rail (SECTIONS array)
│   ├── AppearanceSection
│   ├── NotificationsSection
│   ├── ProfileSection
│   ├── SecuritySection
│   ├── AccountSection
│   ├── ConnectorsSection (modified)
│   │   ├── UnifiedConnectorList (new)
│   │   │   ├── ConnectorListItem (existing)
│   │   │   ├── MCPServerListItem (new)
│   │   │   └── TypeBadge (new)
│   │   ├── RegisterMCPServerForm (new)
│   │   └── EmptyState (existing)
│   ├── SkillsSection (new)
│   │   ├── SkillList (new)
│   │   │   └── SkillListItem (new)
│   │   ├── InstallSkillForm (new)
│   │   └── EmptyState (new)
│   ├── UsageSection
│   ├── PersonalizationSection
│   ├── WatchingSection
│   └── VoiceSection
```

## Components and Interfaces

### TypeBadge

A small visual indicator distinguishing item types in the unified list.

```typescript
type BadgeType = 'connector' | 'mcp_server' | 'skill';

interface TypeBadgeProps {
  type: BadgeType;
}

const BADGE_LABELS: Record<BadgeType, string> = {
  connector: 'Connector',
  mcp_server: 'MCP Server',
  skill: 'Skill',
};

function TypeBadge({ type }: TypeBadgeProps) {
  return (
    <span className="rounded-full bg-[color-mix(in_srgb,var(--text)_8%,transparent)] px-2 py-0.5 text-[11px] font-medium">
      {BADGE_LABELS[type]}
    </span>
  );
}
```

### UnifiedConnectorList

Combines connector accounts and MCP servers into a single list with type differentiation.

```typescript
interface UnifiedConnectorListProps {
  connectors: ConnectorAccount[];
  mcpServers: MCPServerResponse[];
  onRefreshConnector: (accountId: string, scope: string) => void;
  onRemoveMCPServer: (name: string, scope: string) => void;
  isRefreshing: boolean;
  isRemoving: boolean;
}

function UnifiedConnectorList({
  connectors,
  mcpServers,
  onRefreshConnector,
  onRemoveMCPServer,
  isRefreshing,
  isRemoving,
}: UnifiedConnectorListProps) {
  const items = [
    ...connectors.map((c) => ({ ...c, itemType: 'connector' as const })),
    ...mcpServers.map((s) => ({ ...s, itemType: 'mcp_server' as const })),
  ];

  if (items.length === 0) {
    return <EmptyState message="No items found" />;
  }

  return (
    <ul className="max-w-xl space-y-2">
      {items.map((item) => (
        item.itemType === 'connector' ? (
          <ConnectorListItem
            key={item.id}
            account={item}
            onRefresh={onRefreshConnector}
            isRefreshing={isRefreshing}
          />
        ) : (
          <MCPServerListItem
            key={item.id}
            server={item}
            onRemove={onRemoveMCPServer}
            isRemoving={isRemoving}
          />
        )
      ))}
    </ul>
  );
}
```

### MCPServerListItem

Renders an MCP server with its metadata and removal action.

```typescript
interface MCPServerListItemProps {
  server: MCPServerResponse;
  onRemove: (name: string, scope: string) => void;
  isRemoving: boolean;
}

function MCPServerListItem({ server, onRemove, isRemoving }: MCPServerListItemProps) {
  return (
    <li className="flex items-center justify-between rounded-lg bg-[color-mix(in_srgb,var(--text)_4%,transparent)] px-4 py-3">
      <span className="min-w-0">
        <span className="flex items-center gap-2">
          <span className="truncate text-sm font-medium">{server.name}</span>
          <TypeBadge type="mcp_server" />
        </span>
        <span className="block text-[12px] text-[var(--text-muted)]">
          {server.transport} · {server.enabled ? 'Enabled' : 'Disabled'}
        </span>
      </span>
      <button
        type="button"
        aria-label={`Remove ${server.name}`}
        disabled={isRemoving}
        onClick={() => onRemove(server.name, `mcp:remove:${server.name}`)}
        className="flex h-8 shrink-0 items-center gap-1.5 rounded-full px-3 text-[12px] font-medium text-[var(--text-muted)] transition-colors hover:bg-[color-mix(in_srgb,var(--text)_8%,transparent)] hover:text-[var(--text)] disabled:opacity-40"
      >
        Remove
      </button>
    </li>
  );
}
```

### RegisterMCPServerForm

Form for registering a new MCP server with conditional fields based on transport type.

```typescript
interface RegisterMCPServerFormProps {
  onSubmit: (payload: RegisterMCPServerRequest, scope: string) => void;
  isSubmitting: boolean;
  error: string | null;
}

function RegisterMCPServerForm({ onSubmit, isSubmitting, error }: RegisterMCPServerFormProps) {
  const [name, setName] = useState('');
  const [transport, setTransport] = useState<'stdio' | 'sse'>('stdio');
  const [command, setCommand] = useState('');
  const [url, setUrl] = useState('');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSubmit(
      {
        name,
        transport,
        command: transport === 'stdio' ? command : null,
        url: transport === 'sse' ? url : null,
      },
      `mcp:register:${name}`,
    );
  };

  return (
    <form onSubmit={handleSubmit} className="max-w-md space-y-4">
      <div>
        <label className="block text-[13px] font-medium">Name</label>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
          maxLength={64}
          className="mt-1 w-full rounded-lg border border-[var(--border)] px-3 py-2 text-sm"
        />
      </div>
      
      <div>
        <label className="block text-[13px] font-medium">Transport</label>
        <select
          value={transport}
          onChange={(e) => setTransport(e.target.value as 'stdio' | 'sse')}
          className="mt-1 w-full rounded-lg border border-[var(--border)] px-3 py-2 text-sm"
        >
          <option value="stdio">stdio</option>
          <option value="sse">sse</option>
        </select>
      </div>

      {transport === 'stdio' ? (
        <div>
          <label className="block text-[13px] font-medium">Command</label>
          <input
            type="text"
            value={command}
            onChange={(e) => setCommand(e.target.value)}
            required
            className="mt-1 w-full rounded-lg border border-[var(--border)] px-3 py-2 text-sm"
          />
        </div>
      ) : (
        <div>
          <label className="block text-[13px] font-medium">URL</label>
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            required
            className="mt-1 w-full rounded-lg border border-[var(--border)] px-3 py-2 text-sm"
          />
        </div>
      )}

      {error && (
        <p role="alert" className="text-[12px] text-[var(--text)]">{error}</p>
      )}

      <button
        type="submit"
        disabled={isSubmitting}
        className="btn btn-primary"
      >
        {isSubmitting ? 'Registering...' : 'Register MCP Server'}
      </button>
    </form>
  );
}
```

### SkillsSection

New settings section for managing installed skills.

```typescript
function SkillsSection() {
  const skills = useSkills();
  const installSkill = useInstallSkill();
  const toggleSkill = useToggleSkill();

  return (
    <div>
      <h2 className="text-[22px] font-semibold tracking-[-0.015em]">Skills</h2>
      <Blurb>
        Skills are agent capabilities installed from Git repositories. Each skill is pinned to a
        specific commit for reproducibility.
      </Blurb>

      <InstallSkillForm
        onSubmit={(payload, scope) => installSkill.mutate({ payload, scope })}
        isSubmitting={installSkill.isPending}
        error={installSkill.isError ? userMessageOf(installSkill.error) : null}
      />

      <div className="mt-6">
        {skills.isLoading ? (
          <p className="text-[13px] text-[var(--text-muted)]">Loading skills...</p>
        ) : skills.data && skills.data.length > 0 ? (
          <SkillList
            skills={skills.data}
            onToggle={(id, enabled, scope) => toggleSkill.mutate({ id, enabled, scope })}
            isToggling={toggleSkill.isPending}
          />
        ) : (
          <EmptyState
            title="No skills installed"
            description="Install a skill by providing its Git repository URL and a commit SHA above."
          />
        )}
      </div>
    </div>
  );
}
```

### SkillList

Renders a list of installed skills with toggle controls.

```typescript
interface SkillListProps {
  skills: SkillResponse[];
  onToggle: (id: string, enabled: boolean, scope: string) => void;
  isToggling: boolean;
}

function SkillList({ skills, onToggle, isToggling }: SkillListProps) {
  return (
    <ul className="max-w-xl space-y-2">
      {skills.map((skill) => (
        <SkillListItem
          key={skill.id}
          skill={skill}
          onToggle={onToggle}
          isToggling={isToggling}
        />
      ))}
    </ul>
  );
}
```

### SkillListItem

Renders a single skill with metadata and enable/disable toggle.

```typescript
interface SkillListItemProps {
  skill: SkillResponse;
  onToggle: (id: string, enabled: boolean, scope: string) => void;
  isToggling: boolean;
}

function SkillListItem({ skill, onToggle, isToggling }: SkillListItemProps) {
  const commitDisplay = skill.source === 'git' && skill.commit
    ? skill.commit.slice(0, 7)
    : null;

  return (
    <li className="flex items-center justify-between rounded-lg bg-[color-mix(in_srgb,var(--text)_4%,transparent)] px-4 py-3">
      <span className="min-w-0">
        <span className="flex items-center gap-2">
          <span className="truncate text-sm font-medium">{skill.name}</span>
          <TypeBadge type="skill" />
          {skill.source === 'builtin' && (
            <span className="rounded-full bg-[color-mix(in_srgb,var(--text)_12%,transparent)] px-2 py-0.5 text-[10px] font-medium">
              Built-in
            </span>
          )}
        </span>
        <span className="block text-[12px] text-[var(--text-muted)]">
          {skill.version}
          {commitDisplay && ` · ${commitDisplay}`}
        </span>
        {skill.description && (
          <span className="block truncate text-[12px] text-[var(--text-muted)]">
            {skill.description}
          </span>
        )}
      </span>
      <label className="relative inline-flex cursor-pointer items-center">
        <input
          type="checkbox"
          checked={skill.enabled}
          onChange={() => onToggle(skill.id, !skill.enabled, `skill:toggle:${skill.id}`)}
          disabled={isToggling}
          className="peer sr-only"
        />
        <div className="peer h-5 w-9 rounded-full bg-[var(--border)] after:absolute after:left-0.5 after:top-0.5 after:h-4 after:w-4 after:rounded-full after:bg-white after:transition-all peer-checked:after:translate-x-4 peer-checked:bg-[var(--text)] peer-focus:ring-2 peer-focus:ring-[var(--text)]" />
      </label>
    </li>
  );
}
```

### InstallSkillForm

Form for installing a skill from a Git repository.

```typescript
interface InstallSkillFormProps {
  onSubmit: (payload: SkillInstallRequest, scope: string) => void;
  isSubmitting: boolean;
  error: string | null;
}

function InstallSkillForm({ onSubmit, isSubmitting, error }: InstallSkillFormProps) {
  const [repoUrl, setRepoUrl] = useState('');
  const [commit, setCommit] = useState('');
  const [validationError, setValidationError] = useState<string | null>(null);

  const isValidCommitSha = (value: string) => /^[0-9a-f]{40}$/i.test(value);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    
    if (!isValidCommitSha(commit)) {
      setValidationError('Commit must be a valid 40-character hexadecimal SHA');
      return;
    }
    
    setValidationError(null);
    onSubmit({ repo_url: repoUrl, commit }, `skill:install:${repoUrl}:${commit}`);
  };

  return (
    <form onSubmit={handleSubmit} className="max-w-md space-y-4">
      <div>
        <label className="block text-[13px] font-medium">Repository URL</label>
        <input
          type="url"
          value={repoUrl}
          onChange={(e) => setRepoUrl(e.target.value)}
          required
          placeholder="https://github.com/org/repo.git"
          className="mt-1 w-full rounded-lg border border-[var(--border)] px-3 py-2 text-sm"
        />
      </div>
      
      <div>
        <label className="block text-[13px] font-medium">Commit SHA</label>
        <input
          type="text"
          value={commit}
          onChange={(e) => setCommit(e.target.value)}
          required
          placeholder="a1b2c3d4e5f6..."
          maxLength={40}
          className="mt-1 w-full rounded-lg border border-[var(--border)] px-3 py-2 font-mono text-sm"
        />
      </div>

      {(validationError || error) && (
        <p role="alert" className="text-[12px] text-[var(--text)]">
          {validationError || error}
        </p>
      )}

      <button
        type="submit"
        disabled={isSubmitting}
        className="btn btn-primary"
      >
        {isSubmitting ? 'Installing...' : 'Install Skill'}
      </button>
    </form>
  );
}
```

## Data Models

### Type Definitions

```typescript
// web/src/types/index.ts

// MCP Server types (matching backend/src/modules/connectors/router.py)
export interface MCPServerResponse {
  id: string;
  name: string;
  transport: 'stdio' | 'sse';
  command: string | null;
  url: string | null;
  enabled: boolean;
  created_at: string;
}

export interface RegisterMCPServerRequest {
  name: string;
  transport: 'stdio' | 'sse';
  command?: string | null;
  url?: string | null;
}

export interface MCPDiscoveryResponse {
  server_name: string;
  job_id: string;
  status: string;
}

// Skill types (matching backend/src/modules/skills/data.py)
export interface SkillResponse {
  id: string;
  name: string;
  description: string;
  version: string;
  source: 'builtin' | 'git' | 'upload';
  commit: string | null;
  enabled: boolean;
  storage_path: string;
  created_at: string;
  updated_at: string;
}

export interface SkillInstallRequest {
  repo_url: string;
  commit: string;
}

export interface SkillToggleRequest {
  enabled: boolean;
}

// RunStatus update (removing stale_thread_paused)
export type RunStatus =
  | 'queued'
  | 'running'
  | 'awaiting_approval'
  | 'awaiting_background_job'
  | 'completed'
  | 'failed';
```

## API Integration

### VigilApi Interface Extensions

```typescript
// web/src/lib/contract.ts

export interface VigilApi {
  // ... existing methods ...
  
  // MCP Server management
  listMCPServers(): Promise<MCPServerResponse[]>;
  registerMCPServer(payload: RegisterMCPServerRequest, scope: string): Promise<MCPDiscoveryResponse>;
  unregisterMCPServer(name: string, scope: string): Promise<void>;
  
  // Skill management
  listSkills(): Promise<SkillResponse[]>;
  installSkill(payload: SkillInstallRequest, scope: string): Promise<SkillResponse>;
  toggleSkill(id: string, payload: SkillToggleRequest, scope: string): Promise<SkillResponse>;
}
```

### API Implementation

```typescript
// web/src/lib/api.ts (additions to liveApi)

const liveApi: VigilApi = {
  // ... existing methods ...

  listMCPServers() {
    return request<MCPServerResponse[]>('/connectors/mcp-servers');
  },

  registerMCPServer(payload, scope) {
    return request<MCPDiscoveryResponse>('/connectors/mcp-servers', {
      method: 'POST',
      body: payload,
      scope,
    });
  },

  unregisterMCPServer(name, scope) {
    return request<void>(`/connectors/mcp-servers/${encodeURIComponent(name)}`, {
      method: 'DELETE',
      scope,
    });
  },

  listSkills() {
    return request<SkillResponse[]>('/skills');
  },

  installSkill(payload, scope) {
    return request<SkillResponse>('/skills', {
      method: 'POST',
      body: payload,
      scope,
    });
  },

  toggleSkill(id, payload, scope) {
    return request<SkillResponse>(`/skills/${id}`, {
      method: 'PATCH',
      body: payload,
      scope,
    });
  },
};
```

### Mock Implementation

```typescript
// web/src/lib/mock.ts (additions to mockApi)

const mcpServers: MCPServerResponse[] = [
  {
    id: 'mcp-1',
    name: 'linear-mcp',
    transport: 'stdio',
    command: 'npx -y @anthropic-ai/linear-mcp',
    url: null,
    enabled: true,
    created_at: iso(-500),
  },
  {
    id: 'mcp-2',
    name: 'slack-mcp',
    transport: 'sse',
    command: null,
    url: 'https://mcp.slack.com/sse',
    enabled: false,
    created_at: iso(-200),
  },
];

const skills: SkillResponse[] = [
  {
    id: 'skill-1',
    name: 'code-review',
    description: 'Automated code review with style enforcement',
    version: '1.2.0',
    source: 'git',
    commit: 'a1b2c3d4e5f6789012345678901234567890abcd',
    enabled: true,
    storage_path: '/skills/code-review',
    created_at: iso(-1000),
    updated_at: iso(-100),
  },
  {
    id: 'skill-2',
    name: 'api-design',
    description: 'REST API design patterns and validation',
    version: '2.0.0',
    source: 'builtin',
    commit: null,
    enabled: true,
    storage_path: '/skills/api-design',
    created_at: iso(-2000),
    updated_at: iso(-2000),
  },
];

export const mockApi: VigilApi = {
  // ... existing methods ...

  listMCPServers() {
    return later([...mcpServers]);
  },

  registerMCPServer(payload, _scope) {
    const existing = mcpServers.find((s) => s.name === payload.name);
    if (existing) {
      return Promise.reject(new Error(`MCP server '${payload.name}' already exists`));
    }
    const server: MCPServerResponse = {
      id: `mcp-${Date.now()}`,
      name: payload.name,
      transport: payload.transport,
      command: payload.command ?? null,
      url: payload.url ?? null,
      enabled: false,
      created_at: new Date().toISOString(),
    };
    mcpServers.push(server);
    return later({
      server_name: payload.name,
      job_id: `job-${Date.now()}`,
      status: 'queued',
    });
  },

  unregisterMCPServer(name, _scope) {
    const index = mcpServers.findIndex((s) => s.name === name);
    if (index === -1) {
      return Promise.reject(new Error(`MCP server '${name}' not found`));
    }
    mcpServers.splice(index, 1);
    return later(undefined);
  },

  listSkills() {
    return later([...skills]);
  },

  installSkill(payload, _scope) {
    const skill: SkillResponse = {
      id: `skill-${Date.now()}`,
      name: payload.repo_url.split('/').pop()?.replace('.git', '') ?? 'unknown',
      description: 'Newly installed skill',
      version: '0.0.0',
      source: 'git',
      commit: payload.commit,
      enabled: false,
      storage_path: `/skills/${Date.now()}`,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    skills.push(skill);
    return later(skill);
  },

  toggleSkill(id, payload, _scope) {
    const skill = skills.find((s) => s.id === id);
    if (!skill) {
      return Promise.reject(new Error(`Skill ${id} not found`));
    }
    skill.enabled = payload.enabled;
    skill.updated_at = new Date().toISOString();
    return later({ ...skill });
  },
};
```

## State Management

### React Query Hooks

```typescript
// web/src/queries/settings.ts (extensions)

export const MCP_SERVERS_KEY = ['mcp-servers'] as const;

export function useMCPServers() {
  return useQuery<MCPServerResponse[]>({
    queryKey: MCP_SERVERS_KEY,
    queryFn: () => api.listMCPServers(),
  });
}

export function useRegisterMCPServer() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ payload, scope }: { payload: RegisterMCPServerRequest; scope: string }) =>
      api.registerMCPServer(payload, scope),

    onSettled: (_data, _error, variables) => {
      releaseIdempotencyKey(variables.scope);
      void queryClient.invalidateQueries({ queryKey: MCP_SERVERS_KEY });
    },
  });
}

export function useUnregisterMCPServer() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ name, scope }: { name: string; scope: string }) =>
      api.unregisterMCPServer(name, scope),

    onSuccess: () => {
      // Optimistic update: remove from cache immediately
      queryClient.setQueryData<MCPServerResponse[]>(MCP_SERVERS_KEY, (old) =>
        old ? old.filter((s) => s.name !== name) : []
      );
    },

    onSettled: (_data, _error, variables) => {
      releaseIdempotencyKey(variables.scope);
      void queryClient.invalidateQueries({ queryKey: MCP_SERVERS_KEY });
    },
  });
}
```

```typescript
// web/src/queries/skills.ts (new file)

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { releaseIdempotencyKey } from '@/lib/idempotency';
import type { SkillResponse, SkillInstallRequest, SkillToggleRequest } from '@/types';

export const SKILLS_KEY = ['skills'] as const;

export function useSkills() {
  return useQuery<SkillResponse[]>({
    queryKey: SKILLS_KEY,
    queryFn: () => api.listSkills(),
  });
}

export function useInstallSkill() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ payload, scope }: { payload: SkillInstallRequest; scope: string }) =>
      api.installSkill(payload, scope),

    onSettled: (_data, _error, variables) => {
      releaseIdempotencyKey(variables.scope);
      void queryClient.invalidateQueries({ queryKey: SKILLS_KEY });
    },
  });
}

export function useToggleSkill() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      id,
      enabled,
      scope,
    }: {
      id: string;
      enabled: boolean;
      scope: string;
    }) => api.toggleSkill(id, { enabled }, scope),

    onSuccess: (data, variables) => {
      // Optimistic update
      queryClient.setQueryData<SkillResponse[]>(SKILLS_KEY, (old) =>
        old ? old.map((s) => (s.id === variables.id ? { ...s, enabled: variables.enabled } : s)) : []
      );
    },

    onSettled: (_data, _error, variables) => {
      releaseIdempotencyKey(variables.scope);
      void queryClient.invalidateQueries({ queryKey: SKILLS_KEY });
    },
  });
}
```

## Error Handling

### Error Display Strategy

All mutations follow a consistent error handling pattern:

1. **Network errors** (no response): Display generic "Network request failed" message
2. **API errors** (4xx/5xx response): Extract `message` from error envelope
3. **Validation errors**: Client-side validation prevents submission with inline error display
4. **Conflict errors** (409): Special handling for duplicate MCP server names

```typescript
// Error display in components
{mutation.isError ? (
  <p role="alert" className="mt-3 max-w-xl text-[12px] text-[var(--text)]">
    {userMessageOf(mutation.error)}
  </p>
) : null}
```

### Conflict Error Handling

```typescript
// In RegisterMCPServerForm component
const [conflictError, setConflictError] = useState<string | null>(null);

const handleRegister = (payload: RegisterMCPServerRequest, scope: string) => {
  setConflictError(null);
  registerMCPServer.mutate(
    { payload, scope },
    {
      onError: (error) => {
        if (error instanceof ApiError && error.status === 409) {
          setConflictError(error.message);
        }
      },
    }
  );
};
```

## Loading States

### Query Loading States

```typescript
// Pattern used across all sections
{query.isLoading ? (
  <p className="text-[13px] text-[var(--text-muted)]">Loading...</p>
) : query.data && query.data.length > 0 ? (
  <List items={query.data} />
) : (
  <EmptyState title="No items" description="Add items using the form above." />
)}
```

### Mutation Loading States

```typescript
// Button loading state
<button
  type="submit"
  disabled={mutation.isPending}
  className="btn btn-primary"
>
  {mutation.isPending ? 'Submitting...' : 'Submit'}
</button>

// Spinner on existing action buttons
<RefreshCw
  size={13}
  strokeWidth={1.5}
  className={mutation.isPending ? 'animate-spin' : undefined}
/>
```

### Background Install States

For skills with `status: 'installing'`, the UI displays an inline indicator:

```typescript
{skill.status === 'installing' && (
  <span className="flex items-center gap-1.5 text-[12px] text-[var(--text-muted)]">
    <Loader2 size={12} className="animate-spin" />
    Installing...
  </span>
)}
```

## Navigation Structure

### Updated SECTIONS Array

```typescript
// web/src/components/settings/SettingsPage.tsx

import { Wrench } from 'lucide-react';

const SECTIONS: SectionDef[] = [
  { id: 'appearance', label: 'Appearance', Icon: Sun, body: <AppearanceSection /> },
  { id: 'notifications', label: 'Notifications', Icon: Bell, body: <NotificationsSection /> },
  { id: 'profile', label: 'Profile', Icon: UserRound, body: <ProfileSection /> },
  { id: 'security', label: 'Security & login', Icon: ShieldCheck, body: <SecuritySection /> },
  { id: 'account', label: 'Account', Icon: CircleUser, body: <AccountSection /> },
  { id: 'connectors', label: 'Connectors', Icon: Plug, body: <ConnectorsSection /> },
  { id: 'skills', label: 'Skills', Icon: Wrench, body: <SkillsSection /> },  // NEW
  { id: 'usage', label: 'Usage', Icon: Gauge, body: <UsageSection /> },
  {
    id: 'personalization',
    label: 'Personalization',
    Icon: SlidersHorizontal,
    body: <PersonalizationSection />,
  },
  { id: 'watching', label: 'Watching', Icon: Eye, body: <WatchingSection /> },
  { id: 'voice', label: 'Voice', Icon: Mic, body: <VoiceSection /> },
];
```

## Correctness Properties

*Property is a characteristic or behavior that should hold true across all valid executions of a system-essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Unified List Contains All Items

*For any* set of connector accounts and MCP servers returned by the backend, the unified connector list SHALL render all items with correct type badges.

**Validates: Requirements 1.1, 1.2**

### Property 2: MCP Server Form Conditional Fields

*For any* transport type selection (stdio or sse), the MCP server registration form SHALL display the appropriate conditional field: command for stdio, url for sse.

**Validates: Requirements 2.1**

### Property 3: MCP Server List Item Rendering

*For any* MCP server with valid name, transport, and enabled status, the rendered list item SHALL display the server name, transport type, and enabled status in the correct format.

**Validates: Requirements 1.4**

### Property 4: Commit SHA Validation

*For any* user input to the commit field that is not a valid 40-character hexadecimal SHA, the skill installation form SHALL reject submission and display a validation error.

**Validates: Requirements 5.5**

### Property 5: Skill List Item Rendering

*For any* skill with name, description, version, source, and enabled status, the rendered list item SHALL display all required fields including the appropriate source badge or commit truncation.

**Validates: Requirements 4.2, 8.3**

### Property 6: Git Source Commit Truncation

*For any* skill with `source: 'git'` and a non-null commit SHA, the displayed commit SHALL be truncated to exactly the first 7 characters.

**Validates: Requirements 8.4**

### Property 7: Cache Invalidation on Mutation

*For any* successful mutation (register, unregister, install, toggle), the relevant React Query cache SHALL be invalidated, triggering a refetch of the corresponding list.

**Validates: Requirements 7.3**

### Property 8: Error Propagation

*For any* failed mutation, the error SHALL be accessible to the component for display to the user.

**Validates: Requirements 7.4**

## Testing Strategy

### Unit Testing

Unit tests will verify individual component behavior and pure functions:

- **TypeBadge rendering**: Verify correct labels for each type (connector, mcp_server, skill)
- **Commit SHA truncation**: Test the truncate function with various SHA lengths and edge cases
- **Form validation**: Test commit SHA validation regex against valid and invalid inputs
- **Conditional field rendering**: Verify command/url fields appear/disappear based on transport type

### Integration Testing

Integration tests will verify API interactions and state management:

- **Mutation hooks**: Verify cache invalidation after successful mutations
- **Error handling**: Test error propagation from API through hooks to components
- **Navigation**: Verify Skills section appears at correct position in navigation
- **Form submission**: Test complete registration/installation flows with mocked API

### Property-Based Testing

Property-based tests will verify universal properties across generated inputs:

1. **Unified list rendering**: Generate random connector accounts and MCP servers, verify all appear with correct badges
2. **Form conditional fields**: Generate random transport selections, verify correct fields appear
3. **Commit validation**: Generate random strings, verify only valid 40-char hex SHAs pass
4. **Skill rendering**: Generate random skills, verify all required fields appear
5. **Cache invalidation**: Run mutations with various payloads, verify cache updates

### Test Configuration

- **Framework**: Vitest with React Testing Library
- **Property testing**: fast-check for property-based tests
- **Coverage target**: 80% line coverage for new code
- **Mock strategy**: Use mockApi for unit tests, MSW for integration tests
