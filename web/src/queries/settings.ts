import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { releaseIdempotencyKey } from '@/lib/idempotency';
import type {
  MCPServerResponse,
  MCPToolResponse,
  RegisterMCPServerRequest,
  UpdateMCPToolLevelRequest,
  ConnectorAccount,
  HealthStatus,
  SystemLimits,
  UserSettings,
  UserSettingsUpdateRequest,
  VaultSecretDTO,
  SetVaultSecretRequest,
  PersonalizationResponse,
  PersonalizationUpdateRequest,
  MemoryChunkListResponse,
  MemorySearchResponse,
} from '@/types';


export const HEALTH_KEY = ['health'] as const;
export const SETTINGS_KEY = ['settings'] as const;
export const LIMITS_KEY = ['settings', 'limits'] as const;
export const CONNECTORS_KEY = ['connectors'] as const;
export const MCP_KEY = ['connectors', 'mcp'] as const;
export const VAULT_KEY = ['settings', 'vault'] as const;
export const PERSONALIZATION_KEY = ['settings', 'personalization'] as const;
export const MEMORY_CHUNKS_KEY = ['settings', 'memory', 'chunks'] as const;


export function useHealth() {
  return useQuery<HealthStatus>({
    queryKey: HEALTH_KEY,
    queryFn: () => api.health(),
    refetchInterval: 30_000,
    retry: 1,
  });
}

export function useUserSettings() {
  return useQuery<UserSettings>({
    queryKey: SETTINGS_KEY,
    queryFn: () => api.getSettings(),
  });
}

/** PATCH /settings with an all-optional body, the backend merges missing fields. */
export function useUpdateSettings() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      body,
      scope,
    }: {
      body: UserSettingsUpdateRequest;
      scope: string;
    }) => api.updateSettings(body, scope),

    onSuccess: (updated: UserSettings) => {
      queryClient.setQueryData<UserSettings>(SETTINGS_KEY, updated);
    },

    onSettled: (_data, _error, variables) => {
      releaseIdempotencyKey(variables.scope);
      void queryClient.invalidateQueries({ queryKey: SETTINGS_KEY });
    },
  });
}

export function useLimits() {
  return useQuery<SystemLimits>({
    queryKey: LIMITS_KEY,
    queryFn: () => api.getLimits(),
  });
}

export function useConnectors() {
  return useQuery<ConnectorAccount[]>({
    queryKey: CONNECTORS_KEY,
    queryFn: () => api.listConnectors(),
  });
}

export function useSyncConnector() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ accountId, scope }: { accountId: string; scope: string }) =>
      api.syncConnector(accountId, scope),

    onSettled: (_data, _error, variables) => {
      releaseIdempotencyKey(variables.scope);
      void queryClient.invalidateQueries({ queryKey: CONNECTORS_KEY });
    },
  });
}

/* --- MCP servers --------------------------------------------------------------------------- */

/**
 * Registration returns 202 with a discovery job id, not the server row. The row appears in
 * the list polling or on the next refetch, `status: 'queued'` in the discovery response is
 * the signal the discovery job was accepted.
 */
export function useMCPServers() {
  return useQuery<MCPServerResponse[]>({
    queryKey: MCP_KEY,
    queryFn: () => api.listMCPServers(),
  });
}

export function useRegisterMCPServer() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      body,
      scope,
    }: {
      body: RegisterMCPServerRequest;
      scope: string;
    }) => {
      if (body.transport === 'stdio' && !body.command) {
        return Promise.reject(new Error('A stdio server needs a command'));
      }
      if (body.transport === 'sse' && !body.url) {
        return Promise.reject(new Error('An SSE server needs a URL'));
      }
      return api.registerMCPServer(body, scope);
    },

    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: MCP_KEY });
    },

    onSettled: (_data, _error, variables) => {
      releaseIdempotencyKey(variables.scope);
    },
  });
}

export function useUnregisterMCPServer() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ name, scope }: { name: string; scope: string }) =>
      api.unregisterMCPServer(name, scope),

    onMutate: async ({ name }) => {
      await queryClient.cancelQueries({ queryKey: MCP_KEY });
      const previous = queryClient.getQueryData<MCPServerResponse[]>(MCP_KEY);
      queryClient.setQueryData<MCPServerResponse[]>(MCP_KEY, (current) =>
        current ? current.filter((server) => server.name !== name) : current,
      );
      return { previous };
    },

    onError: (_error, _variables, context) => {
      if (context?.previous) queryClient.setQueryData(MCP_KEY, context.previous);
    },

    onSettled: (_data, _error, variables) => {
      releaseIdempotencyKey(variables.scope);
      void queryClient.invalidateQueries({ queryKey: MCP_KEY });
    },
  });
}

export function useMCPServerTools(serverName: string | null) {
  return useQuery<MCPToolResponse[]>({
    queryKey: [...MCP_KEY, serverName, 'tools'] as const,
    queryFn: () => api.listMCPServerTools(serverName!),
    enabled: Boolean(serverName),
  });
}

