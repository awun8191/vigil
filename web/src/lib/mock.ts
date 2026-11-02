import type {
  Approval,
  Card,
  CardListResponse,
  ChatMessage,
  ChatSession,
  ConnectorAccount,
  HealthStatus,
  PairingResponse,
  SyncTrigger,
  SystemLimits,
  UserSettings,
} from '@/types';
import type { VigilApi } from './contract';

/**
 * Fixture-backed implementation of the API surface, used when the client is built with
 * `VITE_VIGIL_MOCK=1`.
 *
 * This is a BUILD-TIME swap. Runtime detection is forbidden: a user who loses their token must
 * see an error, never fabricated cards that make the assistant look like it is working.
 */

const NOW = Date.now();
const iso = (offsetMinutes: number) => new Date(NOW + offsetMinutes * 60_000).toISOString();
const later = <T,>(value: T, ms = 260) =>
  new Promise<T>((resolve) => setTimeout(() => resolve(value), ms));

let cards: Card[] = [
  {
    id: 'c-1',
    user_id: 'u-1',
    item_id: 'i-1',
    thread_id: 'thread-budget',
    type: 'email',
    urgency: 'high',
    title: 'Reply drafted for Sarah regarding the Q4 budget',
    summary:
      'Sarah asked for the revised line items before end of day. A reply is drafted and waiting on your approval, it includes the two figures you confirmed yesterday.',
    due_at: iso(126),
    status: 'awaiting_approval',
    actions: [
      { id: 'review', label: 'Review and send', action_type: 'run', payload: {} },
      { id: 'dismiss', label: 'Dismiss', action_type: 'dismiss', payload: {} },
    ],
    feedback: null,
    created_at: iso(-180),
    updated_at: iso(-20),
  },
  {
    id: 'c-2',
    user_id: 'u-1',
    item_id: 'i-2',
    thread_id: null,
    type: 'calendar',
    urgency: 'medium',
    title: 'Design sync in 30 minutes',
    summary: 'Prep notes are ready in the project memory. Last week you asked to be nudged twice.',
    due_at: iso(30),
    status: 'new',
    actions: [
      { id: 'open', label: 'Open notes', action_type: 'reply', payload: { seed: 'Summarise my design sync prep notes' } },
      { id: 'dismiss', label: 'Dismiss', action_type: 'dismiss', payload: {} },
    ],
    feedback: null,
    created_at: iso(-90),
    updated_at: iso(-90),
  },
  {
    id: 'c-3',
    user_id: 'u-1',
    item_id: null,
    thread_id: null,
    type: 'alert',
    urgency: 'critical',
    title: 'Two connector tokens expired',
    summary: 'The Gmail and Calendar connectors both stopped syncing six hours ago and need reauthorisation.',
    due_at: null,
    status: 'in_progress',
    actions: [{ id: 'dismiss', label: 'Acknowledge', action_type: 'dismiss', payload: {} }],
    feedback: null,
    created_at: iso(-360),
    updated_at: iso(-360),
  },
  {
    id: 'c-4',
    user_id: 'u-1',
    item_id: 'i-4',
    thread_id: 'thread-invoice',
    type: 'email',
    urgency: 'low',
    title: 'Invoice from the hosting provider',
    summary: 'Monthly invoice received. Nothing to do, kept for the record.',
    due_at: iso(1440),
    status: 'seen',
    actions: [{ id: 'dismiss', label: 'Dismiss', action_type: 'dismiss', payload: {} }],
    feedback: null,
    created_at: iso(-720),
    updated_at: iso(-700),
  },
];

const sessions: ChatSession[] = [
  {
    session_id: 's-1',
    user_id: 'u-1',
    title: 'Q4 budget reply',
    created_at: iso(-120),
    updated_at: iso(-10),
    message_count: 4,
  },
];

const messages: Record<string, ChatMessage[]> = {
  's-1': [
    {
      id: 'm-1',
      session_id: 's-1',
      role: 'user',
      content: 'Can you pull the two figures I confirmed for the Q4 budget reply?',
      created_at: iso(-110),
    },
    {
      id: 'm-2',
      session_id: 's-1',
      role: 'assistant',
      content:
        'The two figures you confirmed yesterday were 42,800 for tooling and 118,000 for contractor spend. Both are in the draft reply to Sarah, which is waiting on your approval on Home.',
      created_at: iso(-108),
    },
    {
      id: 'm-3',
      session_id: 's-1',
      role: 'user',
      content: 'Is the contractor number the one that includes the retainer?',
      created_at: iso(-12),
    },
    {
      id: 'm-4',
      session_id: 's-1',
      role: 'assistant',
      content:
        'Yes. 118,000 includes the 24,000 retainer and 94,000 of hourly work. The draft reply states it that way.',
      created_at: iso(-10),
    },
  ],
};

const approvals: Approval[] = [
  {
    id: 'a-1',
    run_id: 'r-1',
    tool_call_id: 'tc-1',
    tool_name: 'gmail_send',
    arguments: {
      to: 'sarah@example.com',
      subject: 'Q4 budget, revised line items',
      body: 'Hi Sarah, revised figures attached. Tooling lands at 42,800 and contractor spend at 118,000 including the retainer.',
    },
    status: 'pending',
    decided_at: null,
    decision_note: null,
  },
];

const connectors: ConnectorAccount[] = [
  {
    id: 'ca-1',
    user_id: 'u-1',
    connector_id: 'gmail',
    account_label: 'work@example.com',
    scopes: ['gmail.readonly', 'gmail.send'],
    status: 'needs_reauth',
    enabled_tools: ['gmail_search', 'gmail_send'],
  },
  {
    id: 'ca-2',
    user_id: 'u-1',
    connector_id: 'calendar',
    account_label: 'work@example.com',
    scopes: ['calendar.readonly'],
    status: 'active',
    enabled_tools: ['calendar_list'],
  },
];

