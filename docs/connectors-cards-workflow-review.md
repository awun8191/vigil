# Connectors and Cards Workflow Review

Scope: how a connector item becomes a homepage card, how the agent creates cards,
how card actions run, and what is needed to properly land Gmail, Linear, and Google Drive.

## 1. Verified current state

### 1.1 The full intended pipeline and where it stops

```
OAuth connect -> register account (encrypted creds)
     -> poll job (delta sync) -> ingest item (dedupe by external_id)
     -> triage_item job (gate -> extract) -> card created with actions
     -> homepage card (60s poll) -> action -> run
     -> approval gate for external tools -> run completes -> card updated  [missing]
```

Verified stops in the pipeline:

| Stage | State | Evidence |
|---|---|---|
| OAuth register account | Service exists, AES-256-GCM encrypt works, audit row written. No OAuth authorization-code endpoint anywhere in the backend routers. | `src/modules/connectors/service.py:178`, `connectors/gmail/auth.py:81` |
| Poll connector | Job type declared, handler raises `JobHandlerUnavailableError`. | `src/worker.py:202-206` |
| Triage item | Handler raises `JobHandlerUnavailableError`. | `src/worker.py:183-187` |
| Triage service | Two-stage logic exists, but `jev_client` and `llm_client` are never injected by `get_triage_service`; the fallback fabricates fixed probabilities `{action_required: 0.88, is_bulk: 0.05, urgency: 0.85}` for everything. | `src/modules/triage/service.py:93-101` |
| Card creation | Only triage creates cards (`upsert_card_for_thread`), plus a `POST /cards` endpoint nobody calls. | `src/modules/triage/service.py:131`, `src/modules/cards/router.py:72` |
| Card execution of actions | Frontend `TaskCard` runs/dismisses/replies. No backend path ever sets a card's status to `awaiting_approval`, `in_progress`, or `done` after a run starts. | `web/src/components/home/TaskCard.tsx:122-125` |

### 1.2 Every service in this chain ignores the database

`CardsService`, `ConnectorsService` (accounts, sync states, items), and
`TriageService` all store state in class-level dicts despite receiving an
`AsyncSession`:

- `src/modules/cards/service.py:25` `_cards: ClassVar[dict[UUID, CardDTO]]`
- `src/modules/connectors/service.py:72-75` accounts/sync state/items dicts
- `src/modules/connectors/service.py:223-225` MCP server and tool dicts (global, not per user)
- `src/modules/triage/service.py:22-23` triage result dicts

Meanwhile `alembic/versions/0001_initial_schema.py` already creates
`connected_accounts`, `connector_sync_state`, `connector_items`,
`triage_decisions`, `cards`, and `card_actions`. Schema exists, services never
touch it. Consequences: cards and connector accounts vanish on restart, whole
user account lists mix across processes, and the MCP registry is global so one
user's registered third-party server is visible to all.

### 1.3 The Gmail connector is an island

- `connectors/gmail/` (auth, sync, MCP server with `gmail_search`, `gmail_read`,
  `gmail_create_draft`, `gmail_send`) is complete standalone code.
- Nothing in `backend/src` imports `connectors.gmail`. The host client
  (`src/integrations/mcp/mcp.py:426-509`) can only reach servers registered by
  name in `ConnectorsService._mcp_servers`; the Gmail classes are never
  registered, so `gmail_*` tools cannot appear in a run.
- OAuth: `GmailAuthManager.get_authorization_url` and
  `exchange_code_for_tokens` exist, but no router exposes `/connectors/gmail/authorize`
  or a redirect callback, so there is no way to actually obtain tokens.
- `gmail_send` is marked `approval_level: external` in the connector's tool
  definitions, which matches the architecture: an Approval row, then resume.
  The approval machinery (`approvals` table, `src/modules/runs/state.py`) is
  real and tested for built-in tools; it just needs the Gmail tools reachable.

### 1.4 Frontend card action payloads do not match what the backend creates

Contract from `TaskCard.tsx`:

| Action type | Payload keys the frontend reads |
|---|---|
| `run` | `config_id` (falls back to default config), `initial_prompt` |
| `reply` | `seed` (pre-fills chat input) |
| `dismiss` | none |

What triage creates (`src/modules/triage/service.py:127-130`): a `reply` action
whose payload is `{thread_id}` only, and a `dismiss` action. So:

1. A "Quick Reply" click navigates to chat with an empty seed. The thread id,
   the one thing that would make the reply contextual, is dropped.
2. Triage never emits a `run` action, so the primary "Run" path of `TaskCard`
   (`POST /runs` with `card_id`) has likely never been exercised against a real
   card.
3. No producer sets `status="awaiting_approval"` on a card, so the "Waiting on
   you" primary-button state (`TaskCard.tsx:122`) is dead code today.

### 1.5 The agent cannot create cards

