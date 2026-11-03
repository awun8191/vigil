import type {
  Approval,
  ApprovalDecisionRequest,
  ApprovalDecisionResponse,
  AuthMe,
  Card,
  CardFeedbackRequest,
  CardListResponse,
  ChatMessage,
  ChatSession,
  ConnectorAccount,
  HealthStatus,
  MCPDiscoveryResponse,
  MCPServerResponse,
  PairingResponse,
  RegisterMCPServerRequest,
  Run,
  RunCancelResponse,
  RunCreateRequest,
  RunMessageResponse,
  RunStep,
  AgentConfigResponse,
  SkillInstallRequest,
  SkillResponse,
  SyncTrigger,
  SystemLimits,
  UserSettings,
  UserSettingsUpdateRequest,
} from '@/types';
import type { VigilApi } from './contract';
import { ApiError, AuthError, NetworkError } from './errors';
import { idempotencyKey } from './idempotency';
import { createEventStream } from './sse';
import { mockApi } from './mock';

const BASE = (import.meta.env['VITE_API_BASE'] as string | undefined) ?? '/api/v1';

export const IS_MOCK = import.meta.env['VITE_VIGIL_MOCK'] === '1';

/* ------------------------------------------------------------------------------------------- *
 * Auth wiring. The token lives in memory for the session and in IndexedDB across reloads.
 * ------------------------------------------------------------------------------------------- */

let bearerToken: string | null = null;
let authFailureHandler: (() => void) | null = null;
let authFailureFired = false;

export function setBearerToken(token: string | null): void {
  bearerToken = token;
  if (token) authFailureFired = false;
}

export function getBearerToken(): string | null {
  return bearerToken;
}

export function setAuthFailureHandler(handler: (() => void) | null): void {
  authFailureHandler = handler;
}

function notifyAuthFailure(): void {
  // Exactly once per token. Prevents a redirect loop when several queries fail together.
  if (authFailureFired) return;
  authFailureFired = true;
  authFailureHandler?.();
}

/* ------------------------------------------------------------------------------------------- *
 * Request plumbing
 * ------------------------------------------------------------------------------------------- */

interface RequestInit_ {
  method?: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE';
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined>;
  /** Required for any mutating request. Identifies the logical user action, not the attempt. */
  scope?: string;
  signal?: AbortSignal;
}

function buildUrl(path: string, query?: RequestInit_['query']): string {
  const url = `${BASE}${path}`;
  if (!query) return url;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined) continue;
    params.set(key, String(value));
  }
  const qs = params.toString();
  return qs ? `${url}?${qs}` : url;
}

function parseJson(text: string): unknown {
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return null;
  }
}

