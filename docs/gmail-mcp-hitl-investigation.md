# Investigation & Implementation Blueprint: Gmail Connector, Proactive Triage, Multi-Question HITL UI, Rich Emails & Free MCP Tool Execution

> [!NOTE]
> **Implementation Status: Completed & Verified (November 16, 2026)**
> All five pillars below have been implemented and verified:
> - **Frontend**: `cd web && npm run build` (`tsc --noEmit && vite build`) passed (exit code `0`, 2,866 modules).
> - **Backend**: `pytest` passed across `test_gmail_hitl_mcp.py`, `test_gmail_connector.py`, `test_tools.py`, `test_job_dispatch.py`, `test_loop_state.py`, `test_mcp_factory.py`, `test_mcp_registration.py`, `test_cards_service.py`, `test_triage_pipeline.py` (**138 passed**), plus `test_connectors_state.py`, `test_connectors_persistence.py`, and `test_approvals.py` (**18 passed**).
>
> **Implemented Key Files**:
> - **Gmail OAuth, Sync & Rich MIME**: `backend/src/config/settings.py`, `backend/connectors/gmail/auth.py`, `backend/connectors/gmail/sync.py`, `backend/connectors/gmail/server.py`, `backend/src/modules/connectors/router.py`, `backend/src/modules/connectors/state.py`, `backend/src/modules/connectors/service.py`
> - **Proactive Polling, Triage & Cards**: `backend/src/worker.py`, `backend/src/modules/cards/service.py`, `backend/src/modules/triage/service.py`, `backend/src/modules/triage/dependencies.py`, `backend/src/modules/runs/tools/cards.py` (`raise_card`), `backend/src/modules/runs/tools/__init__.py` (`TOOL_COUNT = 25`)
> - **Multi-Question `ask_user` & Argument-Mutating Approvals**: `backend/src/modules/runs/tools/human.py`, `backend/src/modules/approvals/data.py`, `backend/src/modules/approvals/router.py`, `backend/src/modules/approvals/service.py`, `backend/src/modules/runs/loop.py`, `backend/src/modules/runs/core_context.py`
> - **Free MCP Hydration & Run-to-Card Lifecycle**: `backend/src/modules/runs/service.py`, `backend/src/dependencies.py`
> - **Frontend UI**: `web/src/components/chat/AskUserQuestionCard.tsx`, `web/src/components/hark/HarkEmailCard.tsx`, `web/src/components/chat/InlineApprovalCard.tsx`, `web/src/components/chat/ThinkingCapsule.tsx`, `web/src/components/hark/HarkUnifiedApp.tsx`, `web/src/components/settings/sections.tsx`, `web/src/types/index.ts`, `web/src/lib/contract.ts`, `web/src/lib/api.ts`, `web/src/lib/mock.ts`

> [!IMPORTANT]
> **Executive Summary of Root Causes Found in the Codebase**
> 1. **Gmail is an isolated island**: `backend/connectors/gmail/` has standalone OAuth, sync, and MCP tool code, but is never imported by `backend/src/`. There are no Google OAuth authorize/callback routes, key derivation in `GmailAuthManager` conflicts with `security.py`, and `poll_connector` / `triage_item` raise `JobHandlerUnavailableError` in `worker.py`.
> 2. **Services drop data across processes**: `ConnectorsService` (`_sync_states`, `_items`), `TriageService`, and `CardsService` store records in in-memory `ClassVar` dicts instead of the existing PostgreSQL tables (`connector_sync_state`, `connector_items`, `triage_decisions`, `cards`, `card_actions`).
> 3. **Approval & `ask_user` flow is structurally broken**:
>    - `ask_user` (`backend/src/modules/runs/tools/human.py`) only supports a single question, is marked `level="external"`, and renders inside `InlineApprovalCard.tsx` as a raw JSON dump with only **`[Reject]`** and **`[Approve]`** buttons.
>    - Worse, when a user approves an external tool in `RunLoop._dispatch_calls` (`loop.py:750-756`), `approval.decision_note` is **completely ignored** and `_run_external` re-runs `ask_user`, returning `{"status": "pending_user_input"}` instead of the user's answer!
> 4. **Rich Email Card is unwired & plain-text only**: `HarkEmailCard.tsx` is never imported anywhere in `web/src/`, is read-only plain text, and `GmailMCPServer` only constructs `MIMEText` (`text/plain`) without `multipart/alternative` HTML or RFC 2822 reply headers (`In-Reply-To`, `References`).
> 5. **MCP tools cannot be freely used by the agent**: `build_runs_service` (`dependencies.py:978-1030`) never passes `mcp_tools` into `RunsService`; `RunsService.process` (`service.py:664-668`) filters tool schemas by static `config.tools`; and `discover_tools` + `resolve_partition_policy` force all MCP tools to `level="external"`, blocking read/search/draft tools behind manual approvals.

---

## 1. Open-Source GitHub Projects to Reuse & Adapt