The tool registry (`src/modules/runs/tools/__init__.py:174-183`) holds eleven
built-ins: files, shell, delivery, `ask_user`, `manage_tasks`, skills,
`web_search`, `web_fetch`. There is no card tool, so a run can never surface
output as a homepage card. The only card producer is triage, which is itself
unwired. This is the missing metric the homepage is built around.

## 2. Exa web search: verified working

`src/modules/runs/tools/search.py` calls `https://mcp.exa.ai/mcp` with a bare
`tools/call` (no `initialize`). Live test against the free endpoint returned a
valid SSE `data:` line with search results and parsed fine. Free endpoint needs
no key; `EXA_API_KEY` only raises rate limits. Two notes:

- Tool names registered are `web_search` and `web_fetch`, both `level="local"`
  (auto-approved), which is the right default for read-only search.
- On error with an invalid key the code retries anonymously. That retry can
  mask a half-configured key; acceptable for now, worth a log line.

## 3. Target workflow designs

### 3.1 Connector ingestion (the resume path for Gmail)

1. **Auth**: add `POST /api/v1/connectors/{connector_id}/connect` returning the
   Google authorization URL with a signed `state` (user id + nonce), plus
   `GET .../callback` exchanging code for tokens. Store via `register_account`
   (already encrypts with AES-256-GCM and writes the audit row). Drop
   `gmail.modify` for v1; `readonly + compose` is enough for search, draft, and
   reply.
2. **Poll**: implement `handle_poll_connector` in the worker: load the account,
   decrypt creds, refresh token if expired (`GmailAuthManager.refresh_access_token`),
   then drive `GmailSyncEngine` (history delta via `historyId`; `history.list`
   every N minutes for v1; Pub/Sub push is a later optimization) and call
   `save_items`, then enqueue one `triage_item` job per new item.
3. **Triage**: implement `handle_triage_item`: wire `jev_client` and
   `llm_client` in `get_triage_service` (both clients exist under
   `src/integrations/jev` and `src/integrations/llm`), pass full item data
   (subject, body, sender, thread_id), then `upsert_card_for_thread`.
4. **Persistence**: back all four services with the existing tables instead of
   class dicts. This is required before any of the above is meaningful in
   production and is small, mechanical work since the schema already exists.

### 3.2 Cards created by the agent (the new surface)

Add a built-in tool `raise_card` to the registry:

- Args: `type` (reuse the existing type vocabulary: inquiry, billing, deadline,
  subscription, meeting, security, general), `urgency`, `title`, `summary`,
  `due_at` optional, `thread_id` optional (enables dedupe/update of the card
  for the same external thread), and up to three actions.
- Action whitelist, mirroring what the frontend can actually do:
  - `dismiss`
  - `reply` with payload `{seed: string}`
  - `run` with payload `{initial_prompt: string}`; `config_id` stays a backend
    decision, injected server-side at card creation time, never trusted from
    the model. This closes the loop with `TaskCard.tsx:62-66`, which already
    takes `config_id` from the payload or the pinned default config.
- Level: `local` (the run already passed user gating; the card is just surfacing).
- Enforce the existing `CardActionDTO` shape; anything else fails validation.
- Fix triage's action payloads to the same contract: `reply` gets
  `{seed: "Reply to ... <subject>", thread_id}` so the frontend finally shows
  a seeded reply box.

### 3.3 Run-to-card lifecycle (closing the loop)

- When `POST /runs` carries `card_id`, set that card to `in_progress`; on
  approval-required tool calls set `awaiting_approval`, which flips
  `TaskCard` into its "Waiting on you" state with the approve button as
  primary.
- On run completion set `done` or `failed`; `TaskCard` already renders
  "Ran to completion." / "That run failed." from the subscription, but the card
  list itself should reflect the terminal state so a card is not re-runnable
  forever.
- The run's final answer should be able to update the card summary
  (via the same tool harness) so the homepage card becomes the report.

## 4. Linear connector

Research result: use the first-party hosted MCP server.

- Endpoint: `https://mcp.linear.app/mcp` (Streamable HTTP). The SSE endpoint
  `https://mcp.linear.app/sse` is deprecated and scheduled for removal; do not
  build against it.
- Auth: OAuth 2.1 with dynamic client registration, or a bearer token / Linear
  API key. Read-only variant exists at `/mcp/readonly`, and requesting only the
  `read` OAuth scope yields read-only tokens on the main endpoint.
- Tools (verified against current docs): issues
  (`list_issues`, `get_issue`, `save_issue` create-or-update, statuses,
  labels), comments (`list_comments`, `save_comment`, `delete_comment`),
  projects, milestones, initiatives, documents, cycles, teams, users,
  attachments (prepare upload + direct PUT), diffs, status updates.

Vigil backend gaps to close before Linear works:

1. Add a Streamable HTTP transport to `src/integrations/mcp/mcp.py`. The
   existing `_discover_sse`/`_call_sse_tool` POST probing is close but has no
   support for per-request auth headers, which Linear requires.
