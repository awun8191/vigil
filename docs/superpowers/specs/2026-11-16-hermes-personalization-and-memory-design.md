# Hermes-Adapted Agent Personalization, Evolving User Profile, and Active Memory Design

**Date:** 2026-11-16
**Status:** Draft for Review
**Scope:** Backend Memory Store (`SOUL.md`, `USER.md`, `MEMORY.md`), Dynamic System Prompt Assembly & Semantic Auto-Recall, Active Harness Tools (`memory`, `memory_search`), Personalization REST API, and Settings Web UI.

---

## 1. Problem & Goal

Vigil currently indexes conversation turns into pgvector (`memory_chunks`) via `MemoryService`, but the agent runtime (`RunLoop` and `TOOLS`) cannot read, search, or write to its own memory during a run. Furthermore, Vigil's identity is a static string baked once into the `vigil-default` database row, with no configurable Markdown personality file, no persistent user profile, and no mechanism to learn the user's name or form an evolving opinion of the user.

Adapted from **`NousResearch/hermes-agent`** (`tools/memory_tool.py`, `tools/memory_tool_store.py`, and `agent/prompt_builder.py`), this design equips Vigil with:
1. **Configurable Personality Markdown (`SOUL.md`)**: Loaded from disk into Slot #1 of the system prompt at the start of every run, editable by both the user (via disk or Web UI Settings) and the agent (when asked in chat).
2. **Curated User Profile & Evolving Opinion (`USER.md`) + Agent Notes (`MEMORY.md`)**: File-backed, `\n§\n`-delimited entry stores with strict character budgets injected as a frozen snapshot into the system prompt at run initialization.
3. **Learned User Name Sync**: When the agent learns the user's name (via the `memory` tool or Settings UI), it updates both `USER.md` and `UserModel.display_name` in PostgreSQL so the entire app UI reflects the user's name.
4. **Hybrid Memory Access (`memory`, `memory_search`, and Auto-Recall)**:
   - Automatic semantic recall at run start from `MemoryService.search_memory` (pgvector + exponential time-decay).
   - Active `memory` tool (`add`, `patch`, `remove`, `read` on `user`, `memory`, and `soul` targets).
   - Active `memory_search` tool querying pgvector semantic chunks, recent unindexed chat messages across sessions, and curated `USER.md`/`MEMORY.md` entries.
5. **Interactive Web UI**: Full editors in Settings → Personalization and Settings → Profile for `SOUL.md` (with persona presets), `USER.md` entries, `MEMORY.md` entries, character budget bars, and display name.

---

## 2. Architecture & Components

### 2.1 File-Backed `MemoryStore` (`backend/src/modules/memory/store.py`)

Adapted from Hermes Agent's `MemoryStore`:

- **Base Directory (`VIGIL_HOME`)**:
  - Added to `Settings` in `backend/src/config/settings.py`: `VIGIL_HOME: str = Field(default="~/.vigil")`.
  - Resolved via `resolve_vigil_home(settings)`: expands `~`; if the path cannot be created (e.g., restricted container environment), falls back to `Path(settings.WORKSPACE_BASE_PATH) / "vigil_home"`.
- **Files Managed**:
  1. `<VIGIL_HOME>/SOUL.md` (`soul_char_limit = 8000` chars):
     - Seeded on first read with `DEFAULT_SOUL_MD` if absent; never overwritten automatically once created.
     - Strips optional YAML frontmatter (`--- ... ---`) when rendering into the prompt.
  2. `<VIGIL_HOME>/memories/USER.md` (`user_char_limit = 1500` chars):
     - Stores discrete entries joined by `ENTRY_DELIMITER = "\n§\n"`.
     - Captures user identity (name, role, technical background, preferences) and Vigil's candid, evolving opinion/impression of the user (how they think, working style, strengths, blind spots, and rapport).
  3. `<VIGIL_HOME>/memories/MEMORY.md` (`memory_char_limit = 2200` chars):
     - Stores discrete entries joined by `ENTRY_DELIMITER = "\n§\n"`.
     - Captures durable environment facts, project conventions, tool quirks, and standing lessons.