| Capability | GitHub Reference Project | What to Reuse / Adapt in Vigil |
| :--- | :--- | :--- |
| **Proactive Email Triage, Auto-Drafting & HITL Inbox** | `langchain-ai/executive-ai-assistant` (EAIA) & `langchain-ai/agent-inbox` | • **Triage classification (`ignore` / `notify` / `respond`)** with structured action extraction.<br>• **Pre-drafting replies** during triage for high-confidence threads.<br>• **HITL Action Card states**: `Accept & Send`, `Edit & Send` (mutating tool args before execution), `Steer / Respond to Agent` (feeding instructions back into the run), and `Ignore`. |
| **Multi-Question Multiple-Choice Agent UI** | **BoringUI `AskUserQuestions`** (`boringui.dev/r/ask-user-questions.json`) & `slopus/happy` (Claude Code `AskUserQuestion` renderer) | • Schema supporting `questions: Array<{ id, header, question, options: Array<{ label, description }>, multi_select, allow_other }>`.<br>• Stepped or stacked question cards with radio/checkbox pills, `(Recommended)` badge, and inline `"Other (specify)..."` free-text input. |
| **Rich HTML + Multipart Gmail MCP** | `GongRzhe/Gmail-MCP-Server` & `theposch/gmail-mcp` | • `MIMEMultipart("alternative")` pairing `text/plain` and `text/html` (compiled from Markdown/HTML).<br>• Recursive MIME part walker for `gmail_read` and `gmail_read_thread`.<br>• Proper RFC 2822 threading headers (`In-Reply-To`, `References`, `threadId`). |
| **Inline Rich Email Editor** | `tiptap` / Lightweight Markdown-HTML WYSIWYG pattern | • Formatting toolbar (Bold, Italic, Lists, Links, Blockquote, Code) + `[Rich Preview | Markdown]` toggle inside `HarkEmailCard.tsx`. |

---

## 2. Pillar-by-Pillar Implementation Design

### Pillar A: Gmail Connector, OAuth2 Flow & Rich MIME Support

1. **Unify Encryption & Add Server-Side Google OAuth Routes**:
   - **Settings (`backend/src/config/settings.py`)**: Add `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI` (defaulting to `http://localhost:8000/api/v1/connectors/google/callback` in dev).
   - **Fix Key Derivation (`backend/connectors/gmail/auth.py`)**: Replace custom AES key padding in `GmailAuthManager` with `encrypt_secret` and `decrypt_secret` from `backend/src/config/security.py`.
   - **OAuth Endpoints (`backend/src/modules/connectors/router.py`)**:
     - `GET /api/v1/connectors/google/authorize?connector_id=gmail`: Returns `{ "authorization_url": ... }` with a signed HMAC state containing `{user_id, connector_id, nonce, exp}` and scopes `gmail.readonly` + `gmail.compose`.
     - `GET /api/v1/connectors/google/callback?code=...&state=...`: Verifies `state`, exchanges `code` for tokens via `GmailAuthManager.exchange_code_for_tokens`, fetches the user's Gmail address from `/gmail/v1/users/me/profile` to set `account_label = email`, saves the encrypted credentials in `connected_accounts`, enqueues an immediate `poll_connector` job, and redirects back to `/agent/settings/connectors?connected=gmail`.
     - Add a **"Connect Gmail"** 1-click button in `web/src/components/settings/sections.tsx`.

2. **Upgrade `GmailMCPServer` & `GmailSyncEngine` for Rich HTML, Threads & Drafts**:
   - **Recursive MIME Parser (`backend/connectors/gmail/sync.py`)**:
     - Walk nested `payload.parts` recursively to extract both `text/plain` and `text/html`. If only `text/html` is present, convert HTML to clean Markdown text for `body_text` while keeping `body_html` for rich UI display.
     - Extract `Message-ID`, `In-Reply-To`, `References`, and `Cc` headers so replies thread properly.
   - **Rich Multipart Email Construction (`backend/connectors/gmail/server.py`)**:
     - Support both Markdown and HTML in `body` / `body_html`. Convert Markdown to clean inline-styled HTML and build a `MIMEMultipart("alternative")` message with both `text/plain` and `text/html` parts.
     - Populate `In-Reply-To` and `References` headers when `thread_id` or `reply_to_message_id` is provided.
   - **Complete Gmail Tool Surface & Approval Levels**:
     - `gmail_search(query, max_results)` -> `approval_level: "read"` (auto-executed)
     - `gmail_read(message_id)` -> `approval_level: "read"` (auto-executed)
     - `gmail_read_thread(thread_id)` -> `approval_level: "read"` (auto-executed; fetches full conversation context)
     - `gmail_create_draft(to, cc, bcc, subject, body, body_html, thread_id)` -> `approval_level: "local"` (auto-executed)
     - `gmail_update_draft(draft_id, to, cc, bcc, subject, body, body_html)` -> `approval_level: "local"` (auto-executed)
     - `gmail_send(to, cc, bcc, subject, body, body_html, thread_id, draft_id)` -> `approval_level: "external"` (pauses for user approval via the rich editable `HarkEmailCard`).

