import type {
  AgentConfigResponse,
  Approval,
  Artifact,
  Card,
  CardListResponse,
  ChatMessage,
  ChatSession,
  ConnectorAccount,
  HealthStatus,
  MCPDiscoveryResponse,
  MCPServerResponse,
  PairingResponse,
  Run,
  RunCancelResponse,
  RunCreateRequest,
  RunMessageResponse,
  RunStep,
  SkillResponse,
  SyncTrigger,
  SystemLimits,
  UserSettings,
  UserSettingsUpdateRequest,
  VaultSecretDTO,
  VaultValidationResult,
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
      // `run` actions reach POST /runs, which requires a config_id in the payload. The mock
      // carries one so the guarded button is exercisable in simulation.
      { id: 'review', label: 'Review and send', action_type: 'run', payload: { config_id: 'cfg-1', initial_prompt: 'Send the drafted reply to Sarah' } },
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
      content: 'Can you pull the two figures I confirmed for the Q4 budget reply and check the burn-rate projection?',
      created_at: iso(-110),
    },
    {
      id: 'm-2',
      session_id: 's-1',
      role: 'assistant',
      content: `The two figures you confirmed yesterday are **$42,800** for infrastructure tooling and **$118,000** for contractor spend. Both are staged in the draft reply to Sarah.

| Line Item | Confirmed Allocation | Share of Q4 Cap | Status |
| :--- | ---: | ---: | :--- |
| Cloud & Sandbox Tooling | $42,800 | 26.6% | Locked |
| Contractor Spend (incl. Retainer) | $118,000 | 73.4% | Pending Sarah's sign-off |
| **Total Committed** | **$160,800** | **100.0%** | **Ready** |

Using the continuous runway decay model:

$$R(t) = R_0 - \\int_0^t \\left( B_{\\text{fixed}} + \\gamma \\cdot C_{\\text{var}}(\\tau) \\right) d\\tau$$

With $\\gamma = 1.08$ for peak Q4 load, our monthly burn settles at $\\approx \\$53.6\\text{k}/\\text{mo}$, keeping a **14.2% safety margin** below the ceiling.`,
      created_at: iso(-108),
      run_id: 'r-seed-1',
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
        'Yes — `$118,000` combines the `$24,000` fixed retainer and `$94,000` of milestone-based engineering hours:\n\n```python\nretainer = 24_000\nmilestone_hours = 94_000\ntotal_contractor_budget = retainer + milestone_hours  # $118,000\n```\n\nThe generated reconciliation brief is attached above if you want to forward it directly.',
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

let settings = userSettings;

const skills: SkillResponse[] = [
  {
    id: 'skl-1',
    name: 'Expense reconciliation',
    description: 'Matches receipts against statements and drafts a monthly reconciliation summary.',
    version: '1.4.0',
    source: 'builtin',
    commit: null,
    enabled: true,
    storage_path: '/skills/builtin/expense-reconciliation',
    created_at: iso(-43_200),
    updated_at: iso(-10_080),
  },
  {
    id: 'skl-2',
    name: 'Linear triage',
    description: 'Reads issue streams and drafts assignments, priorities and labels.',
    version: '2.0.1',
    source: 'git',
    commit: '1f4b9c7d0a2e',
    enabled: false,
    storage_path: '/skills/git/linear-triage',
    created_at: iso(-10_080),
    updated_at: iso(-4_320),
  },
];

let mcpServers: MCPServerResponse[] = [
  {
    id: 'mcp-1',
    name: 'atlassian',
    transport: 'sse',
    command: null,
    url: 'https://mcp.atlassian.com/v1/sse',
    enabled: true,
    created_at: iso(-5_760),
  },
];

const replyDraft =
  'Both connectors are quiet right now. The draft reply to Sarah is still the only thing actually waiting on you, everything else is informational.';

/* Mock run state. createRun seeds one run and openRunStream replays a fixed step script
   against it, so the run surface is exercisable in simulation mode. */

interface MockRun {
  run: Run;
  cancelled: boolean;
  prompt: string | null;
  sessionId: string | null;
  createdAt: string;
}

let runs = new Map<string, MockRun>([
  [
    'r-seed-1',
    {
      run: {
        id: 'r-seed-1',
        config_id: 'cfg-1',
        card_id: null,
        parent_run_id: null,
        depth: 0,
        status: 'completed',
        started_at: iso(-110),
        ended_at: iso(-108),
        token_usage: { prompt: 1240, completion: 620 },
        cost_micros: 4200,
        cost_usd: 0.0042,
      },
      cancelled: false,
      prompt: 'Can you pull the two figures I confirmed for the Q4 budget reply and check the burn-rate projection?',
      sessionId: 's-1',
      createdAt: iso(-110),
    },
  ],
]);
let runCounter = 0;

const defaultAgentTools = [
  'read',
  'read_document',
  'write',
  'edit',
  'bash',
  'deliver_file',
  'generate_document',
  'ask_user',
  'manage_tasks',
  'skill_load',
  'web_search',
  'web_fetch',
];

const defaultAgentSkills = [
  'high-end-visual-design',
  'emil-design-eng',
  'design-taste-frontend',
  'literature-search-arxiv',
  'literature-search-openalex',
  'plus-ultra',
  'ultrawork',
  'marketing-psychology',
];

const mockArtifactsByRun: Record<string, Artifact[]> = {
  'r-seed-1': [
    {
      id: 'art-seed-md',
      run_id: 'r-seed-1',
      kind: 'document',
      path: '/var/vigil/deliveries/q4-budget-reconciliation.md',
      size_bytes: 1420,
      preview:
        '# Q4 Budget Reconciliation Brief\n\n## Executive Summary\n- **Tooling Allocation:** $42,800\n- **Contractor Allocation:** $118,000 (includes $24,000 retainer)\n- **Status:** Verified against Q4 ledger.',
      created_at: iso(-108),
    },
    {
      id: 'art-seed-xlsx',
      run_id: 'r-seed-1',
      kind: 'document',
      path: '/var/vigil/deliveries/q4-ledger-summary.xlsx',
      size_bytes: 4820,
      preview:
        '[sheet: Q4 Allocation]\nLine Item\tConfirmed Allocation\tShare of Cap\tStatus\nCloud & Sandbox Tooling\t$42,800\t26.6%\tLocked\nContractor Spend\t$118,000\t73.4%\tPending Sign-off\nTotal Committed\t$160,800\t100.0%\tReady',
      created_at: iso(-108),
    },
  ],
};

const mockRunRun = (runId: string, cardId: string | null): Run => ({
  id: runId,
  config_id: 'cfg-1',
  card_id: cardId,
  parent_run_id: null,
  depth: 0,
  status: 'completed',
  started_at: iso(0),
  ended_at: null,
  token_usage: {},
  cost_micros: 0,
  cost_usd: 0,
});

/** The mock's final assistant reply for a run, keyed off the prompt when there is one. */
function mockRunFinal(entry: MockRun): string {
  if (entry.prompt) {
    return `Here is what the run produced for "${entry.prompt.slice(0, 80)}": the reply draft still stands, both figures match what you confirmed, and the message is approved for sending.`;
  }
  return replyDraft;
}

const stepScript: Array<{ event_type: RunStep['event_type']; payload: Record<string, unknown> }> = [
  {
    event_type: 'thought',
    payload: {
      content:
        'Cross-referencing the Q4 budget ledger in /workspace/finance/q4_ledger.csv and verifying contractor retainer bounds before generating the reconciliation brief.',
      total_tokens: 384,
    },
  },
  {
    event_type: 'tool_call',
    payload: {
      tool_call_id: 'call_bash_1',
      tool_name: 'bash',
      arguments: {
        command: 'python3 -c "import csv; print(\' tooling: 42800\\n contractor: 118000\')"',
        timeout_seconds: 30,
      },
    },
  },
  {
    event_type: 'tool_result',
    payload: {
      tool_call_id: 'call_bash_1',
      tool_name: 'bash',
      result: {
        ok: true,
        content: 'exit_code: 0\n--- stdout ---\ntooling: 42800\ncontractor: 118000\n--- stderr ---\n',
      },
    },
  },
  {
    event_type: 'tool_call',
    payload: {
      tool_call_id: 'call_read_1',
      tool_name: 'read',
      arguments: { path: '/workspace/notes/sarah_thread.md' },
    },
  },
  {
    event_type: 'tool_result',
    payload: {
      tool_call_id: 'call_read_1',
      tool_name: 'read',
      result: {
        ok: true,
        content: '# Sarah Thread Summary\nConfirmed $42,800 tooling and $118,000 contractor spend ($24k retainer + $94k hourly).',
      },
    },
  },
  {
    event_type: 'tool_call',
    payload: {
      tool_call_id: 'call_doc_1',
      tool_name: 'generate_document',
      arguments: {
        filename: 'q4-budget-reconciliation.md',
        format: 'markdown',
        title: 'Q4 Budget Reconciliation Brief',
        content:
          '## Executive Summary\n- **Tooling Allocation:** $42,800\n- **Contractor Allocation:** $118,000 (includes $24,000 retainer)\n- **Status:** Verified against Q4 ledger.',
      },
    },
  },
  {
    event_type: 'tool_result',
    payload: {
      tool_call_id: 'call_doc_1',
      tool_name: 'generate_document',
      result: {
        ok: true,
        artifact_ref: '/var/vigil/deliveries/q4-budget-reconciliation.md',
        content: {
          path: '/var/vigil/deliveries/q4-budget-reconciliation.md',
          name: 'q4-budget-reconciliation.md',
          size_bytes: 1420,
          sha256: '9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08',
          format: 'markdown',
        },
      },
    },
  },
];

function mockStepRecord(runId: string, index: number): RunStep {
  const step = stepScript[index];
  if (!step) throw new Error(`No mock step ${index}`);
  const created = new Date(Date.parse(iso(0)) + index * 400).toISOString();
  return {
    id: `step-${runId}-${index}`,
    run_id: runId,
    step_number: index + 1,
    event_type: step.event_type,
    payload: step.payload,
    created_at: created,
  };
}

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

  createRun(body: RunCreateRequest) {
    runCounter += 1;
    const runId = `r-${runCounter}`;
    runs.set(runId, {
      run: { ...mockRunRun(runId, body.card_id ?? null), status: 'queued' },
      cancelled: false,
      prompt: body.initial_prompt ?? null,
      sessionId: body.session_id ?? null,
      createdAt: new Date().toISOString(),
    });
    return later({
      ...mockRunRun(runId, body.card_id ?? null),
      status: 'queued',
      started_at: new Date().toISOString(),
    });
  },

  getRun(runId): Promise<Run> {
    const entry = runs.get(runId);
    if (!entry) return Promise.reject(new Error('Run not found'));
    return later({ ...entry.run });
  },

  getLatestRun(sessionId): Promise<Run | null> {
    let latest: { run: Run; createdAt: string } | null = null;
    for (const entry of runs.values()) {
      if (entry.sessionId !== sessionId) continue;
      const createdAt = entry.createdAt;
      if (!latest || createdAt > latest.createdAt) latest = { run: entry.run, createdAt };
    }
    return latest ? later({ ...latest.run }) : Promise.resolve(null);
  },

  getRunMessages(runId): Promise<RunMessageResponse[]> {
    const entry = runs.get(runId);
    if (!entry) return Promise.reject(new Error('Run not found'));
    const body = entry.prompt ?? replyDraft;
    return later([
      {
        id: `msg-${runId}-u`,
        run_id: runId,
        role: 'user',
        content: body,
        created_at: entry.run.started_at,
      },
      {
        id: `msg-${runId}-a`,
        run_id: runId,
        role: 'assistant',
        content: mockRunFinal(entry),
        created_at: new Date().toISOString(),
      },
    ]);
  },

  listRunArtifacts(runId): Promise<Artifact[]> {
    return later([...(mockArtifactsByRun[runId] ?? [])]);
  },

  downloadArtifactBlob(artifactId): Promise<Blob> {
    for (const list of Object.values(mockArtifactsByRun)) {
      const found = list.find((a) => a.id === artifactId);
      if (found) {
        return later(new Blob([found.preview ?? ''], { type: 'text/plain;charset=utf-8' }));
      }
    }
    return later(new Blob([''], { type: 'application/octet-stream' }));
  },

  getArtifactText(artifactId): Promise<string> {
    for (const list of Object.values(mockArtifactsByRun)) {
      const found = list.find((a) => a.id === artifactId);
      if (found) {
        return later(found.preview ?? '');
      }
    }
    return later('');
  },

  getDefaultAgentConfig(): Promise<AgentConfigResponse> {
    return later({
      id: 'cfg-1',
      name: 'vigil-default',
      model: 'reasoning-model',
      tools: [...defaultAgentTools],
      skills: [...defaultAgentSkills],
      memory_scopes: ['global'],
      limits: {},
    });
  },

  getRunSteps(runId, sinceStep = 0) {
    const entry = runs.get(runId);
    if (!entry) return Promise.reject(new Error('Run not found'));
    const total = stepScript.length;
    const steps: RunStep[] = [];
    for (let i = sinceStep; i < total; i += 1) steps.push(mockStepRecord(runId, i));
    return later(steps);
  },

  cancelRun(runId): Promise<RunCancelResponse> {
    const entry = runs.get(runId);
    if (!entry) return Promise.reject(new Error('Run not found'));
    entry.cancelled = true;
    entry.run = { ...entry.run, status: 'failed', ended_at: new Date().toISOString() };
    return later({
      run_id: runId,
      status: entry.run.status,
      cancelled_at: entry.run.ended_at ?? new Date().toISOString(),
    });
  },

  openRunStream(runId, { sinceStep = 0, handlers }) {
    const entry = runs.get(runId);
    if (!entry) {
      handlers.onStateChange?.('closed');
      return () => undefined;
    }
    handlers.onStateChange?.('connecting');

    const total = stepScript.length;
    const timers: Array<ReturnType<typeof setTimeout>> = [];
    const status = entry.cancelled ? 'failed' : 'completed';
    const terminal = status === 'failed' ? 'run_failed' : 'run_completed';

    for (let index = Math.max(0, sinceStep); index < total; index += 1) {
      const step = mockStepRecord(runId, index);
      timers.push(
        setTimeout(
          () =>
            handlers.onEvent({
              type: step.event_type,
              id: String(step.step_number),
              data: JSON.stringify({
                run_id: runId,
                step_number: step.step_number,
                event_type: step.event_type,
                payload: step.payload,
                created_at: step.created_at,
              }),
            }),
          200 + index * 500,
        ),
      );
    }

    const terminalStep = mockStepRecord(runId, total - 1);
    terminalStep.event_type = terminal;
    terminalStep.step_number = total + 1;
    timers.push(
      setTimeout(() => {
        handlers.onEvent({
          type: terminal,
          id: String(total + 1),
          data: JSON.stringify({
            run_id: runId,
            step_number: total + 1,
            event_type: terminal,
            payload:
              terminal === 'run_completed'
                ? { content: mockRunFinal(entry) }
                : { reason: 'cancelled' },
            created_at: new Date().toISOString(),
          }),
        });
        handlers.onStateChange?.('closed');
      }, 200 + total * 500),
    );

    entry.run = {
      ...entry.run,
      status,
      ended_at: new Date(Date.now() + (200 + total * 500)).toISOString(),
      token_usage: { prompt: 1_412, completion: 903 },
      cost_micros: 9_417,
      cost_usd: 0.009_417,
    };

    return () => {
      for (const timer of timers) clearTimeout(timer);
      handlers.onStateChange?.('closed');
    };
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

  health() {
    return later({ ...health, timestamp: new Date().toISOString() });
  },

  getSettings() {
    return later({ ...settings });
  },

  updateSettings(body: UserSettingsUpdateRequest) {
    settings = { ...settings, ...body };
    return later({ ...settings });
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

  listSkills() {
    return later([...skills]);
  },

  installSkill(body) {
    if (!/^[0-9a-f]{7,40}$/i.test(body.commit)) {
      return Promise.reject(new Error('Commit must be a hexadecimal SHA'));
    }
    const skill: SkillResponse = {
      id: `skl-${skills.length + 1}`,
      name: body.repo_url.split('/').pop()?.replace(/\.git$/, '') ?? 'untitled skill',
      description: 'Installed from a Git repository, awaiting its first run.',
      version: 'unknown',
      source: 'git',
      commit: body.commit,
      enabled: false,
      storage_path: `/skills/git/${body.repo_url}`,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    skills.push(skill);
    return later(skill);
  },

  enableSkill(skillId) {
    const skill = skills.find((entry) => entry.id === skillId);
    if (!skill) return Promise.reject(new Error('Skill not found'));
    skill.enabled = true;
    skill.updated_at = new Date().toISOString();
    return later({ ...skill });
  },

  disableSkill(skillId) {
    const skill = skills.find((entry) => entry.id === skillId);
    if (!skill) return Promise.reject(new Error('Skill not found'));
    skill.enabled = false;
    skill.updated_at = new Date().toISOString();
    return later({ ...skill });
  },

  listMCPServers() {
    return later([...mcpServers]);
  },

  registerMCPServer(body): Promise<MCPDiscoveryResponse> {
    if (mcpServers.some((server) => server.name === body.name)) {
      return Promise.reject(new Error(`An MCP server named ${body.name} already exists`));
    }
    const server: MCPServerResponse = {
      id: `mcp-${mcpServers.length + 1}`,
      name: body.name,
      transport: body.transport,
      command: body.command ?? null,
      url: body.url ?? null,
      enabled: false,
      created_at: new Date().toISOString(),
    };
    mcpServers.push(server);
    const jobId = `job-${mcpServers.length}`;
    setTimeout(() => {
      // Discovery completes in the background and the next list refresh sees it enabled.
      server.enabled = true;
    }, 3_000);
    return later({ server_name: server.name, job_id: jobId, status: 'queued' }, 260);
  },

  unregisterMCPServer(name) {
    if (!mcpServers.some((server) => server.name === name)) {
      return Promise.reject(new Error(`MCP server ${name} not found`));
    }
    mcpServers = mcpServers.filter((server) => server.name !== name);
    return later(undefined);
  },

  listVaultSecrets() {
    return later([...mockVaultSecrets]);
  },

  setVaultSecret(body) {
    const masked = body.value.length > 8 ? `${body.value.slice(0, 3)}****${body.value.slice(-4)}` : '****';
    const now = new Date().toISOString();
    const existing = mockVaultSecrets.find((s) => s.key_name === body.key);
    if (existing) {
      existing.masked_value = masked;
      existing.updated_at = now;
    } else {
      mockVaultSecrets.push({
        key_name: body.key,
        masked_value: masked,
        created_at: now,
        updated_at: now,
      });
    }
    return later({ ok: true, key: body.key });
  },

  deleteVaultSecret(key) {
    mockVaultSecrets = mockVaultSecrets.filter((s) => s.key_name !== key);
    return later(undefined);
  },

  testVaultSecret(body) {
    return later(mockValidateSecret(body.key, body.value));
  },

  validateVaultSecret(key) {
    const stored = mockVaultSecrets.find((s) => s.key_name === key);
    if (!stored) {
      return Promise.reject(new Error(`Secret ${key} not found in vault`));
    }
    return later({
      key_name: key,
      provider: mockProvider(key),
      valid: true,
      status_code: 200,
      message: 'Simulated provider accepted the stored key.',
    });
  },
};

function mockProvider(key: string): string {
  return key.trim().toUpperCase() === 'GROQ_API_KEY' ? 'groq' : 'unknown';
}

function mockValidateSecret(key: string, value: string): VaultValidationResult {
  const keyName = key.trim();
  const provider = mockProvider(keyName);

  if (provider === 'groq') {
    const token = value.trim().replace(/^bearer\s+/i, '');
    const valid = token.startsWith('gsk_') && token.length > 8;
    return {
      key_name: keyName,
      provider,
      valid,
      status_code: valid ? 200 : 401,
      message: valid
        ? 'Groq accepted the key.'
        : 'Groq rejected the key (HTTP 401). Check that it is a Groq API key.',
    };
  }

  return {
    key_name: keyName,
    provider: 'unknown',
    valid: false,
    status_code: null,
    message: `No key validator is configured for ${JSON.stringify(keyName)} yet.`,
  };
}

let mockVaultSecrets: VaultSecretDTO[] = [
  {
    key_name: 'FIRECRAWL_API_KEY',
    masked_value: 'fc-****4321',
    created_at: new Date(Date.now() - 86400000).toISOString(),
    updated_at: new Date(Date.now() - 86400000).toISOString(),
  },
  {
    key_name: 'GROQ_API_KEY',
    masked_value: 'gsk****8899',
    created_at: new Date(Date.now() - 43200000).toISOString(),
    updated_at: new Date(Date.now() - 43200000).toISOString(),
  },
];

