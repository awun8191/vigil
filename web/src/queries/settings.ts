import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { releaseIdempotencyKey } from '@/lib/idempotency';
import type {
  MCPServerResponse,
  RegisterMCPServerRequest,
  ConnectorAccount,
  HealthStatus,
  SystemLimits,
  UserSettings,
  UserSettingsUpdateRequest,
  VaultSecretDTO,
  SetVaultSecretRequest,
} from '@/types';


export const HEALTH_KEY = ['health'] as const;
export const SETTINGS_KEY = ['settings'] as const;
export const LIMITS_KEY = ['settings', 'limits'] as const;
export const CONNECTORS_KEY = ['connectors'] as const;
export const MCP_KEY = ['connectors', 'mcp'] as const;
export const VAULT_KEY = ['settings', 'vault'] as const;


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