- **Atomicity, Concurrency & Content Safety**:
  - Every write acquires an advisory lock (`fcntl.flock` on `<file>.lock`) and writes to a temporary sibling file before `os.replace`.
  - `_scan_memory_content(text)` strips UTF-8 BOM, rejects invisible Unicode bidi/zero-width control characters, and normalizes embedded `\n§\n` sequences inside single entries so delimiter boundaries remain intact.
- **Operations Supported on `MemoryStore`**:
  - `load_soul() -> str`, `save_soul(content: str) -> dict`, `patch_soul(old_text: str, new_text: str) -> dict`, `reset_soul() -> dict`.
  - `get_entries(target: Literal["user", "memory"]) -> list[str]`.
  - `set_entries(target: Literal["user", "memory"], entries: Sequence[str]) -> dict`.
  - `apply_batch(target: Literal["user", "memory", "soul"], operations: Sequence[Mapping[str, Any]]) -> dict`:
    - Supports `action` in `{"add", "patch", "remove", "read"}`.
    - Evaluates all mutations in-memory first and checks the character limit (`len(ENTRY_DELIMITER.join(entries)) <= limit`) against the **final** result, so a single batch can remove/shorten stale entries and add new ones atomically even when the store is near capacity.
    - On overflow, leaves the file untouched and returns:
      ```json
      {
        "success": false,
        "error": "Character limit exceeded (1580/1500 chars). Reissue as one batch that removes or patches stale entries and adds the new entry together.",
        "current_entries": ["..."],
        "usage": {"current": 1420, "limit": 1500}
      }
      ```
  - `extract_user_name() -> str | None`:
    - Scans `USER.md` entries for `Name: <name>` or `User's name is <name>` patterns.

---

### 2.2 Dynamic System Prompt Assembly & Auto-Recall (`core_context.py`, `loop.py`, `service.py`)

#### Slot Structure in `build_core_system_prompt` (`backend/src/modules/runs/core_context.py`)
1. **Slot 1 — Identity (`SOUL.md`) & Platform Operational Rules**:
   - `DEFAULT_BASE_IDENTITY` is split into:
     - `DEFAULT_SOUL_MD`: Vigil's persona, voice, intellectual honesty, and permission to form genuine opinions and adapt to the user.
     - `PLATFORM_OPERATIONAL_RULES`: Living canvas deliverable rules (`generate_document`, `deliver_file`), live browser preview rules, `[Verb]` pills, greeting turn boundaries, non-interactive CLI flags, and ASD-STE100 clarity.
   - If `SOUL.md` is present on disk (or seeded), its Markdown body is placed first in Slot 1, followed by `PLATFORM_OPERATIONAL_RULES`.
2. **Slot 2 — Engineering & Brainstorming Philosophy + Active Skills**:
   - `CORE_PHILOSOPHY_AND_BRAINSTORMING`, `proactivity` skill body, and active skill catalog.
