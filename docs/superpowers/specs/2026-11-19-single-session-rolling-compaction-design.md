# Single Ambient Session, Rolling Compaction, and Scoped Projects Design

**Date:** 2026-11-19
**Status:** Draft for Implementation
**Scope:** Single Long-Lived Session Model, Session-Level Rolling Compaction (No JEV), Tightened Memory Guidance & Continuous Embedding Overlap, Scoped Project Workspaces, and Hark Pro UI Alignment.

---

## 1. Problem & Architectural Gaps

Vigil is intended to behave like **Hark Pro**, **Meta Muse**, and **Grokbot**: one continuous, ambient conversation per user rather than a traditional list of disposable chat threads. Several mismatches in the current codebase prevent that behavior:

1. **Projects are faked as multiple chat sessions (`web/src/components/hark/HarkUnifiedApp.tsx`)**:
   - Clicking `+ New Project` calls `createSession` (`title: "New Project"`), and the Projects sidebar lists `sessions.data` (`ThreadModel` rows).
   - Users should not create or switch between chat sessions in Chat mode; Chat should be one persistent conversation, while Projects should be real scoped workspaces.
2. **History is hard-sliced without rolling compaction (`backend/src/modules/runs/state.py`, `loop.py`)**:
   - `list_session_history` hard-slices the last `40` user/assistant rows (`rows[-40:]`) with no rolling summary of earlier turns, silently dropping older context.
   - `RunLoop._compact_check` measures tokens across `[*prior_history, *messages]`, but passes only the current `run_id`'s `messages` to `CompactionService.compact()`. Across a long-lived session where each turn creates a new `run_id`, `prior_history` is never compacted.
3. **JEV + Tier 0 locking blocks session-level compaction (`backend/src/modules/compaction/`)**:
   - `tiers.py` and `summarizer.py` lock every `UnitKind.USER` message (`_LOCKED_KINDS = {USER, APPROVAL, DECISION}`) and raise `SummaryInputError` if a user turn reaches Tier 2 summarization.
   - Tier 1 (`score_units`) invokes `JevClientProtocol` per batch, adding unnecessary latency and complexity when simple interaction-window rolling compaction suffices.
4. **Single-run memory indexing loses window overlap (`backend/src/modules/memory/service.py`)**:
   - `enqueue_index_for_completed_run` queues `index_memory` after each completed run (`2` unindexed rows: `1` user + `1` assistant), causing `Chunker.segment` to emit isolated 2-turn chunks with no cross-turn overlap.

---

## 2. Design Pillar 1: Single Long-Lived Ambient Session & Scoped Projects

### 2.1 Single Primary Ambient Session (`threads` & `chat`)

Each user has **one auto-provisioned primary ambient session** (`kind = "ambient"`):

- **Schema additions on `ThreadModel` (`backend/src/schemas/runtime.py`)** via Alembic migration:
  - `kind: Mapped[str]` (`String(32)`, default `"ambient"`, indexed).
  - `rolling_summary: Mapped[str | None]` (`Text`, nullable=True) — latest bounded session-level compaction summary.
  - `compacted_through_message_id: Mapped[UUID | None]` (`UUID(as_uuid=True)`, nullable=True) — ID of the latest message already absorbed into `rolling_summary`.
  - `compacted_through_at: Mapped[datetime | None]` (`DateTime(timezone=True)`, nullable=True) — deterministic timestamp cursor paired with `compacted_through_message_id` for `(created_at, id)` ordering.
- **Repository & API (`backend/src/modules/chat/`)**:
  - Add `get_or_create_primary_session(user_id: UUID) -> ChatSessionDTO` to `ChatRepository`, `SqlChatRepository`, `InMemoryChatRepository`, and `ChatService`:
    - Looks up the user's earliest/primary `kind == "ambient"` thread; if none exists, creates one with `title="Primary Stream"` and `kind="ambient"`.
    - Enforces at most one `ambient` thread per user (partial unique index on `(user_id) WHERE kind = 'ambient'` in PostgreSQL).
  - Expose `GET /api/v1/chat/primary` returning `ChatSessionResponse`.
  - Add cursor pagination (`before_id: UUID | None`, `limit: int = 50`) on `GET /api/v1/chat/sessions/{session_id}/messages` (defaulting to the latest `limit` messages in chronological order) so a multi-month conversation renders fast and loads older turns on scrollback.
  - Keep existing `/chat/sessions` endpoints backward-compatible for internal/test callers during migration, while Chat UI routes solely through `/chat/primary`.

### 2.2 Projects as Scoped Workspaces (`projects`)

Projects are separated from `ThreadModel` into real workspace entities:

- **Schema (`ProjectModel`, table `projects`)**:
  - `id: UUID` (PK)
  - `user_id: UUID` (FK `users.id`, indexed)
  - `name: str` (`String(120)`)
  - `slug: str` (`String(120)`, unique per user)
  - `description: str | None` (`Text`)
  - `workspace_path: str` (`String(512)` — isolated directory under `<WORKSPACE_BASE_PATH>/projects/<user_id>/<slug>`)
  - `instructions: str | None` (`Text` — optional standing project rules/brief)
  - `created_at`, `updated_at`
- **How Projects interact with the single ambient stream**:
  - Users stay in their single ambient conversation (`Chat`), and can optionally attach/activate a Project workspace scope (`project_id` on `POST /runs` / run metadata).
  - When `project_id` is set on a run, `RunsService` injects the project's `name`, `workspace_path`, and `instructions` into the run's system context and sets the sandbox/workspace working path to that project's directory.
  - In `Projects` mode (`HarkUnifiedApp.tsx`), the UI lists `projects` (with files, deliverables, and project brief) instead of chat threads; `+ New Project` creates a `ProjectModel` workspace.

### 2.3 Web UI Alignment (`web/src/components/hark/HarkUnifiedApp.tsx`)

- Replace `useSessions()` + auto-`useCreateSession()` with `usePrimarySession()` (`GET /api/v1/chat/primary`).
- Remove session creation from the Chat flow and simplify `/agent/chat` to load the primary stream directly (redirecting `/agent/chat/:sessionId` or handling it only as a deep-link fallback).
- Add upward scrollback pagination (`Load earlier messages` / scroll-top fetch via `before_id`) to the central feed.
- Wire the `Projects` mode and left column Projects list to the new `projects` API rather than `sessions.data`.

---

## 3. Design Pillar 2: Session-Level Rolling Compaction (No JEV)

### 3.1 Removal of JEV from Compaction

- Remove `JevClientProtocol`, `score_units`, `JEV_BATCH_SIZE`, `MAX_JEV_CALLS`, and Tier 0 user-message locking from `backend/src/modules/compaction/`.
- Remove `jev_client` from `CompactionService.__init__` and `get_compaction_service` in `backend/src/dependencies.py`.
- Note: `JevClient` remains untouched for `backend/src/modules/triage/` unless separately deprecated.

### 3.2 Interaction Tail Barrier & Rolling Window

Define an **interaction** as one completed `user` message together with its final `assistant` prose response (excluding intermediate `tool_calls` assistant rows and `tool` output rows).

- **Configuration Constants**:
  - `DEFAULT_INTERACTION_BARRIER = 8` (configurable `5..10`, e.g., `COMPACTION_INTERACTION_BARRIER: int = 8` in `Settings`).
  - `DEFAULT_COMPACTION_OVERFLOW_INTERACTIONS = 4` (trigger rolling compaction once `>= 4` completed interactions accumulate *above* the 8-interaction barrier, i.e., every ~12 uncompacted interactions, OR when estimated active context tokens exceed `context_token_limit * COMPACTION_TRIGGER_RATIO`).
- **Context Assembled for Every Run (`RunLoop._assemble`)**:
  1. **System Prompt (Head)**: `SOUL.md` + `USER.md` + `MEMORY.md` + Slot 4 Embedding Auto-Recall (+ active Project scope if selected).
  2. **Rolling Session Summary** (when `ThreadModel.rolling_summary` is non-empty): injected as a single compact system/context message right before recent interactions (`== Session Context Summary ==\n...`).
  3. **Verbatim Tail Barrier**: the newest `N = 8` interactions (or all uncompacted interactions after `compacted_through_message_id` up to the barrier trigger) kept verbatim.
  4. **Current In-Flight Run**: active user prompt + current run's tool calls and tool outputs.

### 3.3 Strict Summarizer Input Filter (No Tool Calls, No Thinking)

When rolling compaction runs on the overflow interactions above the 8-interaction barrier:

- **Included inputs to Fast LLM (`SUMMARY_TIER = "fast"`)**:
  1. The previous `ThreadModel.rolling_summary` (if present), so facts from earlier batches carry forward cleanly.
  2. Clean `(role, content)` text of the outgoing `user` and final `assistant` messages being rolled past the barrier.
- **Strictly excluded from summarizer inputs**:
  - All `tool_calls` JSON payloads and `tool` role output rows.
  - All `thinking` / reasoning traces and `run_steps` (`thought`, `tool_call`, `tool_result`).
- **Output format (`summarizer.py`)**:
  - Keep the bounded structured block (`What happened`, `Decisions`, `Open items`, default budget `700` tokens) produced by the Fast LLM and enforced post-generation.
  - Persist `(rolling_summary, compacted_through_message_id, compacted_through_at)` atomically on `ThreadModel`.
  - Never delete rows from `messages`: all historical turns remain in PostgreSQL for UI scrollback and `memory_search`.