export function useUpdateMCPToolLevel() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      serverName,
      toolName,
      body,
      scope,
    }: {
      serverName: string;
      toolName: string;
      body: UpdateMCPToolLevelRequest;
      scope: string;
    }) => api.updateMCPToolLevel(serverName, toolName, body, scope),

    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({
        queryKey: [...MCP_KEY, variables.serverName, 'tools'],
      });
    },

    onSettled: (_data, _error, variables) => {
      releaseIdempotencyKey(variables.scope);
    },
  });
}

export function useRefreshMCPServer() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ name, scope }: { name: string; scope: string }) =>
      api.refreshMCPServer(name, scope),

    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: MCP_KEY });
      void queryClient.invalidateQueries({
        queryKey: [...MCP_KEY, variables.name, 'tools'],
      });
    },

    onSettled: (_data, _error, variables) => {
      releaseIdempotencyKey(variables.scope);
    },
  });
}

/* --- Key Vault ---------------------------------------------------------------------------- */

export function useVaultSecrets() {
  return useQuery<VaultSecretDTO[]>({
    queryKey: VAULT_KEY,
    queryFn: () => api.listVaultSecrets(),
  });
}

export function useSetVaultSecret() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ body, scope }: { body: SetVaultSecretRequest; scope: string }) =>
      api.setVaultSecret(body, scope),

    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: VAULT_KEY });
    },

    onSettled: (_data, _error, variables) => {
      releaseIdempotencyKey(variables.scope);
    },
  });
}

export function useTestVaultSecret() {
  return useMutation({
    mutationFn: ({ body, scope }: { body: SetVaultSecretRequest; scope: string }) =>
      api.testVaultSecret(body, scope),

    onSettled: (_data, _error, variables) => {
      releaseIdempotencyKey(variables.scope);
    },
  });
}

export function useValidateVaultSecret() {
  return useMutation({
    mutationFn: ({ key, scope }: { key: string; scope: string }) =>
      api.validateVaultSecret(key, scope),

    onSettled: (_data, _error, variables) => {
      releaseIdempotencyKey(variables.scope);
    },
  });
}

export function useDeleteVaultSecret() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ key, scope }: { key: string; scope: string }) =>
      api.deleteVaultSecret(key, scope),

    onMutate: async ({ key }) => {
      await queryClient.cancelQueries({ queryKey: VAULT_KEY });
      const previous = queryClient.getQueryData<VaultSecretDTO[]>(VAULT_KEY);
      queryClient.setQueryData<VaultSecretDTO[]>(VAULT_KEY, (current) =>
        current ? current.filter((secret) => secret.key_name !== key) : current,
      );
      return { previous };
    },

    onError: (_error, _variables, context) => {
      if (context?.previous) queryClient.setQueryData(VAULT_KEY, context.previous);
    },

    onSettled: (_data, _error, variables) => {
      releaseIdempotencyKey(variables.scope);
      void queryClient.invalidateQueries({ queryKey: VAULT_KEY });
    },
  });
}

/* --- Personalization & Hermes-style Memory (SOUL.md, USER.md, MEMORY.md) ------------------- */

export function usePersonalization() {
  return useQuery<PersonalizationResponse>({
    queryKey: PERSONALIZATION_KEY,
    queryFn: () => api.getPersonalization(),
  });
}

export function useUpdatePersonalization() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      body,
      scope,
    }: {
      body: PersonalizationUpdateRequest;
      scope: string;
    }) => api.updatePersonalization(body, scope),

    onSuccess: (updated: PersonalizationResponse) => {
      queryClient.setQueryData<PersonalizationResponse>(PERSONALIZATION_KEY, updated);
    },

    onSettled: (_data, _error, variables) => {
      releaseIdempotencyKey(variables.scope);
      void queryClient.invalidateQueries({ queryKey: PERSONALIZATION_KEY });
    },
  });
}

/* --- Long-Term Semantic Memory Chunks ------------------------------------------------------ */

export function useMemoryChunks(limit = 50, offset = 0) {
  return useQuery<MemoryChunkListResponse>({
    queryKey: [...MEMORY_CHUNKS_KEY, limit, offset] as const,
    queryFn: () => api.listMemoryChunks({ limit, offset, include_excluded: true }),
  });
}

export function useSearchMemory(query: string) {
  const trimmed = query.trim();
  return useQuery<MemorySearchResponse>({
    queryKey: ['settings', 'memory', 'search', trimmed] as const,
    queryFn: () => api.searchMemory(trimmed, 12),
    enabled: trimmed.length >= 2,
  });
}

export function useExcludeMemoryChunk() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      chunkId,
      excluded,
      scope,
    }: {
      chunkId: string;
      excluded: boolean;
      scope: string;
    }) => api.excludeMemoryChunk(chunkId, excluded, scope),

    onSettled: (_data, _error, variables) => {
      releaseIdempotencyKey(variables.scope);
      void queryClient.invalidateQueries({ queryKey: MEMORY_CHUNKS_KEY });
    },
  });
}

export function useDeleteMemoryChunk() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ chunkId, scope }: { chunkId: string; scope: string }) =>
      api.deleteMemoryChunk(chunkId, scope),

    onSettled: (_data, _error, variables) => {
      releaseIdempotencyKey(variables.scope);
      void queryClient.invalidateQueries({ queryKey: MEMORY_CHUNKS_KEY });
    },
  });
}