2. Add an auth header / token field to `MCPServerConfig` and to registered
   servers (`headers: dict`), with tokens stored encrypted like connector
   credentials.
3. The global MCP registry must become per user (see 1.2). For Linear this
   matters because the OAuth token is per user.
4. Tool names from Linear (`list_issues`, `create_issue`-style names like
   `create_comment`) do not collide with Vigil built-ins today
   (`_check_tool_collisions` will catch future drift).

Card feed for Linear: for proactive cards, prefer scheduled
`list_issues` deltas (assigned-new, status changes) over webhooks in v1,
matching the Gmail poll pattern and the existing job queue.

## 5. Google Drive connector

Options evaluated:

| Option | Verdict |
|---|---|
| Google's hosted `drivemcp.googleapis.com` MCP | Not viable for personal `@gmail.com` setups in practice: data-plane calls are denied even with the API enabled and scopes granted; restricted preview. |
| Community stdio MCP servers (e.g. gdrive-mcp-server forks, `google-workspace-mcp` covering Docs/Sheets/Drive/Gmail/Calendar) | Viable and consistent with the existing third-party stdio architecture. Auth is `GOOGLE_CLIENT_ID/SECRET/REFRESH_TOKEN`, auto-refresh required (the original Anthropic server had a token-refresh bug; use a maintained fork or implement refresh ourselves). |
| Direct Drive API v3 inside Vigil (like the Gmail connector) | Same effort class as Gmail; gives per-user accounts and encrypted tokens natively through `ConnectorAccountDTO`. |

Scoped recommendation for v1: read-only Drive.

- Scopes: `drive.metadata.readonly` + `drive.readonly` (add `drive.file` only
  when saving artifacts back to Drive).
- Tool surface, mapped to the same per-user host pattern as Gmail:
  - `drive_search` (full-text + Drive v3 query syntax)
  - `drive_read` (auto-converts Docs to Markdown, Sheets to CSV, Slides to text;
    binaries rejected with a note rather than base64 blobs to protect context)
  - `drive_list_folder`
- Approval levels: `read` for all three; any write tool later is `external`.
- Token gotcha that applies to Gmail too: with the OAuth consent screen in
  Testing mode, refresh tokens expire after 7 days (`invalid_grant`). Publish
  the app or expect forced reconnects weekly. When a refresh fails, the right
  behavior is: set the account to `status="expired"` and let triage (or a
  dedicated health check) raise a "reconnect" card, matching the homepage
  widgets rail which already renders `expired` as an error tone
  (`WidgetsRail.tsx:35`).

## 6. Gmail correctness notes for the implementation pass

- `gmail_send` with `thread_id`: the current code passes `threadId` into the
  API body, which Gmail accepts, but proper threading also wants
  `In-Reply-To`/`References` headers built from the parent message; the sync
  engine has the `Message-ID` available when a reply targets a read message.
- `_search` fans out to `fetch_message_detail` per result (N+1 requests). Cap
  it to page one and return snippets from `messages/list` in v1; full detail
  only on `gmail_read`.
- Rate limits: Gmail per-user quotas are generous for personal use, but `watch`
  (Pub/Sub push) is capped at one event/second per user; polling `history.list`
  on an interval sidesteps that entirely and is the right v1 choice.
- Scope choice: requesting `gmail.modify` triggers fuller Google security
  review for production publishing. Start with `readonly + compose`.

## 7. Priority fix list

| # | Priority | Item |
|---|---|---|
| 1 | P0 | Back `CardsService`, connectors, and `TriageService` with the existing tables; remove class-dict state. |
| 2 | P0 | Implement `handle_poll_connector` and `handle_triage_item` worker handlers; inject Jev + LLM clients into triage. |
| 3 | P0 | Gmail OAuth connect/callback endpoints; register the Gmail MCP host per account so `gmail_*` tools appear in runs; `gmail_send` rides the existing approval gate. |
| 4 | P0 | Fix card action payload contract: `seed` on reply, emit `run` actions with `initial_prompt`, align truncated summary lengths. |
| 5 | P1 | Add `raise_card` agent tool + run-to-card status lifecycle (`in_progress`, `awaiting_approval`, `done`, `failed`). |
| 6 | P1 | Streamable HTTP transport + auth headers in the MCP client; make the MCP registry per user. |
| 7 | P1 | Linear connector registration (bearer token or API key path first, OAuth 2.1 second); scheduled `list_issues` deltas to cards. |
| 8 | P1 | Drive read-only connector (direct Drive v3, same connector-framework pattern as Gmail). |
| 9 | P2 | Token-expiry handling: account `expired` status + reconnect cards; publish OAuth app to escape the 7-day testing-mode expiry. |
| 10 | P2 | Widgets rail per-connector stats (unread count, last sync time) once items persist. |
