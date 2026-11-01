import type {
  Approval,
  ApprovalDecisionRequest,
  ApprovalDecisionResponse,
  AuthMe,
  CardFeedbackRequest,
  CardListResponse,
  Card,
  ChatMessage,
  ChatSession,
  ConnectorAccount,
  HealthStatus,
  PairingResponse,
  Run,
  SseEvent,
  StreamState,
  SyncTrigger,
  SystemLimits,
  UserSettings,
} from '@/types';

export interface StreamHandlers {
  onEvent: (event: SseEvent) => void;
  onStateChange?: (state: StreamState) => void;
}

/**
 * The surface the UI codes against. `lib/api.ts` provides the real implementation,
 * `lib/mock.ts` provides the fixture-backed one. The two are interchangeable and are selected
 * at build time by `VITE_VIGIL_MOCK`, never at runtime.
 */
export interface VigilApi {
  readonly mode: 'live' | 'mock';

  pair(pairingCode: string, deviceName: string): Promise<PairingResponse>;
  me(): Promise<AuthMe>;
  revokeDevice(deviceId: string): Promise<void>;

  listCards(params?: { limit?: number; offset?: number }): Promise<CardListResponse>;
  dismissCard(cardId: string, scope: string): Promise<Card>;
  cardFeedback(cardId: string, body: CardFeedbackRequest, scope: string): Promise<Card>;

  /**
   * Card actions with `action_type: 'run'` post here. The payload is whatever the server attached
   * to the action, merged with the card id. The client never invents a `config_id`: if the payload
   * lacks one the backend rejects it and the UI surfaces that message.
   */
  createRun(payload: Record<string, unknown>, cardId: string, scope: string): Promise<Run>;

  listApprovals(): Promise<Approval[]>;
  getApproval(approvalId: string): Promise<Approval>;
  decideApproval(
    approvalId: string,
    body: ApprovalDecisionRequest,
    scope: string,
  ): Promise<ApprovalDecisionResponse>;

  listSessions(): Promise<ChatSession[]>;
  createSession(title: string | undefined, scope: string): Promise<ChatSession>;
  listMessages(sessionId: string): Promise<ChatMessage[]>;
  sendMessage(sessionId: string, content: string, scope: string): Promise<ChatMessage>;

  /**
   * Subscribe to the session stream. Returns an unsubscribe function.
   *
   * The backend stream is `GET /chat/sessions/{id}/stream?q=<prompt>` and emits bare
   * `data: <token>` frames terminated by `data: [DONE]`. The prompt travels in the query string,
   * not in a preceding POST.
   */
  openStream(sessionId: string, prompt: string, handlers: StreamHandlers): () => void;

  health(): Promise<HealthStatus>;
  getSettings(): Promise<UserSettings>;
  getLimits(): Promise<SystemLimits>;
  listConnectors(): Promise<ConnectorAccount[]>;
  syncConnector(accountId: string, scope: string): Promise<SyncTrigger>;
}
