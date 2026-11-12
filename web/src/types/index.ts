/**
 * Wire types for the Vigil API.
 *
 * These mirror `backend/src/modules/<module>/data.py` exactly. Do not add fields the backend
 * does not emit, and do not rename fields to something tidier. `session_id` stays `session_id`.
 */

export type Urgency = 'critical' | 'high' | 'medium' | 'low';

export type CardStatus =
  | 'new'
  | 'seen'
  | 'in_progress'
  | 'awaiting_approval'
  | 'done'
  | 'dismissed'
  | 'expired';

export interface CardAction {
  id: string;
  label: string;
  action_type: string;
  payload: Record<string, unknown>;
  verb?: string | null;
}

export interface Card {
  id: string;
  user_id: string;
  item_id: string | null;
  thread_id: string | null;
  type: string;
  urgency: Urgency;
  title: string;
  summary: string | null;
  due_at: string | null;
  status: CardStatus;
  actions: CardAction[];
  feedback: string | null;
  created_at: string;
  updated_at: string;
  proactivity_tag?: string | null;
  verb?: string | null;
}

export interface CardListResponse {
  items: Card[];
  total: number;
  limit: number;
  offset: number;
}

/** Request body of POST /cards/{id}/feedback. The backend schema accepts `note`, but
 *  cards/router.py currently records only the feedback string, so the UI does not send
 *  `note` until the router consumes it. */
export interface CardFeedbackRequest {
  feedback: 'useful' | 'not_useful' | 'dismissed' | 'spam';
}

export type ApprovalStatus = 'pending' | 'approved' | 'rejected' | 'stale';

export interface Approval {
  id: string;
  run_id: string;
  tool_call_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  status: ApprovalStatus;
  decided_at: string | null;
  decision_note: string | null;
}

export interface ApprovalDecisionRequest {
  decision: 'approved' | 'rejected';
  decision_note?: string | null;
}

export interface ApprovalDecisionResponse {
  approval_id: string;
  status: string;
  run_id: string;
  resumed: boolean;
}