### 3.4 Intra-Run Overflow Guard (No JEV)

If a single *active* run executes many tool calls before finishing and exceeds `context_token_limit * compact_ratio`:

- Deterministically replace the `content` of the oldest `tool` result messages inside the current run with compact preview stubs (retaining `tool_call_id` and `name` so the tool-call chain remains valid for the LLM API).
- Never invoke JEV and never send raw tool JSON dumps to the summarizer.

---

## 4. Design Pillar 3: Zero-Extra-Model Memory Architecture

No extra background extraction LLM is introduced. Vigil's three memory layers remain distinct, cheap, and complementary:

1. **Curated Always-On Files (`SOUL.md`, `USER.md` 1,500 chars, `MEMORY.md` 2,200 chars)**:
   - Maintained directly by the main agent via the `memory` tool.
   - Tighten `MEMORY_AND_PERSONA_GUIDANCE` in `backend/src/modules/runs/core_context.py`:
     - Save to `memory(target="user" | "memory", ...)` **only** when:
       1. The user explicitly asks to remember or forget something, **or**
       2. High-signal, durable core facts emerge (e.g., user's name, email address, phone number, role, location, hard preferences, standing project rules).
     - Explicitly instruct the agent **not** to log routine task chatter or transient status updates so character budgets stay lean and high-signal.
2. **Rolling Session Summary (Fast LLM)**:
   - Preserves sequential narrative continuity for turns between the 8-interaction verbatim tail and long-term archival history.
3. **Deep Semantic Recall (`memory_chunks` + Local Embedding Model)**:
   - Continues using `get_local_embedder` (zero LLM spend) over `user` + `assistant` prose only (`Chunker`).
   - **Continuous-Session Overlap Fix (`backend/src/modules/memory/service.py`)**:
     - When `index_session` fetches unindexed turns for a long-lived session, prepend the last `2` already-indexed `user`/`assistant` turns (`DEFAULT_OVERLAP_TURNS = 2`) as context for window segmentation (only marking the newly fetched turns with `indexed_at` and creating chunks when new turns exist).
     - This ensures single-turn completions on a single continuous session still produce overlapping 4–6 turn semantic windows for Slot 4 auto-recall (`top_n=4`) and `memory_search`.

---

## 5. Implementation & Verification Plan

### Phase 1: Single Ambient Session & Rolling Compaction (Backend)
1. **Migration (`0013`)**: Add `kind`, `rolling_summary`, `compacted_through_message_id`, `compacted_through_at` to `threads`; create `projects` table.
2. **Chat (`backend/src/modules/chat/`)**: Implement `get_or_create_primary_session`, `GET /api/v1/chat/primary`, and `before_id` cursor pagination on message history.
3. **Compaction (`backend/src/modules/compaction/`)**: Remove JEV (`JevClientProtocol` / `score_units`) and `_LOCKED_KINDS` user-blocking; implement `SessionCompactor` with 8-interaction tail barrier, clean `(user, assistant)` input filtering (no `tool_calls` or `thinking`), rolling summary persistence on `ThreadModel`, and deterministic intra-run tool-output stubbing.
4. **Runs (`backend/src/modules/runs/`)**: Update `list_session_history` to return `rolling_summary` + uncompacted tail interactions; hook session-level rolling compaction into `RunLoop` / `RunsService`.
5. **Memory (`backend/src/modules/memory/` & `core_context.py`)**: Tighten `MEMORY_AND_PERSONA_GUIDANCE` for core facts & explicit requests; add 2-turn overlap lookback in `MemoryService.index_session`.

### Phase 2: Scoped Projects & Web UI
1. **Projects Module (`backend/src/modules/projects/`)**: CRUD + workspace directory provisioning + `project_id` context injection on `POST /runs`.
2. **Frontend (`web/src/`)**: Bind Chat view to `GET /api/v1/chat/primary` with upward scrollback pagination; wire `Projects` view and `+ New Project` to `/api/v1/projects`.

### Verification Commands
- Backend unit tests: `cd backend && uv run pytest tests/unit/ -q`
- Targeted compaction, chat, memory, and skill checks: `cd backend && uv run pytest tests/unit/test_summarizer.py tests/unit/test_compaction_verify.py tests/unit/test_compaction_effectiveness.py tests/unit/test_chat_sql_paging.py tests/unit/test_memory_index.py tests/unit/test_chunker.py tests/unit/test_hermes_memory.py tests/unit/test_skill_manifest.py -q`
- Frontend typecheck & build: `cd web && npm run build`