const health: HealthStatus = {
  status: 'ok',
  version: '0.1.0',
  database: 'connected',
  timestamp: new Date().toISOString(),
  details: null,
};

const userSettings: UserSettings = {
  user_id: 'u-1',
  spending_cap_usd_monthly: 25,
  polling_interval_seconds: 300,
  approval_defaults: { gmail_send: 'require_approval' },
  preferred_model_tier: 'balanced',
};

const limits: SystemLimits = {
  max_steps_per_run: 40,
  max_cost_usd_per_run: 0.5,
  run_timeout_seconds: 900,
  docker_memory_limit: '1024m',
};

const replyDraft =
  'Both connectors are quiet right now. The draft reply to Sarah is still the only thing actually waiting on you, everything else is informational.';

export const mockApi: VigilApi = {
  mode: 'mock',

  pair(): Promise<PairingResponse> {
    return later({
      device_token: 'vgt_mock_token',
      device_id: 'd-1',
      device_name: 'Browser (simulation)',
      user_id: 'u-1',
    });
  },

  me() {
    return later({
      device_id: 'd-1',
      device_name: 'Browser (simulation)',
      user_id: 'u-1',
      user_display_name: 'Muhammad',
      created_at: iso(-10_000),
      last_seen_at: iso(0),
    });
  },

  revokeDevice() {
    return later(undefined);
  },

  listCards(params): Promise<CardListResponse> {
    const limit = params?.limit ?? 50;
    const offset = params?.offset ?? 0;
    return later({
      items: cards.slice(offset, offset + limit),
      total: cards.length,
      limit,
      offset,
    });
  },

  dismissCard(cardId) {
    const target = cards.find((card) => card.id === cardId);
    if (!target) return Promise.reject(new Error('Card not found'));
    const updated: Card = { ...target, status: 'dismissed' };
    cards = cards.map((card) => (card.id === cardId ? updated : card));
    return later(updated);
  },

  cardFeedback(cardId, body) {
    const target = cards.find((card) => card.id === cardId);
    if (!target) return Promise.reject(new Error('Card not found'));
    const updated: Card = { ...target, feedback: body.feedback };
    cards = cards.map((card) => (card.id === cardId ? updated : card));
    return later(updated);
  },

  createRun(_payload: Record<string, unknown>, cardId: string) {
    return later({
      id: 'r-1',
      config_id: 'cfg-1',
      card_id: cardId,
      parent_run_id: null,
      depth: 0,
      status: 'queued' as const,
      started_at: new Date().toISOString(),
      ended_at: null,
      token_usage: {},
      cost_usd: 0,
    });
  },

  listApprovals() {
    return later(approvals.filter((approval) => approval.status === 'pending'));
  },

  getApproval(approvalId) {
    const found = approvals.find((approval) => approval.id === approvalId);
    return found ? later(found) : Promise.reject(new Error('Approval not found'));
  },

  decideApproval(approvalId, body) {
    const found = approvals.find((approval) => approval.id === approvalId);
    if (!found) return Promise.reject(new Error('Approval not found'));
    found.status = body.decision;
    found.decided_at = new Date().toISOString();
    cards = cards.map((card) =>
      card.status === 'awaiting_approval' ? { ...card, status: 'done' } : card,
    );
    return later({
      approval_id: approvalId,
      status: body.decision,
      run_id: found.run_id,
      resumed: true,
    });
  },

  listSessions() {
    return later([...sessions]);
  },

  createSession(title) {
    const session: ChatSession = {
      session_id: `s-${sessions.length + 1}`,
      user_id: 'u-1',
      title: title ?? null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      message_count: 0,
    };
    sessions.unshift(session);
    messages[session.session_id] = [];
    return later(session);
  },

  listMessages(sessionId) {
    return later(messages[sessionId] ?? []);
  },

  sendMessage(sessionId, content) {
    const list = messages[sessionId] ?? [];
    const message: ChatMessage = {
      id: `m-${Date.now()}`,
      session_id: sessionId,
      role: 'user',
      content,
      created_at: new Date().toISOString(),
    };
    list.push(message);
    messages[sessionId] = list;
    return later(message);
  },

  openStream(sessionId, prompt, handlers) {
    handlers.onStateChange?.('connecting');
    const openTimer = setTimeout(() => handlers.onStateChange?.('open'), 120);

    // Mirrors the backend: bare `data: <token>` frames, terminated by `data: [DONE]`.
    const words = replyDraft.split(' ');
    const chunkTimers: Array<ReturnType<typeof setTimeout>> = [];

    words.forEach((word, index) => {
      chunkTimers.push(
        setTimeout(
          () => handlers.onEvent({ type: 'message', data: index === 0 ? word : ` ${word}` }),
          380 + index * 55,
        ),
      );
    });

    chunkTimers.push(
      setTimeout(
        () => handlers.onEvent({ type: 'message', data: '[DONE]' }),
        380 + words.length * 55 + 80,
      ),
    );

    void sessionId;
    void prompt;

    return () => {
      clearTimeout(openTimer);
      for (const timer of chunkTimers) clearTimeout(timer);
      handlers.onStateChange?.('closed');
    };
  },

  health() {
    return later({ ...health, timestamp: new Date().toISOString() });
  },

  getSettings() {
    return later(userSettings);
  },

  getLimits() {
    return later(limits);
  },

  listConnectors() {
    return later([...connectors]);
  },

  syncConnector(accountId): Promise<SyncTrigger> {
    return later({ account_id: accountId, job_id: 'job-1', status: 'dispatched' });
  },
};