async function request<T>(path: string, init: RequestInit_ = {}): Promise<T> {
  const method = init.method ?? 'GET';
  const isMutating = method !== 'GET';

  const headers: Record<string, string> = { Accept: 'application/json' };
  if (init.body !== undefined) headers['Content-Type'] = 'application/json';
  if (bearerToken) headers['Authorization'] = `Bearer ${bearerToken}`;

  if (isMutating) {
    // Frontend policy, not a backend contract: the idempotency middleware only manages requests
    // that carry the header, it never rejects a request for missing one. The backend enforces
    // replay, lock conflicts (409) and key validation (422) for managed requests only.
    if (!init.scope) {
      throw new Error(
        `Mutating request ${method} ${path} requires an idempotency scope. ` +
          'The frontend refuses to send an untracked mutation.',
      );
    }
    headers['Idempotency-Key'] = idempotencyKey(init.scope);
  }

  let response: Response;
  try {
    response = await fetch(buildUrl(path, init.query), {
      method,
      headers,
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
      ...(init.signal ? { signal: init.signal } : {}),
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new NetworkError(error instanceof Error ? error.message : 'Network request failed');
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const payload = text ? parseJson(text) : null;

  if (!response.ok) {
    const envelope =
      payload && typeof payload === 'object' ? (payload as Record<string, unknown>) : null;

    if (response.status === 401) {
      notifyAuthFailure();
      throw new AuthError(401, envelope as never);
    }
    throw new ApiError(response.status, envelope as never);
  }

  return payload as T;
}

/* ------------------------------------------------------------------------------------------- *
 * Live implementation
 * ------------------------------------------------------------------------------------------- */

const liveApi: VigilApi = {
  mode: 'live',

  pair(pairingCode, deviceName) {
    return request<PairingResponse>('/auth/pair', {
      method: 'POST',
      body: { pairing_code: pairingCode, device_name: deviceName },
      scope: 'auth:pair',
    });
  },

  me() {
    return request<AuthMe>('/auth/me');
  },

  revokeDevice(deviceId) {
    return request<void>('/auth/revoke', {
      method: 'POST',
      body: { device_id: deviceId },
      scope: `auth:revoke:${deviceId}`,
    });
  },

  listCards(params) {
    return request<CardListResponse>('/cards', {
      query: { limit: params?.limit ?? 50, offset: params?.offset ?? 0 },
    });
  },

  dismissCard(cardId, scope) {
    return request<Card>(`/cards/${cardId}/dismiss`, { method: 'POST', scope });
  },

  cardFeedback(cardId, body: CardFeedbackRequest, scope) {
    return request<Card>(`/cards/${cardId}/feedback`, { method: 'POST', body, scope });
  },

  createRun(body: RunCreateRequest, scope: string) {
    return request<Run>('/runs', {
      method: 'POST',
      body,
      scope,
    });
  },

  getRun(runId) {
    return request<Run>(`/runs/${runId}`);
  },

  async getLatestRun(sessionId) {
    // The newest run 404s on a session with no runs; that is "none", not a failure.
    try {
      return await request<Run>('/runs/latest', { query: { session_id: sessionId } });
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 404) return null;
      throw cause;
    }
  },

  getRunSteps(runId, sinceStep) {
    return request<RunStep[]>(`/runs/${runId}/steps`, {
      query: sinceStep && sinceStep > 0 ? { since_step: sinceStep } : undefined,
    });
  },

  getRunMessages(runId) {
    return request<RunMessageResponse[]>(`/runs/${runId}/messages`);
  },

  getDefaultAgentConfig() {
    return request<AgentConfigResponse>('/runs/configs/default');
  },

  cancelRun(runId, scope) {
    return request<RunCancelResponse>(`/runs/${runId}/cancel`, {
      method: 'POST',
      scope,
    });
  },

  openRunStream(runId, { sinceStep, handlers }) {
    if (!bearerToken) {
      handlers.onStateChange?.('closed');
      return () => undefined;
    }
    return createEventStream({
      url: buildUrl(`/runs/${runId}/events`, sinceStep && sinceStep > 0 ? { since_step: sinceStep } : undefined),
      token: bearerToken,
      onEvent: handlers.onEvent,
      ...(handlers.onStateChange ? { onStateChange: handlers.onStateChange } : {}),
    });
  },

  listApprovals() {
    return request<Approval[]>('/approvals');
  },

  getApproval(approvalId) {
    return request<Approval>(`/approvals/${approvalId}`);
  },

  decideApproval(approvalId, body: ApprovalDecisionRequest, scope) {
    return request<ApprovalDecisionResponse>(`/approvals/${approvalId}/decision`, {
      method: 'POST',
      body,
      scope,
    });
  },

  listSessions() {
    return request<ChatSession[]>('/chat/sessions');
  },

  createSession(title, scope) {
    return request<ChatSession>('/chat/sessions', {
      method: 'POST',
      body: { title: title ?? null },
      scope,
    });
  },

  listMessages(sessionId) {
    return request<ChatMessage[]>(`/chat/sessions/${sessionId}/messages`);
  },

  sendMessage(sessionId, content, scope) {
    return request<ChatMessage>(`/chat/sessions/${sessionId}/messages`, {
      method: 'POST',
      body: { content },
      scope,
    });
  },

  health() {
    return request<HealthStatus>('/health');
  },

  getSettings() {
    return request<UserSettings>('/settings');
  },

  updateSettings(body: UserSettingsUpdateRequest, scope: string) {
    return request<UserSettings>('/settings', {
      method: 'PATCH',
      body,
      scope,
    });
  },

  getLimits() {
    return request<SystemLimits>('/settings/limits');
  },

  listConnectors() {
    return request<ConnectorAccount[]>('/connectors/accounts');
  },

  syncConnector(accountId, scope) {
    return request<SyncTrigger>(`/connectors/accounts/${accountId}/sync`, {
      method: 'POST',
      scope,
    });
  },

  listSkills() {
    return request<SkillResponse[]>('/skills');
  },

  installSkill(body: SkillInstallRequest, scope: string) {
    return request<SkillResponse>('/skills/install', {
      method: 'POST',
      body,
      scope,
    });
  },

  enableSkill(skillId, scope) {
    return request<SkillResponse>(`/skills/${skillId}/enable`, {
      method: 'POST',
      scope,
    });
  },

  disableSkill(skillId, scope) {
    return request<SkillResponse>(`/skills/${skillId}/disable`, {
      method: 'POST',
      scope,
    });
  },

  listMCPServers() {
    return request<MCPServerResponse[]>('/connectors/mcp-servers');
  },

  registerMCPServer(body: RegisterMCPServerRequest, scope: string) {
    return request<MCPDiscoveryResponse>('/connectors/mcp-servers', {
      method: 'POST',
      body,
      scope,
    });
  },

  unregisterMCPServer(name, scope) {
    return request<void>(`/connectors/mcp-servers/${encodeURIComponent(name)}`, {
      method: 'DELETE',
      scope,
    });
  },
};

/**
 * Selected at build time. Never at runtime: a user who loses their token must see an error,
 * not fabricated cards that make the assistant look like it is working.
 */
export const api: VigilApi = IS_MOCK ? mockApi : liveApi;