export interface ChatSession {
  session_id: string;
  user_id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface ChatMessage {
  id: string;
  session_id: string;
  role: 'user' | 'assistant' | 'system' | string;
  content: string;
  created_at: string;
  run_id?: string | null;
}

/** A message rendered in the stream, including optimistic and streaming-local entries.
 *  The local states are produced by `queries/chat.ts` and rendered by `MessageItem`. */
export interface StreamMessage extends ChatMessage {
  local_state?: 'sending' | 'failed' | 'streaming';
  failure_reason?: string;
  failure_class?: FailureClass;
  idempotency_scope?: string;
}

export type FailureClass = 'network' | 'rejected' | 'replayed';

export type RunStatus =
  | 'queued'
  | 'running'
  | 'awaiting_approval'
  | 'awaiting_background_job'
  | 'completed'
  | 'failed';

/** Body of POST /runs. `config_id` is required by the backend, there is no server default. */
export interface RunCreateRequest {
  config_id: string;
  card_id?: string | null;
  initial_prompt?: string | null;
  /** Chat session that owns the run; the session receives a memory index job on completion. */
  session_id?: string | null;
}

/** Response of GET /runs/configs/default, the server-side default agent configuration,
 *  without the system prompt text. */
export interface AgentConfigResponse {
  id: string;
  name: string;
  model: string;
  tools: string[];
  skills: string[];
  memory_scopes: string[];
  limits: Record<string, unknown>;
}

/** Wire shape of one transcript row of GET /runs/{id}/messages. */
export interface RunMessageResponse {
  id: string;
  run_id: string | null;
  role: 'user' | 'assistant' | 'system' | 'tool' | string;
  content: string | null;
  created_at: string;
}

export interface Run {
  id: string;
  config_id: string;
  card_id: string | null;
  parent_run_id: string | null;
  depth: number;
  status: RunStatus;
  started_at: string;
  ended_at: string | null;
  token_usage: Record<string, number>;
  /** Total run cost in integer micros, the backend's source of truth. */
  cost_micros: number;
  /** The same cost in major units, for display only. */
  cost_usd: number;
}

export interface RunCancelResponse {
  run_id: string;
  status: string;
  cancelled_at: string;
}

export type RunStepType =
  | 'thought'
  | 'tool_call'
  | 'tool_result'
  | 'message_delta'
  | 'approval_required'
  | 'run_completed'
  | 'run_failed'
  | 'compaction';

export interface RunStep {
  id: string;
  run_id: string;
  step_number: number;
  event_type: RunStepType;
  payload: Record<string, unknown>;
  created_at: string;
}

/** Response shape of GET /artifacts/run/{run_id} and GET /artifacts/{id}. */
export interface Artifact {
  id: string;
  run_id: string;
  kind: string;
  path: string;
  size_bytes: number;
  preview: string | null;
  created_at: string;
}

export interface HealthStatus {
  status: 'ok' | 'degraded' | 'unhealthy';
  version: string;
  database: 'connected' | 'disabled' | 'error';
  timestamp: string;
  details: Record<string, unknown> | null;
}

export interface AuthMe {
  device_id: string;
  device_name: string;
  user_id: string;
  user_display_name: string;
  created_at: string;
  last_seen_at: string;
}

export interface PairingResponse {
  device_token: string;
  device_id: string;
  device_name: string;
  user_id: string;
}

export interface UserSettings {
  user_id: string;
  spending_cap_usd_monthly: number;
  polling_interval_seconds: number;
  approval_defaults: Record<string, string>;
  preferred_model_tier: string;
}

export interface ConnectorAccount {
  id: string;
  user_id: string;
  connector_id: string;
  account_label: string;
  scopes: string[];
  status: 'active' | 'needs_reauth' | 'error' | 'disabled' | string;
  enabled_tools: string[];
}

export interface SyncTrigger {
  account_id: string;
  job_id: string;
  status: string;
}

export interface SystemLimits {
  max_steps_per_run: number;
  max_cost_usd_per_run: number;
  run_timeout_seconds: number;
  docker_memory_limit: string;
}

export type Theme = 'light' | 'dark';

/* -------------------------------------------------------------------------------------------
   Skills. Mirrors backend/src/modules/skills/data.py. Toggle rides on the dedicated
   `POST /skills/{id}/enable` and `POST /skills/{id}/disable` routes, which take no body,
   so there is no SkillToggleRequest type to mirror.
   ------------------------------------------------------------------------------------------- */

export interface SkillResponse {
  id: string;
  name: string;
  description: string;
  version: string;
  /** The backend emits 'git' and 'builtin' today; kept as a widening union for future sources. */
  source: 'git' | 'builtin' | string;
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

/* -------------------------------------------------------------------------------------------
   MCP servers. Mirrors the BaseModel declarations inside backend/src/modules/connectors/router.py.
   Registration is async: POST returns 202 with a discovery job id, never the server row.
   ------------------------------------------------------------------------------------------- */

export interface RegisterMCPServerRequest {
  name: string;
  transport: 'stdio' | 'sse';
  command?: string | null;
  url?: string | null;
}

export interface MCPServerResponse {
  id: string;
  name: string;
  transport: string;
  command: string | null;
  url: string | null;
  enabled: boolean;
  created_at: string;
}

export interface MCPDiscoveryResponse {
  server_name: string;
  job_id: string;
  status: string;
}

/* -------------------------------------------------------------------------------------------
   Settings mutations and the settings-page audit view.
   ------------------------------------------------------------------------------------------- */

export interface UserSettingsUpdateRequest {
  spending_cap_usd_monthly?: number;
  polling_interval_seconds?: number;
  approval_defaults?: Record<string, string>;
  preferred_model_tier?: string;
}

export interface AuditLogEntry {
  id: string;
  actor: string;
  action: string;
  subject: string;
  details: Record<string, unknown>;
  created_at: string;
}

export interface VaultSecretDTO {
  key_name: string;
  masked_value: string;
  created_at: string;
  updated_at: string;
}

export interface SetVaultSecretRequest {
  key: string;
  value: string;
}

/** Result of a provider check. A rejected key is a 200 report, not an error response. */
export interface VaultValidationResult {
  key_name: string;
  provider: string;
  valid: boolean;
  status_code: number | null;
  message: string;
}


/* -------------------------------------------------------------------------------------------
   Server-sent events. The transport is fetch plus a body reader, never EventSource, because
   EventSource cannot send the Authorization header the backend requires.
   ------------------------------------------------------------------------------------------- */

export interface SseEvent {
  type: string;
  data: string;
  id?: string;
}

/**
 * The `data` JSON payload of one run-event frame, from runs/stream.py: the SSE `id` is the
 * step number, `event` is the event_type, and `data` carries this shape.
 */
export interface RunStepEvent {
  run_id: string;
  step_number: number;
  event_type: RunStepType;
  payload: Record<string, unknown>;
  created_at: string;
}

export type StreamState = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'closed';

/* -------------------------------------------------------------------------------------------
   Dashboard widgets. Shapes mirror backend/src/modules/widgets/data.py caps.
   ------------------------------------------------------------------------------------------- */

export type WidgetType = 'metric_card' | 'data_table' | 'task_list' | 'status_feed';

export interface Widget {
  id: string;
  user_id: string;
  title: string;
  widget_type: string;
  data_payload: Record<string, unknown>;
  refresh_config: Record<string, unknown> | null;
  refresh_interval_minutes: number | null;
  next_refresh_at: string | null;
  version: number;
  updated_at: string;
}

/** The `data` JSON payload of one widget_updated frame from widgets/stream.py. */
export interface WidgetUpdatedEvent {
  id: string;
  version: number;
  data_payload: Record<string, unknown>;
  updated_at: string;
}

export interface ApiErrorEnvelope {
  code: string;
  message: string;
  request_id: string;
  details: unknown;
}