3. **Slot 3 — Persistent Memory & User Profile Guidance + Frozen Snapshot**:
   - `MEMORY_AND_PERSONA_GUIDANCE` (adapted from Hermes's `build_memory_guidance` and `SESSION_SEARCH_GUIDANCE`):
     - Instructs Vigil to use `memory(target="user", ...)` when it learns the user's name, role, preferences, or forms/updates an opinion of the user's style, priorities, and habits.
     - Instructs Vigil to use `memory(target="memory", ...)` for durable cross-session facts and `memory(target="soul", ...)` when the user asks to customize Vigil's personality or tone.
     - Instructs Vigil to call `memory_search(query=...)` whenever the user references past work, prior conversations, or historical decisions not already in the prompt.
   - Frozen Snapshot Blocks:
     - `## USER PROFILE (who the user is & your evolving impression) [X/1500 chars]`
     - `## MEMORY (your personal notes) [Y/2200 chars]`
4. **Slot 4 — Semantic Auto-Recall (`## RECALLED PAST CONTEXT`)**:
   - During run preparation in `RunsService.process` (and passed into `RunLoop` via `extra_system_context` or dynamic prompt assembly), if the run has a user message and a user owner:
     - Queries `MemoryService.search_memory(user_id=owner_id, query=latest_user_prompt, top_n=4)`.
     - Filters chunks with `final_score > 0` and formats them into `## RECALLED PAST CONTEXT (from prior conversations)` appended to the system prompt for that run.
     - Any failure in embedding or DB lookup degrades gracefully (logs at debug and omits Slot 4 so a run never fails if pgvector is offline).

---

### 2.3 Active Harness Tools (`backend/src/modules/runs/tools/memory.py`)

Both tools are registered in `TOOLS` (`backend/src/modules/runs/tools/__init__.py`, increasing `TOOL_COUNT` from `18` to `20`) and added to `RunToolExecutor.HOST_ONLY_TOOLS` (`backend/src/dependencies.py`) and default agent config `tools`.

#### Tool 1: `memory`
- **Approval Level**: `"local"` (executes on host without sandbox container creation or user approval blocks).
- **Arguments Schema (`MemoryToolArgs`)**:
  - `target`: `Literal["user", "memory", "soul"] = "memory"`
  - `operations`: `list[MemoryOperationItem] | None = None` where each item has:
    - `action`: `Literal["add", "patch", "remove", "read"]`
    - `content`: `str | None = None`
    - `old_text`: `str | None = None`
  - `action`: `Literal["add", "patch", "remove", "read"] | None = None` (single-op shorthand compatibility)
  - `content`: `str | None = None`
  - `old_text`: `str | None = None`
  - `user_name`: `str | None = None` (optional explicit user display name to persist when learned)
- **Execution Flow**:
  1. Normalizes single-op arguments (`action`, `content`, `old_text`) into a 1-element `operations` list if `operations` is omitted.
  2. If `user_name` is provided, automatically ensures a `Name: <user_name>` entry is added/patched in `USER.md` and updates `UserModel.display_name` in PostgreSQL (when `context.session` and `context.user_id` are available).
  3. Applies the batch via `MemoryStore.apply_batch(target, operations)`.
  4. If `target == "user"` and a `Name: <value>` entry was added or patched, extracts the name and updates `UserModel.display_name` in PostgreSQL.
  5. Returns `ToolResult.success(result_dict)` when `result["success"]` is True, or `ToolResult.failure(result["error"], content=result_dict)` when validation/budget check fails (so the model still receives `current_entries` and `usage` in `content` to self-correct).

#### Tool 2: `memory_search`
- **Approval Level**: `"read"` (in `HOST_ONLY_TOOLS`).
- **Arguments Schema (`MemorySearchToolArgs`)**:
  - `query`: `str = Field(..., min_length=1, max_length=2000)`
  - `top_n`: `int = Field(default=8, ge=1, le=25)`
  - `scopes`: `list[str] = Field(default_factory=lambda: ["global"])`
- **Execution Flow**:
  1. **Curated Store Matches**: Searches `USER.md` and `MEMORY.md` entries for case-insensitive query token matches.
  2. **Semantic pgvector Search**: Calls `MemoryService(session=context.session, settings=settings).search_memory(user_id=context.user_id, query=args.query, scopes=args.scopes, top_n=args.top_n)` when `context.user_id` is available.
  3. **Recent Unindexed Session Search**: Queries `MessageModel` joined with `ThreadModel` for the user's messages (excluding `context.run_id`) matching query keywords (`ILIKE`), returning up to `top_n` recent turns so conversations that have not yet been background-indexed are still discoverable.
  4. Returns `ToolResult.success({"query": args.query, "curated_matches": [...], "semantic_chunks": [...], "session_messages": [...]})`.

---

### 2.4 REST API Endpoints (`backend/src/modules/memory/router.py` & `data.py`)

Added to `build_memory_router`:
- **`GET /api/v1/memory/personalization` (`PersonalizationResponse`)**:
  ```json
  {
    "display_name": "Alex",
    "soul_md": "# Vigil Identity...",
    "soul_usage": {"current": 642, "limit": 8000},
    "user_md": "Name: Alex\n§\nPrefers direct systems architecture...",
    "user_entries": ["Name: Alex", "Prefers direct systems architecture..."],
    "user_usage": {"current": 128, "limit": 1500},
    "memory_md": "Repo uses uv and FastAPI...",
    "memory_entries": ["Repo uses uv and FastAPI..."],
    "memory_usage": {"current": 95, "limit": 2200}
  }
  ```
- **`PUT /api/v1/memory/personalization` (`PersonalizationUpdateRequest`)**:
  - Accepts optional fields: `display_name`, `soul_md`, `user_md`, `user_entries`, `memory_md`, `memory_entries`.
  - Validates character limits (`422 ValidationError` if exceeded).
  - Writes updated files atomically via `MemoryStore`.
  - If `display_name` is provided (or updated in `user_entries`/`user_md`), updates `UserModel.display_name` in PostgreSQL for the authenticated user.
  - Returns the updated `PersonalizationResponse`.

---

### 2.5 Web UI Settings (`web/src/components/settings/sections.tsx`, `web/src/queries/settings.ts`, `web/src/types/index.ts`)

1. **`ProfileSection`**:
   - Replaces static Name row with an editable Name input + Save button wired to `useUpdatePersonalization()`.
   - Updates `useSession` device `user_display_name` on save so `HarkTopChrome`, `HarkProfileSheet`, and `SettingsPage` header update immediately.
2. **`PersonalizationSection`**:
   - **Agent Personality (`SOUL.md`)**:
     - Preset pills (*Default Vigil*, *Candid & Opinionated Partner*, *Concise Systems Engineer*, *Socratic Collaborator*) to load a template into the editor.
     - Monospace Markdown `<textarea>` with live character budget (`X / 8,000 chars`) and Save button.
   - **User Profile & Agent's Opinion (`USER.md`)**:
     - Progress bar showing character budget (`X / 1,500 chars`).
     - Interactive entry list: view each `\n§\n` entry as a card, edit or remove existing entries, or add a new entry (e.g. preferences, role, notes).
   - **Agent Working Memory (`MEMORY.md`)**:
     - Progress bar showing character budget (`X / 2,200 chars`).
     - Interactive entry list with add, edit, and remove controls.
   - Preserves existing Model Tier & Approval Policy display below.

---

## 3. Verification & Testing Plan

1. **Unit Tests (`backend/tests/unit/test_hermes_memory.py`)**:
   - `MemoryStore`: seeding `SOUL.md`, frontmatter stripping, `\n§\n` entry parsing, atomic batch `add`/`patch`/`remove`, character budget overflow rejection returning `current_entries` and `usage`, control-character/delimiter sanitization, and `Name:` extraction.
   - `memory` tool: adding/patching/removing entries in `user` and `memory`, updating `soul`, atomic batch execution, and syncing `UserModel.display_name`.
   - `memory_search` tool: returning matches across curated `USER.md`/`MEMORY.md`, pgvector `MemoryService`, and recent session transcripts.
   - Dynamic prompt assembly: verifying `SOUL.md`, `USER.md`, `MEMORY.md`, and semantic auto-recall blocks appear in the assembled system prompt.
2. **Existing Suite Compatibility**:
   - Update `TOOL_COUNT` in `backend/src/modules/runs/tools/__init__.py` and `backend/tests/unit/test_tools.py` (`18` -> `20`) and ensure existing tool/run unit tests pass.
3. **Frontend Build**:
   - Run `npm run build` in `web/` to verify zero TypeScript or bundler errors.
