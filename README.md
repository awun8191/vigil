# Vigil (`virgil-agent`)

> **Open-Source, Self-Hostable Proactive AI Assistant & Sandboxed Execution Harness**  
> *Draft 0.1 Technical Specification & Production Implementation Blueprint*

---

## Table of Contents

1. [Executive Summary & System Philosophy](#1-executive-summary--system-philosophy)
2. [Viability & Why Traditional Agent Architectures Fail](#2-viability--why-traditional-agent-architectures-fail)
3. [Module-Driven Architecture & Coding Standards](#3-module-driven-architecture--coding-standards)
4. [Complete Monorepo Directory Layout](#4-complete-monorepo-directory-layout)
5. [Centralized Database Schemas & Table Definitions](#5-centralized-database-schemas--table-definitions)
6. [Deep-Dive: Core Subsystem 1 — The Stateless 6-Stage Event Loop & Multi-Tool Barrier Synchronizer](#6-deep-dive-core-subsystem-1--the-stateless-6-stage-event-loop--multi-tool-barrier-synchronizer)
7. [Deep-Dive: Core Subsystem 2 — Long-Term Memory & Exponential Time-Decay Retrieval (`pgvector`)](#7-deep-dive-core-subsystem-2--long-term-memory--exponential-time-decay-retrieval-pgvector)
8. [Deep-Dive: Core Subsystem 3 — Postgres-Backed Job Queue (`SKIP LOCKED`) & Worker Engine](#8-deep-dive-core-subsystem-3--postgres-backed-job-queue-skip-locked--worker-engine)
9. [Deep-Dive: Core Subsystem 4 — Two-Tier Context Compaction (Jev Filter + Fast LLM Summarizer)](#9-deep-dive-core-subsystem-4--two-tier-context-compaction-jev-filter--fast-llm-summarizer)
10. [Deep-Dive: Core Subsystem 5 — Two-Stage Proactive Triage Pipeline](#10-deep-dive-core-subsystem-5--two-stage-proactive-triage-pipeline)
11. [Deep-Dive: Core Subsystem 6 — Docker Sandbox Runner & Volume Lifecycle](#11-deep-dive-core-subsystem-6--docker-sandbox-runner--volume-lifecycle)
12. [Deep-Dive: Core Subsystem 7 — Connectors Engine & Standalone Gmail MCP Connector](#12-deep-dive-core-subsystem-7--connectors-engine--standalone-gmail-mcp-connector)
13. [Exhaustive Phase-by-Phase Implementation Roadmap](#13-exhaustive-phase-by-phase-implementation-roadmap)

---

## 1. Executive Summary & System Philosophy

Vigil acts as an autonomous personal chief of staff. It continuously monitors incoming communication streams, tracked tasks, deadlines, and project milestones. Rather than passively waiting for user prompts, Vigil triages events using a cost-efficient decision model (Jev), surfaces actionable notification cards to a companion Flutter mobile app, and executes complex multi-step coding, document compilation, file transformation, and communication runs inside an isolated Linux sandbox under an approval-first security model.

### Key Architectural Tenets

1. **Approval-First Execution**: The agent can inspect files, write code, run compilers, and generate artifacts freely inside its isolated Docker sandbox. However, **any external side-effect** (sending an email, creating external drafts, publishing webhooks, modifying third-party accounts) strictly pauses execution and requires user approval via the mobile card UI.
2. **Stateless Process Deallocation**: When an execution pauses for human approval or an asynchronous background task, the worker process **releases all memory and terminates**. Run state is serialized entirely within PostgreSQL. Approvals hours or days later resume execution seamlessly, surviving server restarts and container evictions.
3. **Single Datastore Simplicity**: By running relational state, the atomic worker queue (`FOR UPDATE SKIP LOCKED`), and vector memory (`pgvector`) within PostgreSQL 16+, Vigil eliminates the operational complexity of Redis, Celery, or external vector databases for self-hosters.
4. **Decoupled Connectors via Model Context Protocol (MCP)**: External services (Gmail, Google Calendar, Slack, GitHub) are not hardcoded into core application logic. They are implemented as standalone MCP connectors that expose polling capabilities and tools governed by standard approval policies.

---

## 2. Viability & Why Traditional Agent Architectures Fail

### The In-Memory Trap
Most popular agent frameworks (LangChain, AutoGen, CrewAI, naive Pydantic AI loops) maintain execution state in the runtime memory of an async Python thread or generator:

```python
# NAIVE AGENT PATTERN (FAILS IN PRODUCTION)
async def run_loop():
    while not done:
        response = await model.call(history, tools)
        for tool_call in response.tool_calls:
            if tool_call.requires_approval:
                user_ok = await wait_for_user_approval(tool_call) # BLOCKS IN-MEMORY!
```

This pattern fails in production environments for four critical reasons:
1. **Thread/Memory Exhaustion**: If a user takes 6 hours to respond to an approval notification, a worker thread or process remains locked in memory. At scale or on self-hosted VPS nodes, system resources are quickly exhausted.
2. **Server Restart & Eviction Vulnerability**: If the server restarts, containers roll out, or a cold start occurs, all active, paused, or queued runs are dropped and lost.
3. **Parallel Tool Call ID Desynchronization**: Modern LLMs (OpenAI GPT-4o, Anthropic Claude 3.5, Gemini 1.5) frequently emit *multiple parallel tool calls* in a single turn. For example:
   $$\text{Turn } N \rightarrow [C_1: \text{read\_file}, \ C_2: \text{compile\_artifact}, \ C_3: \text{gmail\_send}]$$
   The model API requires that every single tool call ID emitted in Turn $N$ must receive a corresponding `role="tool"` message before Turn $N+1$ can be called. If $C_1$ is auto-approved, $C_2$ is asynchronous, and $C_3$ requires user approval, a naive loop pauses without generating the tool result for $C_1$ or $C_2$. Resuming later produces API protocol violations because IDs are missing or out of order.
4. **Docker Volume Drift**: If an in-progress run modifies files in `/workspace` before pausing, and the sandbox container is reaped due to an idle timeout, resuming hours later in a fresh container results in lost files.

### The Virgil Solution
Virgil eliminates these failure modes through:
- **Stateless DB-backed transitions**: Runs transition between database enum states (`QUEUED`, `RUNNING`, `AWAITING_APPROVAL`, `AWAITING_BACKGROUND_JOB`, `STALE_THREAD_PAUSED`, `COMPLETED`, `FAILED`).
- **A Multi-Tool Barrier Synchronizer**: Partitions parallel tool calls into immediate execution, background queuing, and gated approvals, persisting all partial results and guaranteeing that every tool call ID is satisfied before resuming model inference.
- **Decoupled Workspace Snapshots**: `/workspace` is synced to host storage on pause and re-hydrated into a clean container on resume.

---

## 3. Module-Driven Architecture & Coding Standards

Every backend domain follows Nas's strict **Module-Driven FastAPI Architecture**:

```text
src/modules/<resource>/
├── repository.py      # Holds ONLY typing.Protocol abstractions and DTOs. Zero SQL queries, zero HTTP calls.
├── service.py         # Implements ALL domain logic, database operations, and external client calls.
├── data.py            # Pydantic v2 request/response schemas (DTOs).
├── dependencies.py    # FastAPI DI providers wiring service instances with sessions and settings.
└── router.py          # build_<resource>_router(settings: Settings) -> APIRouter factory.
```

### Architectural Invariants

1. **Repositories are Pure Abstractions**:
   `src/modules/<module>/repository.py` contains exclusively `@runtime_checkable` `typing.Protocol` declarations. No database queries, no ORM session usage, no network calls. It defines the contract of what the module needs.
2. **Services Own All Mechanism and Execution**:
   `src/modules/<module>/service.py` receives the database session (`AsyncSession`), configuration (`Settings`), and integration protocols via constructor dependency injection. It implements the methods declared by the repository protocol via **duck typing**.
3. **Router Factory Pattern**:
   Routers are never exposed as bare, module-level globals (`router = APIRouter(...)`). Every module must expose:
   ```python
   def build_<resource>_router(settings: Settings) -> APIRouter:
       router = APIRouter(prefix="/<resource>", tags=["<Resource>"])
       # Declare route handlers
       return router
   ```
4. **Centralized Schemas vs. Module DTOs**:
   - SQLAlchemy 2.0 ORM models (`Mapped`, `mapped_column`) reside strictly under `src/schemas/`.
   - Module `data.py` files contain only Pydantic v2 validation schemas and wire DTOs.
5. **Central Dependency Aggregation**:
   `src/dependencies.py` is the parent hub importing and re-exporting all module providers and integration dependencies.
6. **Uniform Error Contract**:
   All errors inherit from `AppException` (`src.config.exceptions.AppException`) and return a predictable JSON payload containing correlation tracking:
   ```json
   {
     "code": "RESOURCE_NOT_FOUND",
     "message": "The requested card was not found",
     "request_id": "c678d3cc-6998-4027-8b57-7540a1659011",
     "details": null
   }
   ```

---

## 4. Complete Monorepo Directory Layout

```text
virgil-agent/
├── backend/
│   ├── pyproject.toml                   # Managed strictly via uv
│   ├── uv.lock
│   ├── alembic.ini                      # Alembic database migration config
│   ├── migrations/                      # Alembic migrations (Postgres + pgvector)
│   │   ├── env.py
│   │   └── versions/
│   │       └── 0001_initial_vigil_schema.py
│   ├── src/
│   │   ├── __init__.py
│   │   ├── main.py                      # FastAPI app factory (create_app) & lifespan
│   │   ├── worker.py                    # Background worker & scheduler process entrypoint
│   │   ├── dependencies.py              # Central parent DI aggregator
│   │   │
│   │   ├── config/                      # Core configuration, errors, router root
│   │   │   ├── __init__.py
│   │   │   ├── settings.py              # Pydantic Settings (env vars, tiered models, DB, sandbox)
│   │   │   ├── exceptions.py            # AppException hierarchy + uniform error handlers
│   │   │   ├── logging.py               # Structured JSON/console logging + request_id contextvar
│   │   │   ├── security.py              # Cryptographic hashing & AES-GCM credential encryption
│   │   │   └── router.py                # create_api_router(settings) root router factory
│   │   │
│   │   ├── middlewares/                 # ASGI Middlewares
│   │   │   ├── __init__.py
│   │   │   ├── request_id.py            # X-Request-ID correlation tracking
│   │   │   ├── timing.py                # X-Process-Time performance headers
│   │   │   └── idempotency.py           # Idempotency-Key validation for mutating endpoints
│   │   │
│   │   ├── schemas/                     # ONLY Centralized SQLAlchemy 2.0 ORM Models
│   │   │   ├── __init__.py
│   │   │   ├── base.py                  # DeclarativeBase, UUIDPrimaryKeyMixin, TimestampMixin
│   │   │   ├── identity.py              # users, devices
│   │   │   ├── connectors.py            # connector_accounts, sync_state, items
│   │   │   ├── triage.py                # triage_results, cards
│   │   │   ├── runtime.py               # agent_configs, runs, messages, approvals, artifacts, run_steps
│   │   │   ├── memory.py                # memory_chunks (pgvector Vector column), skills
│   │   │   └── system.py                # jobs (SKIP LOCKED queue), audit_log
│   │   │
│   │   ├── integrations/                # Infrastructure & Foundation Model Clients
│   │   │   ├── __init__.py
│   │   │   ├── repositories.py          # Abstract Protocols (typing.Protocol) for all integrations
│   │   │   ├── database/
│   │   │   │   ├── __init__.py
│   │   │   │   └── database.py          # AsyncEngine, AsyncSession factory, pgvector setup
│   │   │   ├── jev/
│   │   │   │   ├── __init__.py
│   │   │   │   └── jev.py               # TypeSafe AI Jev Decision API client
│   │   │   ├── llm/
│   │   │   │   ├── __init__.py
│   │   │   │   └── llm.py               # OpenAI-compatible multi-provider adapter (Tier 2 Fast + Tier 3 Reasoning)
│   │   │   ├── sandbox/
│   │   │   │   ├── __init__.py
│   │   │   │   └── sandbox.py           # Docker socket proxy runner & /workspace snapshots
│   │   │   ├── mcp/
│   │   │   │   ├── __init__.py
│   │   │   │   └── mcp.py               # MCP client host (stdio & SSE transports)
│   │   │   └── notifications/
│   │   │       ├── __init__.py
│   │   │       └── ntfy.py              # ntfy.sh / UnifiedPush / FCM push dispatcher
│   │   │
│   │   └── modules/                     # Domain Feature Modules
│   │       ├── __init__.py
│   │       ├── health/                  # Liveness and readiness probes
│   │       ├── auth/                    # Pairing codes, QR provisioning, device bearer auth
│   │       ├── cards/                   # Action cards feed, lifecycle, feedback loop
│   │       ├── triage/                  # Two-stage triage pipeline (Jev gate -> Fast LLM metadata)
│   │       ├── runs/                    # Stateless Agent Event Loop, step execution, SSE streaming
│   │       ├── approvals/               # Pending approvals queue, decision handling
│   │       ├── chat/                    # Direct interactive agent sessions
│   │       ├── artifacts/               # Workspace artifact delivery & storage
│   │       ├── memory/                  # Chunking, embeddings & exponential time-decay retrieval
│   │       ├── compaction/              # Two-tier context compaction (Jev filter + LLM summarizer)
│   │       ├── skills/                  # Skill manifests, registry, loading into sandbox
│   │       ├── connectors/              # MCP connector manager, sync orchestration
│   │       ├── settings/                # Runtime system configuration & limits
│   │       └── jobs/                    # Postgres SKIP LOCKED worker & cron scheduler
│   │
│   └── tests/
│       ├── conftest.py
│       ├── unit/
│       ├── integration/
│       └── replay/
├── connectors/                          # Standalone MCP Connectors (Decoupled from Core)
│   └── gmail/                           # Gmail MCP server (polling, history sync, search, draft, send)
├── mobile/                              # Deferred: Flutter companion mobile application
│   └── README.md
├── web/                                 # Deferred: Portfolio hybrid replay demo web application
│   └── README.md
├── skills/                              # Bundled example skills (client-inquiry, weekly-report, etc.)
├── deploy/                              # Docker Compose, environment templates, systemd units
└── docs/                                # Architectural specifications and reference guides
```

---

## 5. Centralized Database Schemas & Table Definitions

The schema needs 21 tables. Sixteen tables exist in the code today. Two tables come from the corrections in Section 5.8, which gives 18. Three tables are still missing, and Section 5.9 names them.

Section 5.8 lists the corrections. Each correction overrides the code. The text of Sections 5.2 to 5.7 already includes the corrections.

### 5.1 Base (`src/schemas/base.py`)

`base.py` defines one class. It defines no mixins.

```python
convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=convention)
```

- `Base` extends `DeclarativeBase`. It adds one attribute.
- `metadata` carries a naming convention. Every constraint name is deterministic.
- There is no `UUIDPrimaryKeyMixin`. There is no `CreatedAtMixin`. There is no `TimestampMixin`.
- Each model declares `__tablename__` and its own `created_at` column.
- Every primary key is a UUID. The application sets the value with `uuid4`.

### 5.2 Identity Tables (`src/schemas/identity.py`)

- **`users`**
  - `id`: uuid, primary key, app default `uuid4`
  - `display_name`: varchar(255), not null
  - `settings`: jsonb, not null, default `'{}'::jsonb`
  - `created_at`: timestamptz, not null, default `now()`
  - `updated_at`: timestamptz, not null, default `now()`, on update `now()`
- **`devices`**
  - `id`: uuid, primary key, app default `uuid4`
  - `user_id`: uuid, not null, FK `users.id`, on delete cascade, indexed
  - `name`: varchar(100), not null
  - `token_hash`: varchar(64), not null, unique index `ix_devices_token_hash`
  - `push_token`: varchar(500), null
  - `last_seen_at`: timestamptz, not null, default `now()`
  - `revoked_at`: timestamptz, null
  - `created_at`: timestamptz, not null, default `now()`
  - *Index*: `ix_devices_user_id` on `(user_id)`

### 5.3 Connectors Tables (`src/schemas/connectors.py`)

- **`connected_accounts`**
  - `id`: uuid, primary key, app default `uuid4`
  - `user_id`: uuid, not null, FK `users.id`, on delete cascade, indexed
  - `connector_id`: varchar(64), not null, indexed
  - `account_label`: varchar(255), not null
  - `encrypted_credentials`: text, not null
  - `scopes`: jsonb, not null, default `'[]'::jsonb`
  - `status`: varchar(32), not null, default `'active'`, indexed
  - `created_at`: timestamptz, not null, default `now()`
  - `updated_at`: timestamptz, not null, default `now()`, on update `now()`
  - *Constraint*: `UNIQUE(user_id, connector_id, account_label)` named `uq_connected_accounts_user_conn_label`
  - *Removed*: `enabled_tools` (jsonb). The `connector_tools` table replaces it. A tool name alone carries no approval level.
- **`connector_tools`**
  - `id`: uuid, primary key, app default `uuid4`
  - `connector_account_id`: uuid, not null, FK `connected_accounts.id`, on delete cascade
  - `tool_name`: text, not null
  - `level`: varchar(16), not null, allowed values `read`, `local`, `external`
  - `enabled`: boolean, not null, default `true`
  - *Constraint*: `UNIQUE(connector_account_id, tool_name)` named `uq_connector_tools_account_tool`. The unique index serves the lookup by account.
  - *Check*: `level IN ('read', 'local', 'external')` named `ck_connector_tools_level`
- **`connector_items`**
  - `id`: uuid, primary key, app default `uuid4`
  - `connector_account_id`: uuid, not null, FK `connected_accounts.id`, on delete cascade, indexed
  - `external_id`: varchar(255), not null
  - `thread_id`: varchar(255), not null, indexed
  - `sender`: varchar(255), not null
  - `recipients`: jsonb, not null, default `'[]'::jsonb`
  - `subject`: text, not null
  - `body_text`: text, not null
  - `received_at`: timestamptz, not null, indexed
  - `labels`: jsonb, not null, default `'[]'::jsonb`
  - `raw_reference`: text, null
  - `created_at`: timestamptz, not null, default `now()`
  - *Constraint*: `UNIQUE(connector_account_id, external_id)` named `uq_connector_items_account_external_id`
  - *Index*: `ix_connector_items_account_received` on `(connector_account_id, received_at)`
- **`connector_sync_state`**
  - `connector_account_id`: uuid, primary key, FK `connected_accounts.id`, on delete cascade
  - `cursor`: varchar(255), not null
  - `last_success_at`: timestamptz, not null, default `now()`
  - `last_error`: text, null
  - `updated_at`: timestamptz, not null, default `now()`, on update `now()`

### 5.4 Triage & Action Cards (`src/schemas/triage.py`)

- **`triage_decisions`**
  - `id`: uuid, primary key, app default `uuid4`
  - `item_id`: uuid, not null, FK `connector_items.id`, on delete cascade, indexed
  - `answers`: jsonb, not null, default `'{}'::jsonb`
  - `probabilities`: jsonb, not null, default `'{}'::jsonb`
  - `decision`: varchar(64), not null, indexed
  - `model_version`: varchar(64), not null
  - `created_at`: timestamptz, not null, default `now()`
  - *Constraint*: `UNIQUE(item_id, model_version)` named `uq_triage_decisions_item_model`. A re-triage stores a new verdict. The code declares `UNIQUE(item_id)`, which blocks a second verdict.
  - *Index*: `ix_triage_decisions_item_id` on `(item_id)`, not unique. `ix_triage_decisions_decision` on `(decision)`.
- **`cards`**
  - `id`: uuid, primary key, app default `uuid4`
  - `user_id`: uuid, not null, FK `users.id`, on delete cascade, indexed
  - `item_id`: uuid, null, FK `connector_items.id`, on delete set null, indexed
  - `thread_id`: varchar(255), null, indexed
  - `type`: varchar(64), not null, indexed
  - `urgency`: varchar(32), not null, indexed
  - `title`: varchar(500), not null
  - `summary`: text, null
  - `due_at`: timestamptz, null, indexed
  - `status`: varchar(32), not null, default `'new'`, indexed
  - `feedback`: varchar(32), null
  - `created_at`: timestamptz, not null, default `now()`
  - `updated_at`: timestamptz, not null, default `now()`, on update `now()`
  - *Indexes*: `ix_cards_user_status_urgency` on `(user_id, status, urgency)`. `ix_cards_user_due_at` on `(user_id, due_at)`.
- **`card_actions`**
  - `id`: uuid, primary key, app default `uuid4`
  - `card_id`: uuid, not null, FK `cards.id`, on delete cascade, indexed
  - `action_key`: varchar(64), not null
  - `label`: varchar(255), not null
  - `action_type`: varchar(64), not null
  - `payload`: jsonb, not null, default `'{}'::jsonb`
  - `sequence`: integer, not null, default `0`
  - `created_at`: timestamptz, not null, default `now()`
  - *Note*: the parent loads these rows in `sequence` order with `lazy="selectin"`.

### 5.5 Runtime Execution & Steps (`src/schemas/runtime.py`)

- **`threads`**
  - `id`: uuid, primary key, app default `uuid4`
  - `user_id`: uuid, not null, FK `users.id`, on delete cascade, indexed
  - `title`: varchar(255), null
  - `created_at`: timestamptz, not null, default `now()`
  - `updated_at`: timestamptz, not null, default `now()`, on update `now()`
- **`runs`**
  - `id`: uuid, primary key, app default `uuid4`
  - `config_id`: uuid, null, indexed. It points at `agent_configs.id`. The table does not exist yet. See Section 5.9.
  - `card_id`: uuid, null, FK `cards.id`, on delete set null, indexed
  - `parent_run_id`: uuid, null, FK `runs.id`, on delete set null, indexed
  - `depth`: integer, not null, default `0`
  - `status`: varchar(64), not null, default `'queued'`, indexed
  - `started_at`: timestamptz, not null, default `now()`
  - `ended_at`: timestamptz, null
  - `token_usage`: jsonb, not null, default `'{}'::jsonb`
  - `cost_micros`: bigint, not null, default `0`. The value is micro-USD. Money is an integer.
  - `workspace_ref`: text, null. The value is the snapshot archive path.
  - *Removed*: the float money column. `cost_micros` replaces it.
- **`run_workspaces`**
  - `run_id`: uuid, primary key, FK `runs.id`, on delete cascade
  - `archive_path`: text, not null
  - `size_bytes`: bigint, not null
  - `checksum`: text, not null
  - `created_at`: timestamptz, not null, default `now()`
  - *Note*: resume reads the archive path from this row. It never guesses a filename.
- **`messages`**
  - `id`: uuid, primary key, app default `uuid4`
  - `session_id`: uuid, null, FK `threads.id`, on delete cascade, indexed
  - `run_id`: uuid, null, FK `runs.id`, on delete cascade, indexed
  - `role`: varchar(32), not null
  - `content`: text, null
  - `tool_calls`: jsonb, null
  - `tool_call_id`: varchar(255), null, indexed
  - `tool_name`: varchar(255), null
  - `tool_arguments`: jsonb, null
  - `tool_result_reference`: text, null
  - `created_at`: timestamptz, not null, default `now()`
  - `indexed_at`: timestamptz, null. The chunking job sets it. A null value marks a message as not yet folded into a memory chunk.
  - *Indexes*: `ix_messages_session_created` on `(session_id, created_at)`. `ix_messages_run_created` on `(run_id, created_at)`. `ix_messages_unindexed` on `(indexed_at)` with predicate `WHERE indexed_at IS NULL`.
- **`approvals`**
  - `id`: uuid, primary key, app default `uuid4`
  - `run_id`: uuid, not null, FK `runs.id`, on delete cascade, indexed
  - `tool_call_id`: varchar(255), not null, indexed
  - `tool_name`: varchar(255), not null
  - `arguments`: jsonb, not null, default `'{}'::jsonb`
  - `status`: varchar(32), not null, default `'pending'`, indexed
  - `decided_at`: timestamptz, null
  - `decision_note`: text, null
  - `created_at`: timestamptz, not null, default `now()`
- **`artifacts`**
  - `id`: uuid, primary key, app default `uuid4`
  - `run_id`: uuid, not null, FK `runs.id`, on delete cascade, indexed
  - `kind`: varchar(64), not null, indexed
  - `path`: text, not null
  - `size_bytes`: bigint, not null
  - `preview`: text, null
  - `created_at`: timestamptz, not null, default `now()`

### 5.6 Memory (`src/schemas/memory.py`)

- **`memory_chunks`**
  - `id`: uuid, primary key, app default `uuid4`
  - `user_id`: uuid, not null, FK `users.id`, on delete cascade, indexed
  - `scope`: varchar(64), not null, indexed
  - `content`: text, not null
  - `summary`: text, null
  - `source_message_ids`: jsonb, not null, default `'[]'::jsonb`
  - `embedding`: vector(384), not null. The model is bge-small-en-v1.5 with int8 ONNX.
  - `embedding_model`: varchar(64), not null
  - `embedding_dim`: integer, not null. The writer stores 384 here.
  - `card_id`: uuid, null, FK `cards.id`, on delete cascade, indexed. Required when `scope` is `card`.
  - `agent_config_id`: uuid, null, FK `agent_configs.id`. Required when `scope` is `agent`. See Section 5.9.
  - `excluded`: boolean, not null, default `false`, indexed
  - `created_at`: timestamptz, not null, default `now()`
  - *Check*: `ck_memory_chunks_scope_parent`. A `card` row needs `card_id`. An `agent` row needs `agent_config_id`.
  - *Indexes*: `ix_memory_chunks_embedding_hnsw` on `(embedding)`, method `hnsw`, operator class `vector_cosine_ops`, `m = 16`, `ef_construction = 64`. `ix_memory_chunks_user_scope_excluded` on `(user_id, scope, excluded)`.
  - *Correction*: the code declares a wider vector and no `embedding_dim` column. Both are wrong for the deployment model.

### 5.7 System & Queue (`src/schemas/system.py`)

- **`jobs`**
  - `id`: uuid, primary key, app default `uuid4`
  - `type`: varchar(64), not null, indexed
  - `payload`: jsonb, not null, default `'{}'::jsonb`
  - `run_at`: timestamptz, not null, default `now()`, indexed
  - `attempts`: integer, not null, default `0`
  - `max_attempts`: integer, not null, default `5`
  - `status`: varchar(32), not null, default `'pending'`, indexed
  - `locked_by`: varchar(128), null, indexed
  - `locked_at`: timestamptz, null, indexed
  - `last_error`: text, null
  - `created_at`: timestamptz, not null, default `now()`
  - `updated_at`: timestamptz, not null, default `now()`, on update `now()`
  - `priority`: integer, not null, default `0`. A higher value runs first.
  - `heartbeat_at`: timestamptz, null. The worker extends it every 30 seconds.
  - *Claim index*: `ix_jobs_claim_pending` on `(priority DESC, run_at ASC)` with predicate `WHERE status = 'pending'`
  - *Reaper index*: `ix_jobs_stale_heartbeat` on `(status, heartbeat_at)`
  - *Reaper rule*: reclaim a `locked` job when `heartbeat_at` is older than 90 seconds. The code reads `locked_at` at 300 seconds. That rule kills a slow but healthy job.
- **`idempotency_records`**
  - `key`: varchar(256), primary key
  - `status`: varchar(32), not null, indexed
  - `response_code`: integer, null
  - `response_headers`: jsonb, null
  - `response_body`: bytea, null
  - `created_at`: timestamptz, not null, default `now()`
  - `expires_at`: timestamptz, not null, indexed

### 5.8 Corrections applied to the code

Seven rules override the code. The code is wrong in each case.

1. **Embeddings are 384 dimensions.** The model is bge-small-en-v1.5 with int8 ONNX. The column is `vector(384)`. The code declares a wider vector. Add `embedding_dim` so a model change cannot mix vector spaces. Add `card_id` and `agent_config_id` so the `card` and `agent` scopes have a parent row.
2. **Money is an integer.** Replace the float money column on `runs` with `runs.cost_micros`, a bigint of micro-USD. Apply the same rule to `agent_configs.limits`. The key is `max_cost_micros`.
3. **The queue orders by priority.** Add `jobs.priority` as an integer. A higher value runs first. Add `jobs.heartbeat_at` as a timestamptz. The claim index becomes `(priority DESC, run_at ASC) WHERE status = 'pending'`. The reaper uses `heartbeat_at` at 90 seconds. It does not use `locked_at` at 5 minutes.
4. **A run owns a snapshot.** Add `runs.workspace_ref` as a nullable text path. Add the `run_workspaces` table. It holds the archive path, the size, and the checksum.
5. **The chunking job needs a marker.** Add `messages.indexed_at` as a nullable timestamptz. The job reads only rows where the column is null. This step stops double indexing.
6. **A connector tool carries an approval level.** Add the `connector_tools` table. Drop `connected_accounts.enabled_tools`. A tool name alone carries no level.
7. **A re-triage needs a second row.** Use `UNIQUE(item_id, model_version)` on `triage_decisions`. The code declares `UNIQUE(item_id)`. That rule rejects a new verdict for a new model version.

### 5.9 Tables still missing

Three tables are missing. They are not part of the 18.

- **`agent_configs`**: `memory_chunks.agent_config_id` and `runs.config_id` point at this table. The code has no such table. `runs.config_id` has no foreign key. Phase 4 must add the table and both foreign keys. The `limits` jsonb holds `max_cost_micros`, not a float.
- **`skills`**: the skills service keeps its state in a class variable. A restart loses every installed skill. Phase 6 must add the table.
- **`audit_log`**: no table records approval decisions, connector changes, or settings changes.

Two constraints are also missing.

- **`approvals`**: add `UNIQUE(run_id, tool_call_id)` so a retry cannot create a second approval row.
- **`card_actions`**: add `UNIQUE(card_id, action_key)` so a card cannot repeat an action.

---

## 6. Deep-Dive: Core Subsystem 1 — The Stateless 6-Stage Event Loop & Multi-Tool Barrier Synchronizer

### Detailed Loop Execution Lifecycle

```mermaid
sequenceDiagram
    autonumber
    participant W as Worker Process
    participant DB as Postgres Datastore
    participant LLM as Tier 3 Reasoning LLM
    participant SB as Docker Sandbox Runner
    participant MCP as Gmail / Connectors MCP
    participant SSE as FastAPI SSE Gateway
    participant Mobile as Flutter App (User)

    W->>DB: Atomic Claim Job (SELECT FOR UPDATE SKIP LOCKED)
    DB-->>W: Return Run ID & Configuration
    loop Iteration N (Limit: 20-40)
        W->>DB: Stage 1: Assemble Context (Prompt + Memory + Recent Turns)
        W->>W: Stage 2: Compaction Check (If Tokens > 70%, trigger Two-Tier Compaction)
        W->>LLM: Stage 3: Inference (Prompt + Context + Tool Schemas)
        LLM-->>W: Response (Assistant Turn with Parallel Tool Calls)
        W->>DB: Persist Assistant Message & Tool Calls
        W->>DB: Record Step in run_steps
        DB-->>SSE: Stream Event (Thought / Tool Intent)
        SSE-->>Mobile: Live UI Update
        
        alt Model Returns Text Only
            W->>DB: Update Run (status='completed')
            W->>DB: Emit run_completed Step
            Note over W: Run Finished
        else Model Emits Parallel Tool Calls [C1(Local), C2(Async), C3(External)]
            Note over W: Stage 4 & 5: Partition & Dispatch
            W->>SB: Execute C1 (read_file)
            SB-->>W: C1 Result
            W->>DB: Insert Message (role='tool', id='C1', content=Result)
            
            W->>DB: Enqueue C2 (compile_artifact Job in jobs table)
            W->>DB: Insert Message (role='tool', id='C2', content='{"status":"enqueued"}')
            
            W->>DB: Insert Approval for C3 (gmail_send, status='pending')
            W->>DB: Update Run (status='awaiting_approval')
            W->>SB: Snapshot /workspace to Host Volume
            W->>DB: Emit approval_required Step
            DB-->>SSE: Stream Approval Prompt
            SSE-->>Mobile: Render Action Prompt (Approve / Reject)
            Note over W: WORKER DEALLOCATES & EXITS (Zero Memory Held)
        end
    end

    Note over Mobile,DB: Hours Later: User Interacts
    Mobile->>SSE: POST /api/v1/approvals/{id}/decision (Approve)
    SSE->>DB: Update Approval (status='approved')
    SSE->>MCP: Execute External Tool (gmail_send)
    MCP-->>SSE: Gmail Sent Confirmation
    SSE->>DB: Insert Message (role='tool', id='C3', content=SentConfirmation)
    SSE->>DB: Barrier Check: Verify all tool_call_ids for assistant turn exist in messages
    SSE->>DB: Enqueue resume_run Job into jobs table
    
    W->>DB: Worker Claims resume_run via SKIP LOCKED
    W->>SB: Re-hydrate Container from /workspace Snapshot
    Note over W: Loop Continues to Iteration N+1
```

### The Barrier Synchronizer Invariant
The Barrier Synchronizer enforces the OpenAI tool calling contract without in-memory state:
$$\forall c \in \text{AssistantMessage.tool\_calls}: \ \exists m \in \text{Messages} \text{ such that } m.\text{role} = \text{"tool"} \land m.\text{tool\_call\_id} = c.\text{id}$$

1. If an external tool is approved: The executor runs the tool and writes the resulting output to `messages` with `tool_call_id=c.id`.
2. If an external tool is rejected: The executor generates a structured tool error payload:
   ```json
   {
     "error": "USER_REJECTED",
     "message": "The user rejected this proposed action.",
     "decision_note": "User opted not to send this email."
   }
   ```
   and writes it to `messages` with `tool_call_id=c.id`.
3. The model receives a complete history where every ID is satisfied, allowing it to adapt its reasoning cleanly.

---

## 7. Deep-Dive: Core Subsystem 2 — Long-Term Memory & Exponential Time-Decay Retrieval (`pgvector`)

### 7.1 Chunking & Extraction Write Path (Batched & Fully Asynchronous)

To maintain sub-second response times, **memory indexing NEVER runs synchronously on the user request path**. Embedding models introduce noticeable latency (200ms–1500ms) and API overhead; running an embedding call on every single turn would degrade chat performance and waste API tokens.

Instead, memory is written and embedded through an asynchronous, batched background pipeline:

1. **Strictly Excluded: Zero Embedding of Tool Calls**:
   - **Tool calls and raw tool outputs are NEVER embedded into memory.**
   - Tool arguments, shell logs, JSON schemas, file diffs, and raw tool responses pollute the semantic vector space with low-signal syntax.
   - All tool calls, arguments, and outputs stay preserved in the `messages` table and `artifacts` store for inspection, replay, and compaction.
   - **Only human user messages and assistant natural-language messages** are processed for long-term memory chunking.

2. **Batched Indexing Triggers (5–10 Messages)**:
   - Indexing is batched rather than per-message.
   - An asynchronous `index_memory` background job is registered in the Postgres `jobs` table when:
     a) **Message Threshold**: A session accumulates **5 to 10 un-indexed conversational messages**.
     b) **Run Completion**: A multi-step agent run finishes (`status="completed"`).
     c) **Session Idle Timeout**: A chat conversation has been idle for $> 15$ minutes with at least 2 un-indexed messages.
   - This ensures the user's interactive response is never delayed while embeddings are generated and persisted.

3. **Window Segmentation & Formatting**:
   - The worker claims the `index_memory` job and loads un-indexed user and assistant turns.
   - Messages are segmented into overlapping sliding windows of 4–6 conversational turns (2–3 user-assistant exchanges) with an overlap of 1 exchange.
   - The chunk text is cleanly formatted with actor roles:
     ```text
     [User]: Nas
     [Topic]: Q3 Financial Report
     Please draft the executive summary focusing on cloud spending reduction.
     [Assistant]:
     I have generated the summary highlighting the 14% drop in AWS costs...
     ```

4. **Embedding Generation & Vector Storage**:
   - The batch of text chunks is embedded by a local model: `bge-small-en-v1.5`, int8 ONNX, 384 dimensions. No provider call, no per-token cost.
   - The worker loads the model on demand and unloads it after 5 idle minutes. Peak process memory is about 270 MB during a batch, and about 90 MB when idle.
   - The artifact is pinned by revision and checksum, and verified at startup.
   - BGE requires CLS pooling and a query-only instruction prefix. Mean pooling degrades quality with no error.
   - A chunk is capped at 512 tokens, which is the model limit.
   - Chunks are inserted into `memory_chunks` with `user_id`, `scope` (`global`, `card`, `agent`), source message UUID references, and the pgvector embedding vector.
   - The indexed messages are marked with an `indexed_at` timestamp to prevent duplicate chunking.

### 7.2 The Exponential Time-Decay Scoring Formula
Standard cosine similarity fails for assistants because an email discussion from 9 months ago about "travel plans" will outscore a casual remark from yesterday unless time is factored in. Conversely, hard time cutoffs lose critical facts.

Virgil applies an exponential half-life decay function with a non-zero floor and a semantic override gate:

$$\text{similarity} = 1 - (\vec{e}_{\text{chunk}} \Leftrightarrow \vec{q})$$

$$\text{age\_days} = \frac{\text{now}() - \text{created\_at}}{86400}$$

$$\text{decay} = 0.5^{\left(\frac{\text{age\_days}}{\text{half\_life\_days}}\right)} \quad (\text{Default: } \text{half\_life\_days} = 30.0)$$

$$\text{recency\_multiplier} = \text{floor} + (1.0 - \text{floor}) \times \text{decay} \quad (\text{Default: } \text{floor} = 0.35)$$

$$\text{final\_score} = \begin{cases} 
\text{similarity} & \text{if } \text{similarity} \ge \text{override\_threshold } (0.65) \\
\text{similarity} \times \text{recency\_multiplier} & \text{otherwise}
\end{cases}$$

### Mathematical Properties:
- **Halving Property**: Every 30 days, the recency bonus drops by 50%.
- **Non-Zero Floor ($0.35$)**: Even an item from 2 years ago retains at least 35% of its semantic weight. Important past events never disappear completely.
- **Calibration**: 0.85 was the first draft value. Measured on bge-small-en-v1.5, correct answers score 0.679 to 0.801 and the best wrong answers score 0.561 to 0.615. A threshold of 0.85 never fires. Start at 0.65 and recalibrate on real data. Set the value per model, because each model has its own score range.

### 7.3 Retrieval Path: Exact Scan or Tenant Graph

The candidate query takes one of two paths. The path depends on the number of rows that the user holds in `memory_chunks`. `MEMORY_RETRIEVAL_EXACT_SCAN_MAX_ROWS` sets the crossover.

| Tenant rows | Path name | What answers the query |
| --- | --- | --- |
| At or below the crossover | `exact` | The tenant btree index reduces the heap. The database sorts the tenant rows by cosine distance. The top 50 rows are exact. |
| Above the crossover, with a tenant graph | `hnsw` | The graph of that tenant walks by cosine distance. The partial index holds the rows of one tenant, so the walk spends no budget on other tenants. |
| Above the crossover, without a tenant graph | `exact` | The query falls back to the exact scan. It is correct and slower. |

The exact scan is the default path at low row counts. The vector index is not the default path there. The benchmark gives the cause. With 100,000 rows over 100 users, one tenant is 1 percent of the heap. At `ef_search` 200, the global graph reaches recall@50 of 0.56. The exact scan reaches 0.99998 on the same rows and the same queries.

`hnsw.iterative_scan` does not repair the global graph. The scan mode turns 2 returned rows into 50 rows. The recall stays at 0.56. One global graph holds all tenants, so a walk spends its budget on rows that the filter rejects. The setting cannot make the graph meet the floor on a filtered query.

The measured crossover is 20,000 tenant rows. At 5,000 and 10,000 rows the exact scan is faster, and at 20,000 and 50,000 rows the tenant graph is faster. Section 3.5 of `docs/BENCHMARKS.md` gives the table and the host load.

A tenant graph is a partial HNSW index for one tenant. It is built with `m = 32` and `ef_construction = 200`. On 50,000 rows it reaches recall@50 of 0.9796 at `ef_search` 1000. The global graph reaches 0.8483 on the same rows at the same width. The measured width of a tenant graph is 1000, which is the maximum that pgvector accepts.

A tenant above the crossover gets the graph after the maintenance step builds it. `create_tenant_graph_index` builds the index and retires the global graph, because the global graph stays a valid plan for the tenant query and it then misses the floor. The build blocks writes for its duration, so run it at maintenance time. Until the graph exists, the query uses the exact scan and stays correct.

The query keeps its inputs and its response fields. It also reports the path name, `exact` or `hnsw`, for each query.

### 7.4 Executable SQL Query

The path sets the session before the query runs. The exact path runs these statements first:

```sql
SET LOCAL enable_indexscan = off;
SET LOCAL enable_indexonlyscan = off;
SET LOCAL enable_sort = on;
```

The graph path runs these statements first:

```sql
SET LOCAL hnsw.ef_search = 1000;
-- hnsw.iterative_scan returns 2 to 50 rows instead of 2. It cannot raise the
-- recall of a filtered query, because one global graph holds every tenant.
-- The tenant partial index answers this query instead.
SET LOCAL hnsw.iterative_scan = 'relaxed_order';
SET LOCAL enable_indexscan = on;
SET LOCAL enable_indexonlyscan = on;
SET LOCAL enable_sort = off;
```

The width comes from `settings.py`. 1000 is the measured width of a tenant graph. The refused sort keeps the btree and a sort from answering the query instead of the tenant graph. The scan mode changes the row count and not the recall, as Section 7.3 states.

A tenant graph is built by this maintenance statement:

```sql
CREATE INDEX ix_memory_chunks_hnsw_<tenant hex> ON memory_chunks
USING hnsw (embedding vector_cosine_ops)
WITH (m = 32, ef_construction = 200)
WHERE user_id = '<tenant uuid>';

DROP INDEX IF EXISTS ix_memory_chunks_embedding_hnsw;
```

The second statement retires the global graph. After it runs, each tenant query has one usable graph.

The candidate query is the same on both paths. The statements above decide which access method answers it. On the exact path the order is exact. On the graph path the order comes from the graph walk.

```sql
WITH vector_candidates AS (
    SELECT 
        id,
        content,
        summary,
        created_at,
        scope,
        1 - (embedding <=> :query_embedding) AS similarity,
        EXTRACT(EPOCH FROM (NOW() - created_at)) / 86400.0 AS age_days
    FROM memory_chunks
    WHERE user_id = :user_id
      AND scope = ANY(:scopes)
      AND excluded = FALSE
    ORDER BY embedding <=> :query_embedding ASC
    LIMIT :candidate_k -- e.g. 50
)
SELECT 
    id,
    content,
    similarity,
    age_days,
    CASE 
        WHEN similarity >= :override_threshold THEN similarity
        ELSE similarity * (:floor + (1.0 - :floor) * POW(0.5, age_days / :half_life_days))
    END AS final_score
FROM vector_candidates
ORDER BY final_score DESC
LIMIT :top_n; -- e.g. 8
```

---

## 8. Deep-Dive: Core Subsystem 3 — Postgres-Backed Job Queue (`SKIP LOCKED`) & Worker Engine

### 8.1 Zero-Contention Job Claiming
Virgil runs multiple concurrent workers against a single Postgres instance using row-level locking:

```sql
WITH claimable AS (
    SELECT id FROM jobs
    WHERE status = 'pending'
      AND run_at <= NOW()
    ORDER BY priority DESC,
             (run_at + LEAST(3600, EXTRACT(EPOCH FROM (NOW() - run_at))) * INTERVAL '1 second') ASC
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
UPDATE jobs
SET status = 'locked',
    locked_by = :worker_id,
    locked_at = NOW(),
    heartbeat_at = NOW(),
    attempts = attempts + 1
FROM claimable
WHERE jobs.id = claimable.id
RETURNING jobs.*;
```

### 8.2 Crash Resilience & Worker Heartbeats
- **Heartbeat & Reaper**: The worker extends `heartbeat_at` every 30 seconds while a job runs. A separate loop reaps on `heartbeat_at`, not on `locked_at`:
  ```sql
  UPDATE jobs
  SET status = 'pending', locked_by = NULL, locked_at = NULL, heartbeat_at = NULL
  WHERE status = 'locked'
    AND COALESCE(heartbeat_at, locked_at) < NOW() - INTERVAL '90 seconds';
  ```
  The limit is 3 times the heartbeat interval. A container that dies without a clean shutdown recovers in 90 seconds.
  Reaping on a fixed `locked_at` steals any job that runs longer than 5 minutes. Two workers then run the same job, and two sandboxes write to one workspace.
- **Exponential Retry with Jitter**:
  When a job raises an unhandled exception:
  $$\text{delay\_seconds} = \min(3600, \ 2^{\text{attempts}} \times 5) \pm \text{jitter}$$
  ```sql
  UPDATE jobs
  SET status = CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'pending' END,
      run_at = NOW() + (:delay * INTERVAL '1 second'),
      last_error = :error_traceback,
      locked_by = NULL,
      locked_at = NULL
  WHERE id = :job_id;
  ```

---

## 9. Deep-Dive: Core Subsystem 4 — Two-Tier Context Compaction (Jev Filter + Fast LLM Summarizer)

Long-running agent workflows generate massive token bloat (terminal outputs, lint errors, intermediate drafts). Passing this raw text to expensive reasoning models wastes money and degrades prompt attention.

```mermaid
flowchart TD
    Trigger([Context Exceeds 70% of Model Window]) --> Split[Segment History into Units: User, Assistant, Tool Result]
    Split --> T0[Tier 0: Deterministic Safety Locks]
    T0 --> T0_Keep[Lock & Retain:\n- All User Messages\n- Last 6 Conversation Turns\n- All Approvals & User Decisions\n- Final Result of Tool Chains]
    
    Split --> T1[Tier 1: Jev Decision Probability Gate\nBatch Evaluate Remaining Units]
    T1 --> Q1[Q: Needed for current task?]
    T1 --> Q2[Q: Contains user-supplied fact?]
    T1 --> Q3[Q: Is raw bulk/log output?]
    
    Q1 & Q2 & Q3 --> ScoreLogic{Evaluate Probabilities}
    ScoreLogic -- "p(needed) >= 0.7 OR p(user_fact) >= 0.7" --> RetainFull[Retain Unit in Full]
    ScoreLogic -- "p(bulk) >= 0.6" --> ConvertStub[Convert to Artifact Stub\nTitle + 200 char preview + Pointer ID]
    ScoreLogic -- "p(needed) < 0.3" --> DropUnit[Drop from Active Context]
    
    ConvertStub & DropUnit --> T2[Tier 2: Fast LLM Summarizer\nSend Dropped & Stubbed Content to Fast Model]
    T2 --> GenerateSummary[Generate Concise Executive Summary Block]
    
    RetainFull & T0_Keep & GenerateSummary --> Assemble[Assemble Compact Context:\nSystem Prompt + Summary Block + Kept Units & Stubs + Recent Turns]
    Assemble --> End([Resume Event Loop Iteration N])
```

### Artifact Stubbing Format
Bulk data is replaced with a standard plain-text descriptor:
```text
[Truncated Artifact #e4b2a1: tool=bash, command="pytest tests/", size=45KB, exit_code=0]
Preview: ========================= 42 passed in 2.14s =========================
Full log stored in artifacts. Use deliver_file or read_file if detailed inspection is required.
```

---

## 10. Deep-Dive: Core Subsystem 5 — Two-Stage Proactive Triage Pipeline

To achieve near-zero running cost during high-volume background polling, Virgil splits triage into a two-stage filter:

```mermaid
flowchart LR
    Poll[Ingested Message] --> Dedupe[Deduplicate via Unique Index]
    Dedupe --> S1[Stage 1: Jev Decision Classifier\nZero Generative Cost]
    
    S1 --> Gate{p(action_required)}
    Gate -- "< 0.3 AND bulk" --> Discard[Archive / Discard\nZero Token Spend]
    Gate -- "0.3 <= p < 0.8" --> Review[Low-Priority Card:\n'May need a look']
    Gate -- ">= 0.8" --> S2[Stage 2: Fast LLM Extractor\nLightweight gpt-4o-mini / Haiku]
    
    S2 --> Extract[Extract:\n- due_at ISO timestamp\n- Clean 1-sentence title\n- Executive summary\n- Action button presets]
    Extract --> SaveCard[Upsert Action Card in DB]
    SaveCard --> Push[Dispatch Push via ntfy/SSE]
```

### Stage 1: Batched Jev Request Format
```json
{
  "model": "jev-classifier-v1",
  "state": {
    "from": "billing@cloudprovider.com",
    "subject": "Invoice #8921 Overdue - Service Suspension in 48 Hours",
    "body": "Your payment failed. Please update payment method before Nov 2 to avoid cancellation.",
    "received_at": "2026-10-31T14:00:00Z",
    "user_replied_before": false,
    "sender_known": true
  },
  "questions": {
    "action_required": "yes or no probability",
    "urgency": "one of: none, low, medium, high, critical",
    "category": "one of: inquiry, billing, deadline, subscription, meeting, security, newsletter, notification, personal, other",
    "has_deadline": "yes or no probability",
    "is_bulk": "yes or no probability",
    "is_security_sensitive": "yes or no probability"
  }
}
```

---

## 11. Deep-Dive: Core Subsystem 6 — Docker Sandbox Runner & Volume Lifecycle

1. **Security Isolation**:
   - Runs as unprivileged UID `1000`.
   - Read-only root filesystem (`--read-only`), writable `/tmp` (tmpfs) and `/workspace`.
   - All Linux capabilities dropped (`--cap-drop=ALL`).
   - Default seccomp profile applied. The profile hash is written to `audit_log` on each run.
   - Network disabled by default (`--network=none`). Configurable domain allowlist via HTTP proxy sidecar.
   - Resource caps: `--memory 1g --memory-swap 1g --cpus 1.0 --pids-limit 256`.
   - `--security-opt no-new-privileges` and a `noexec` tmpfs at `/tmp`.
   - Workspace quota of 2 GB per run. Archives older than 7 days are deleted.
2. **Docker Socket Protection**:
   The worker never mounts `/var/run/docker.sock` directly, and it does not use a socket proxy either.
   A route-filtering proxy still forwards the full container payload. The agent can express `Privileged: true`, a bind mount of `/`, or `PidMode: host` inside an allowed `POST /containers/create`. That converts a sandbox escape into host root.
   The worker calls a small runner service with a fixed request schema instead:

   ```python
   class SandboxSpec(BaseModel):
       image: str                  # must match the image allowlist
       command: list[str]          # argv, never a shell string
       timeout_seconds: int = 300
       memory_mb: int = 1024
       cpu_quota: float = 1.0
       network: Literal["none"] = "none"
   ```

   The runner builds the Docker config on the server side. The caller cannot express a bind mount, a capability, or a host namespace.
3. **Workspace Snapshot on Pause**:
   When a run transitions to `awaiting_approval`:
   - The container tar-archives `/workspace` to a host volume directory: `/var/vigil/workspaces/{run_id}.tar.gz`.
   - The container is stopped to free CPU and memory.
   - Upon resumption, a clean container starts and extracts the archive back to `/workspace`.

---

## 12. Deep-Dive: Core Subsystem 7 — Connectors Engine & Standalone Gmail MCP Connector

To keep the core backend lean, external services are encapsulated into **Model Context Protocol (MCP)** servers:

```text
connectors/gmail/
├── pyproject.toml
├── src/
│   ├── main.py              # Stdio and SSE MCP server entrypoint
│   ├── auth.py              # Google OAuth2 client, token refresh & PKCE
│   ├── sync.py              # Gmail API historyId cursor sync + 7-day fallback bounded sync
│   └── tools.py             # MCP Tool declarations:
│                            # - gmail_search(query, max_results)
│                            # - gmail_read(message_id)
│                            # - gmail_create_draft(to, subject, body_html)
│                            # - gmail_send(to, subject, body_html)
```

### Backend MCP Client Adapter (`src/integrations/mcp/mcp.py`)
- Discovers tools dynamically via `mcp.list_tools()`.
- Maps tools to approval policies:
  - `gmail_search`, `gmail_read` $\rightarrow$ Level `read` (Auto-approved).
  - `gmail_create_draft`, `gmail_send` $\rightarrow$ Level `external` (Gated approval required).
- Communicates with `connectors/gmail/` over stdio inside a dedicated container or via HTTP/SSE.

---

## 13. Exhaustive Phase-by-Phase Implementation Roadmap

```mermaid
flowchart LR
    P1[Phase 1: Foundations & Architecture] --> P2[Phase 2: Database Schemas & Job Queue]
    P2 --> P3[Phase 3: Connectors Engine & Triage]
    P3 --> P4[Phase 4: Sandbox & Agent Event Loop]
    P4 --> P5[Phase 5: Memory & Compaction]
    P5 --> P6[Phase 6: Skills & MCP Expansion]
    P6 --> P7[Phase 7: Mobile & Portfolio Demo]
```

### Phase 1: Foundations & Architecture Scaffold
- **Objective**: Establish the monorepo, environment configuration, dependency injection hierarchy, database connectivity, and base module skeletons.
- **Detailed Tasks**:
  1. Initialize `backend/pyproject.toml` with `uv` (`fastapi`, `uvicorn`, `pydantic-settings`, `sqlalchemy[asyncio]`, `asyncpg`, `pgvector`, `alembic`, `httpx`, `structlog`, `cryptography`, `pytest`, `pytest-asyncio`).
  2. Implement `src/config/`:
     - `settings.py`: Pydantic settings loading database URLs, model keys, ports, secrets.
     - `exceptions.py`: `AppException` hierarchy (`NotFoundError`, `UnauthorizedError`, `ForbiddenError`, `ConflictError`, `ValidationError`).
     - `logging.py`: Structured JSON logging with `request_id` context propagation.
     - `security.py`: Token hashing (`hash_token`) and credential encryption (`encrypt_secret`, `decrypt_secret`).
     - `router.py`: `create_api_router(settings)` calling each module's router builder.
  3. Implement `src/middlewares/`:
     - `request_id.py`: Generates or passes through `X-Request-ID`.
     - `timing.py`: Records latency in `X-Process-Time`.
     - `idempotency.py`: Validates `Idempotency-Key` headers on POST endpoints.
  4. Scaffold all modules with `repository.py` (Protocols only), `service.py`, `router.py` (factory pattern), `dependencies.py`, and `data.py`.
  5. Implement working `health` probe (`GET /api/v1/health`) and `auth` pairing module (`POST /api/v1/auth/pair`) with first-boot ASCII QR / pairing code generation in server logs.
  6. Create `deploy/docker-compose.yml` (`postgres` with `pgvector/pgvector:pg16`, `api`, `worker`).
- **Exit Criteria**: `uv run pytest` passes 100%; server boots and pairing endpoint generates a device token that survives a restart.
- **Durability requirement**: The device, user, and pairing records must live in PostgreSQL. A class level dictionary in the service loses every token on deploy and breaks a second API replica.
- **Security requirement**: No hardcoded or prefix-matched token is accepted outside `APP_ENV=test`. No production boot with the default `SECRET_KEY`.

### Phase 2: Centralized Database Schemas, Migrations & Postgres Job Queue
- **Objective**: Deploy the full relational and vector database schema via Alembic and build the atomic `SKIP LOCKED` job queue.
- **Detailed Tasks**:
  1. Author centralized SQLAlchemy 2.0 models across `src/schemas/`:
     - `identity.py`, `connectors.py`, `triage.py`, `runtime.py`, `memory.py`, `system.py`.
  2. Create Alembic migration:
     - Enable extension `vector`.
     - Create all 21 tables, foreign keys, unique constraints, and indexes (including the HNSW vector index at 384 dimensions).
  3. Implement `src/modules/jobs/`:
     - `service.py`: `enqueue_job`, `claim_next_job` using `SELECT ... FOR UPDATE SKIP LOCKED`, `complete_job`, `fail_job`.
     - `reaper`: Periodic recovery of stale locked jobs ($> 5$ minutes).
     - Worker loop in `src/worker.py`.
- **Exit Criteria**: Migrations apply cleanly; concurrent worker processes claim and complete jobs with zero deadlocks or duplicate executions.

### Phase 3: Connectors Engine, Gmail MCP Connector & Action Cards
- **Objective**: Build the standalone Gmail MCP server, connect it to the backend connectors engine, run two-stage Jev triage, and populate the mobile card feed.
- **Detailed Tasks**:
  1. Build `connectors/gmail/`:
     - OAuth2 flow with Google APIs, token refresh, and encryption.
     - Polling engine using Gmail `historyId` delta sync + 7-day bounded fallback.
     - Tool declarations (`gmail_search`, `gmail_read`, `gmail_create_draft`, `gmail_send`).
  2. Implement `src/integrations/mcp/mcp.py` & `src/modules/connectors/`:
     - Connector registry, account management, sync state persistence.
  3. Implement `src/integrations/jev/jev.py` & `src/modules/triage/`:
     - Stage 1 Jev decision gate calling TypeSafe AI API.
     - Stage 2 Fast LLM extractor generating clean titles, ISO `due_at` timestamps, summaries, and action button templates.
  4. Implement `src/modules/cards/`:
     - Card feed endpoints (`GET /cards`), card detail (`GET /cards/{id}`), feedback endpoint (`POST /cards/{id}/feedback`).
     - Thread de-duplication: new incoming messages update existing cards.
  5. Implement `src/integrations/notifications/ntfy.py`:
     - Dispatch push alerts to mobile devices for high-urgency cards.
- **Exit Criteria**: Polled emails trigger triage, generate cards in DB, and fire notifications with zero missed or duplicate messages.

### Phase 4: Docker Sandbox Runner, Core Tool Harness & Stateless Agent Runtime
- **Objective**: Build the isolated sandbox runner, core tools, and the 6-stage agent event loop with multi-tool barrier synchronization.
- **Detailed Tasks**:
  1. Implement `src/integrations/sandbox/sandbox.py`:
     - Docker container creation via socket proxy.
     - Execution of shell commands and file tools with timeout and output caps.
     - Tar-snapshot and re-hydration of `/workspace` volumes.
  2. Implement Core Tools:
     - `read`, `write`, `edit`, `bash`, `deliver_file`, `ask_user`, `manage_tasks`, `generate_document`.
  3. Implement `src/modules/runs/`:
     - 6-stage loop execution state machine.
     - Partitioning of parallel tool calls (immediate execution vs async jobs vs gated approvals).
     - Multi-tool Barrier Synchronizer.
     - Resumable SSE streaming endpoint (`GET /runs/{id}/events`).
  4. Implement `src/modules/approvals/`:
     - Approval queue (`GET /approvals`), decision endpoint (`POST /approvals/{id}/decision`).
- **Exit Criteria**: Agent runs execute tools in the sandbox, pause without holding worker memory on external tools, and resume cleanly upon user approval.

### Phase 5: Weighted `pgvector` Memory & Two-Tier Context Compaction
- **Objective**: Implement long-term conversation indexing, exponential time-decay retrieval, and Jev-powered context compaction.
- **Detailed Tasks**:
  1. Implement `src/modules/memory/`:
     - Background chunking of completed runs and idle chat sessions.
     - Embedding generation and storage in `memory_chunks`.
     - Two-stage time-decay retrieval query ($K=50$ candidates, half-life 30 days, floor 0.35, override 0.65).
     - Memory search and deletion endpoints (`GET /memory/search`, `DELETE /memory/chunks/{id}`).
  2. Implement `src/modules/compaction/`:
     - Context size monitor ($> 70\%$ limit).
     - Tier 0 deterministic safety lock.
     - Tier 1 Jev decision batch scoring.
     - Artifact stub replacement.
     - Tier 2 Fast LLM running summary generation.
- **Exit Criteria**: Retrieval tests verify that both recent facts and old high-similarity facts rank in top-8; compaction cuts prompt tokens while maintaining task completion accuracy.

### Phase 6: Skills System, External MCP Servers & Sub-Agent Readiness
- **Objective**: Enable custom user skills, third-party MCP server attachment, and sub-agent execution isolation.
- **Detailed Tasks**:
  1. Implement `src/modules/skills/`:
     - YAML-frontmatter parser for Markdown skill definitions.
     - Git repository skill installer with commit SHA pinning.
     - `skill_load` tool copying skill scripts into the sandbox workspace.
  2. Expand `src/modules/connectors/`:
     - Allow user registration of external stdio and SSE MCP servers.
     - Dynamically wrap discovered tools into the runtime approval policy.
  3. Implement Sub-Agent Readiness:
     - Verify `parent_run_id` and `depth` constraints.
     - Implement child workspace scoping and aggregate cost budget enforcement.
- **Exit Criteria**: A custom skill installed from Git or a custom MCP server attaches tools to the agent loop without code changes.

### Phase 7: Companion Mobile App & Portfolio Hybrid Replay Demo
- **Objective**: Deliver the client-facing Flutter mobile application and portfolio web demo.
- **Detailed Tasks**:
  1. Mobile App (`mobile/`):
     - QR code and pairing code onboarding.
     - Action Card feed with urgency badges and action triggers.
     - Live Run View with real-time SSE step streaming and interactive approval prompts.
     - Chat interface and downloadable artifact viewer.
  2. Portfolio Demo (`web/`):
     - Pre-recorded, deterministic SSE branching trajectories.
     - Interactive simulation of Jev probability gates and user approvals at zero runtime compute cost.
- **Exit Criteria**: Complete end-to-end user journey: mobile app pairs with server, receives email triage cards, triggers runs, approves actions, and downloads compiled documents.

---

## Appendix A. Corrections applied after the Draft 0.1 review

This revision corrects defects found in a review of the first draft. Two companion documents hold the full argument and the evidence.

- `docs/SPEC-REVIEW.md` covers the architecture, data model, queue, security, and retrieval defects, with the acceptance test for each fix.
- `docs/PHASE1-REVIEW.md` covers the Phase 1 scaffold as built, and the authentication bypass found in it.

| Correction | Section |
|---|---|
| Table count 16 in code to 21 required. 18 exist after the corrections | 5, 5.8, 5.9 |
| Explicit `__tablename__` on every model | 5.1 |
| `cost_usd: float` to `cost_micros: bigint` | 5.5 |
| `Vector(1536)` to `Vector(384)` and the local embedding model | 5.6, 7.1 |
| `messages.indexed_at` added | 5.5 |
| `runs.workspace_ref` added | 5.5 |
| `triage_results` unique on `(item_id, model_version)` | 5.4 |
| `jobs.priority` and `jobs.heartbeat_at` added | 5.7 |
| Claim query orders by priority with aging | 8.1 |
| Reaper runs on `heartbeat_at` at 90 seconds | 8.2 |
| Decay override threshold 0.85 to 0.65, measured | 7.2 |
| Socket proxy replaced by a fixed-schema runner | 11.2 |
| Sandbox resource caps and workspace quota | 11.1 |
| Phase 1 durability and security requirements | 13 |
| Four tables that were missing entirely | 5.8 |
