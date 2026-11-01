import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { releaseIdempotencyKey } from '@/lib/idempotency';
import type { ConnectorAccount, HealthStatus, SystemLimits, UserSettings } from '@/types';

export const HEALTH_KEY = ['health'] as const;
export const SETTINGS_KEY = ['settings'] as const;
export const LIMITS_KEY = ['settings', 'limits'] as const;
export const CONNECTORS_KEY = ['connectors'] as const;

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
