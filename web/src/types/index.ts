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
}

export interface CardListResponse {
  items: Card[];
  total: number;
  limit: number;
  offset: number;
}

export interface CardFeedbackRequest {
  feedback: 'useful' | 'not_useful' | 'dismissed' | 'spam';
  note?: string | null;
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
}

/** A message rendered in the stream, including optimistic and streaming-local entries. */
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
  | 'stale_thread_paused'
  | 'completed'
  | 'failed';

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
  cost_usd: number;
}

export type RunStepType =
  | 'thought'
  | 'tool_call'
  | 'tool_result'
  | 'approval_required'
  | 'run_completed'
  | 'run_failed';

export interface RunStep {
  id: string;
  run_id: string;
  step_number: number;
  event_type: RunStepType;
  payload: Record<string, unknown>;
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
   Server-sent events. The transport is fetch plus a body reader, never EventSource, because
   EventSource cannot send the Authorization header the backend requires.
   ------------------------------------------------------------------------------------------- */

export interface SseEvent {
  type: string;
  data: string;
  id?: string;
}

export type StreamState = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'closed';

export interface ApiErrorEnvelope {
  code: string;
  message: string;
  request_id: string;
  details: unknown;
}