---

### Pillar B: Proactive Email Checking, Hark Pro Task Recommendations & CTAs

1. **Back `ConnectorsService`, `TriageService`, and `CardsService` with PostgreSQL**:
   - Replace the in-memory `ClassVar` dicts in `connectors/service.py`, `triage/service.py`, and `cards/service.py` with SQL queries over `ConnectorSyncStateModel`, `ConnectorItemModel`, `TriageDecisionModel`, `CardModel`, and `CardActionModel`.
   - Fix `ConnectorsService.trigger_sync` to enqueue a real `poll_connector` job via `JobsService`.

2. **Wire Background Polling & Triage in `backend/src/worker.py`**:
   - Add `CONNECTOR_SWEEP_INTERVAL_SECONDS = 120` in `run_worker` to periodically enqueue `poll_connector` jobs for active connected accounts.
   - Implement `handle_poll_connector` and `handle_triage_item` with Stage 1 (Jev gate) and Stage 2 (Fast LLM Hark Pro card synthesis + pre-drafted reply + `run`/`reply`/`dismiss` actions).

3. **Add `raise_card` Tool & Run-to-Card Status Lifecycle**:
   - Add `raise_card` (`level="local"`) in `backend/src/modules/runs/tools/cards.py` so the agent can create/update Hark Pro cards during any run.
   - Update linked card status across `in_progress` -> `awaiting_approval` -> `done` / `failed`.

4. **Wire Interactive CTAs & Inline Verb Pills in the Frontend**:
   - In `HarkUnifiedApp.tsx`: Clicking `[ Draft ]` / `[ Reply ]` (`onVerbClick`) immediately starts the agent run or opens the prepared draft; clicking the pill body (`onPillClick`) pre-fills `seed`.
   - In `MarkdownContent.tsx`: Render `[Verb] Summary...` lines as interactive `<HarkProactivityPill>` components.

---

### Pillar C: Fixing the Broken Approval Process & Multi-Question Brainstorming UI

1. **Backend Fix for Multi-Question `ask_user` & Argument-Mutating Approvals**:
   - Upgrade `AskUserArgs` (`human.py`) to support `questions: list[QuestionItem]` (multiple questions, option descriptions, `multi_select`, `allow_other`) while keeping backward compatibility with single `question`.
   - Upgrade `ApprovalDecisionRequest` (`approvals/data.py`) to accept `updated_arguments: dict[str, Any] | None` and `answers: dict[str, Any] | None`.
   - Fix `RunLoop._dispatch_calls` (`loop.py:750-767`):
     - When `approval.status == "approved"` and `call.name == "ask_user"`, return the user's structured answers from `approval.decision_note` instead of re-running `ask_user` (`pending_user_input`).
     - When `approval.status == "approved"` for external tools (`gmail_send`, etc.), apply any user-edited `updated_arguments` before calling `_run_external`.

2. **Frontend `AskUserQuestionCard.tsx` Component**:
   - Render single or multiple questions with radio/checkbox option cards, `(Recommended)` badges, and an inline `"Other (specify)..."` text input, submitting structured answers via `useDecideApproval`.

---

### Pillar D: Rich Email Formatting & Inline Editable Email Card (`HarkEmailCard.tsx`)

1. **Upgrade `web/src/components/hark/HarkEmailCard.tsx`**:
   - Editable `To`, `Cc`, `Bcc`, and `Subject` fields.
   - Rich formatting toolbar (Bold, Italic, Lists, Quote, Link, Code, and `[Rich Preview | Markdown]` toggle) + live editable body.
   - Action bar: **`[ Send Email ]`** (submits `decision: 'approved'` with `updated_arguments`), **`[ Save as Draft ]`**, and **`[ Request Changes ]`** (submits `decision: 'rejected'` with steering `decision_note` so the agent revises the draft).
   - Mount `HarkEmailCard` inside `InlineApprovalCard.tsx` (for `gmail_send`) and `MessageItem.tsx` / `ThinkingCapsule.tsx` (for `gmail_create_draft` / `gmail_read`).

---

### Pillar E: Free Agent Access to MCP Tools Across All Connectors

1. **Dynamic Per-User MCP & Connector Tool Hydration in `dependencies.py:build_runs_service`**:
   - Hydrate connected account tools (`GmailMCPServer` per active Gmail account) and registered MCP servers (`MCPServerModel` + `KeyVaultService` secrets via `MCPTransportFactory`) into `RunsService` on every run.
2. **Fix Schema Exposure & Partition Policy in `RunsService`**:
   - Include all registered `self._mcp_tools` in `resolve_tool_schemas()`.
   - Map MCP tools with `level in ("read", "local")` to partition `"local"` so the agent can freely search, read, list, and create drafts across Gmail and all other MCP connectors without approval roadblocks.
