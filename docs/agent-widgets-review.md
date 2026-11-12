# Agent-Created Widgets Review

## 1. Question

Can run agents create persistent dashboard widgets (panels) today, and what is the smallest safe path to add that ability on the current stack.

Stack verified in this session: `PROJECT.md:8` is React + Vite frontend, `PROJECT.md:20` is FastAPI + AsyncIO backend. Validation idiom in repo is Pydantic v2 (`backend/src/modules/artifacts/data.py:8`, `backend/src/modules/cards/data.py:8`) and TypeScript + Zod style contracts on web.

## 2. Current State

| Stage | Verified state | Evidence |
|---|---|---|
| Left rail panels | Hardcoded to 4 ids, local only, no agent path | `web/src/components/hark/HarkLeftRail.tsx:24` defines `PanelId = today \| usage \| agenda \| waiting`. `HarkLeftRail.tsx:26` stores choice in `localStorage` key `vigil.rail.panels`. `HarkLeftRail.tsx:403` button `Create a new panel` only opens a toggle menu (`HarkLeftRail.tsx:415`), it does not create a record. |
| Home rail widgets | Local pinned micro-apps only, no backend row | `web/src/components/home/WidgetsRail.tsx:53` reads `usePinnedWidgets()`. `WidgetsRail.tsx:61` renders `Pinned Micro-Apps`. Pin store is `localStorage` key `vigil_pinned_widgets` in `web/src/components/chat/ArtifactCard.tsx:27`, with cross tab sync via `vigil:pinned-widgets-changed` at `ArtifactCard.tsx:50`. |
| Micro-app sandbox primitive | Exists, closest reuse candidate | `WidgetsRail.tsx:92` renders `iframe sandbox="allow-scripts" srcDoc={widget.content}`. Backend artifact kinds are fixed at `backend/src/modules/artifacts/data.py:11` as `document \| diff \| log \| export \| micro_app \| screenshot`. `ArtifactCreateRequest` in `artifacts/data.py:8` requires `run_id, kind, path, size_bytes`, so it is file backed, not JSON payload backed. |
| Cards pipeline | Exists for feed, not for widget layout | `backend/src/modules/cards/data.py:29` has `proactivity_tag`, `cards/data.py:30` has `verb`. `HarkLeftRail.tsx:195` agenda filter and `HarkLeftRail.tsx:186` waiting filter read from `useCards()`, so rail panels are views over cards, not agent defined layouts. |
| Agent tool harness | Exists, 15 tools, no `create_widget` | `backend/src/modules/runs/tools/__init__.py:30` sets `TOOL_COUNT = 15`. Tool levels are `read \| local \| external` in same file. Delivery tools only write files to `delivery_dir` (`backend/src/modules/runs/tools/delivery.py:1`). No file in `backend/src/modules/runs/tools/` mentions widget. |
| Postgres schema | No widgets table | `backend/alembic/versions/0001_initial_schema.py:28` through `0001_initial_schema.py:582` create `users, jobs, idempotency_records, devices, connected_accounts, connector_sync_state, connector_items, triage_decisions, cards, card_actions, threads, runs, messages, approvals, artifacts, memory_chunks`. Later versions add `run_workspaces, connector_tools` (`0002_schema_corrections.py:83`), `agent_configs` (`0003_agent_configs.py:30`), `skills` (`0004_skills.py:28`), `audit_log`, `mcp_servers`, `run_steps`, `model_prices`, `vault_secrets`. No migration creates `widgets`. |
| Background refresh path | Queue exists, new type needs registration | `backend/src/modules/jobs/service.py:25` has fixed `JOB_TYPE_PRIORITIES` with 7 types. `backend/src/worker.py:247` has `JOB_HANDLERS` for the same set. `backend/src/worker.py:259` lists `UNWIRED_JOB_TYPES`. A new `refresh_widget` type must be added in both places or it raises `UnknownJobTypeError` (`worker.py:48`) or records failure via `JobHandlerUnavailableError` (`worker.py:53`). |

Conclusion: the pasted SDUI proposal is directionally correct for this repo. Nothing it assumes exists yet. Panels today are per device toggles and pinned HTML blobs. There is no `create_widget` tool, no widget registry, no `widgets` table, no refresh worker, no SSE widget channel.

## 3. Proposal Assessment

The pasted note proposes: fixed layout registry plus JSON validation, one `widgets` table with `data_payload` and `refresh_config`, three update paths (scheduled, event push, interactive), SSE push with dynamic component lookup.

What to keep:

- Fixed registry, not free HTML generation. This matches the existing sandbox posture (`allow-scripts` only, no `allow-same-origin` in `WidgetsRail.tsx:93`) and the Hark card language. Free HTML per widget would bypass the card and approval idioms already in `cards/data.py` and `runs/tools/__init__.py`.
- JSONB `data_payload` plus `refresh_config` in one row. This is the right split. It avoids stretching `artifacts` (file path based) into a live query object.
- Pydantic first, then mirror in Zod. Repo already validates tool args with Pydantic `extra="forbid"` (see `delivery.py:37`). Do the same for widgets.

What to change from the pasted note:

- Do not put cron strings directly from the model into the worker. Accept `refresh_interval_minutes` with an allowlist (for example 15, 60, 240, 1440) and compute `next_refresh_at` server side. Raw cron from model input is fragile and hard to audit in the jobs table.
- Do not let the model write arbitrary `source: gmail` plus free text instruction as executable config. `refresh_config` should be a discriminated union: `{kind: connector_query, connector_id, query}` or `{kind: sql_view, view_name}` or `{kind: static}`. The worker maps `kind` to an existing tool call. Free text stays inside `prompt_hint`, never as code.
- Prefer polling the jobs queue over Postgres `LISTEN` for v1. The repo already has `SKIP LOCKED` claiming, heartbeat, and reaper in `jobs/service.py:72`. Adding one job type reuses that. SSE or websocket then tails the row version.
- Keep `title` user editable but constrain `widget_type` server side with a Literal. The frontend `WIDGET_MAP` must have an explicit fallback card for unknown types so one bad row cannot blank the rail.

## 4. Target Design on This Stack

### 4.1 Backend schema (Pydantic v2)

```python
# backend/src/modules/widgets/data.py
from datetime import datetime
from typing import Literal, Union
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field

WidgetType = Literal["metric_card", "data_table", "task_list", "status_feed"]

class MetricPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str = Field(max_length=64)
    delta: str | None = Field(default=None, max_length=64)
    caption: str | None = Field(default=None, max_length=200)

class TablePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    columns: list[str] = Field(min_length=1, max_length=8)
    rows: list[list[str]] = Field(min_length=0, max_length=50)

class TaskItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(max_length=64)
    label: str = Field(max_length=200)
    done: bool = False

class TaskListPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[TaskItem] = Field(max_length=50)

class FeedEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ts: str = Field(max_length=64)
    text: str = Field(max_length=280)

class FeedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entries: list[FeedEntry] = Field(max_length=30)

class ConnectorQueryRefresh(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["connector_query"] = "connector_query"
    connector_id: str = Field(max_length=64)
    query: str = Field(max_length=500)
    prompt_hint: str | None = Field(default=None, max_length=1000)

class StaticRefresh(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["static"] = "static"

RefreshConfig = Union[ConnectorQueryRefresh, StaticRefresh]

class WidgetCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=120)
    widget_type: WidgetType
    data_payload: MetricPayload | TablePayload | TaskListPayload | FeedPayload
    refresh: ConnectorQueryRefresh | StaticRefresh | None = None
    refresh_interval_minutes: Literal[15, 60, 240, 1440] | None = None

class WidgetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    title: str
    widget_type: WidgetType
    data_payload: dict
    updated_at: datetime
    version: int
```

Validation rule: check `widget_type` matches payload shape before insert. Return the Pydantic error to the model so it can retry, same pattern as tool arg validation in `runs/tools/__init__.py`.

### 4.2 Postgres table (Alembic)

```python
op.create_table(
    "widgets",
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("title", sa.Text(), nullable=False),
    sa.Column("widget_type", sa.Text(), nullable=False),
    sa.Column("data_payload", postgresql.JSONB(), nullable=False),
    sa.Column("refresh_config", postgresql.JSONB(), nullable=True),
    sa.Column("refresh_interval_minutes", sa.Integer(), nullable=True),
    sa.Column("next_refresh_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
)
```

Notes: scope every query by `user_id` (matches Firebase principal convention in repo memory). Add a CHECK on `widget_type in (metric_card, data_table, task_list, status_feed)`. Remember `vigil-backend-review` pitfall: Alembic CHECKs do not run in unit tests that build from metadata, so add one API test against migrated dev DB that inserts a bad type and expects 422.

### 4.3 Agent tools

Add two tools to `backend/src/modules/runs/tools/` following the existing `Tool` harness:

- `create_widget(args: WidgetCreateRequest)` level `local`. Inserts row, enqueues `refresh_widget` job if `refresh` is set, returns `{widget_id, widget_type, title}`.
- `update_widget(args: {widget_id, data_payload})` level `local`. Validates payload shape against stored `widget_type`, bumps `version`, sets `updated_at`.

Approval: both are `local` writes, so they pass through the runs approval policy that already partitions `read \| local \| external`. Do not mark them `read` to avoid silent dashboard writes.

### 4.4 Refresh worker

1. Add `"refresh_widget": 40` to `JOB_TYPE_PRIORITIES` in `backend/src/modules/jobs/service.py:25`.
2. Add `refresh_widget` handler in `backend/src/worker.py:247` that loads the row, runs only the `kind` allowlisted in `refresh_config`, revalidates payload, updates `data_payload` plus `version`, sets `next_refresh_at`.
3. Event push: existing ingest paths that create cards (`cards`, `triage_decisions` tables) can enqueue `refresh_widget` for widgets whose `refresh_config.connector_id` matches. No direct widget mutation from ingest code.
4. Interactive: row level buttons (`Refresh`, row `Archive` or task toggle) POST `widget_id` plus `action` to the orchestrator, which runs one tool call and returns the new `version`. Frontend swaps on version change, no full reload.

Poll cadence: a 60s sweeper that claims due rows (`next_refresh_at <= now`) is enough for low throughput. No Redis needed.

### 4.5 Frontend registry (React + Vite, Zod mirror)

```tsx
// web/src/components/widgets/registry.tsx
import { z } from "zod";
import MetricCard from "./MetricCard";
import DataTable from "./DataTable";
import TaskList from "./TaskList";
import StatusFeed from "./StatusFeed";

export const widgetPayload = z.discriminatedUnion("widget_type", [
  z.object({ widget_type: z.literal("metric_card"), data_payload: z.object({ value: z.string() }) }),
  z.object({ widget_type: z.literal("data_table"), data_payload: z.object({ columns: z.array(z.string()), rows: z.array(z.array(z.string())) }) }),
  z.object({ widget_type: z.literal("task_list"), data_payload: z.object({ items: z.array(z.object({ id: z.string(), label: z.string(), done: z.boolean() })) }) }),
  z.object({ widget_type: z.literal("status_feed"), data_payload: z.object({ entries: z.array(z.object({ ts: z.string(), text: z.string() })) }) }),
]);

export const WIDGET_MAP = { metric_card: MetricCard, data_table: DataTable, task_list: TaskList, status_feed: StatusFeed } as const;

export function WidgetView({ widget }: { widget: z.infer<typeof widgetPayload> & { id: string; title: string } }) {
  const parsed = widgetPayload.safeParse(widget);
  if (!parsed.success) return <FallbackCard title={widget.title} />;
  const C = WIDGET_MAP[parsed.data.widget_type];
  return <C data={parsed.data.data_payload} />;
}
```

Render path: fetch `GET /api/v1/widgets`, `safeParse` each row, render via `WIDGET_MAP`. Subscribe to `GET /api/v1/widgets/stream` (SSE `widget_updated` with `{id, version, data_payload}`). On event, replace JSON in state by id. Unknown type renders a fallback card with title plus `updated_at`, never a blank rail.

Placement: keep `HarkLeftRail.tsx` for the 4 system panels. Render agent widgets in `WidgetsRail.tsx` under a new `Your panels` section above `Pinned Micro-Apps` (`WidgetsRail.tsx:61`), or as an extension of that section once backend backed. Migrate `vigil_pinned_widgets` later, do not fork two pin systems.

### 4.6 Minimal row example

```json
{
  "title": "Pending Client Approvals",
  "widget_type": "data_table",
  "data_payload": {
    "columns": ["Client", "Item", "Due"],
    "rows": [["Acme Corp", "Q4 Contract", "Nov 14"], ["Stark Ind", "Invoice 104", "Nov 18"]]
  },
  "refresh": {
    "kind": "connector_query",
    "connector_id": "gmail",
    "query": "label:Approvals newer_than:7d",
    "prompt_hint": "Extract client, subject item, and any mentioned deadline."
  },
  "refresh_interval_minutes": 240
}
```

## 5. Risks and Open Points

- Approval surface: dashboard writes are user visible and persistent. Keep both tools at `local` so the existing run policy gates them. Verify in `modules/runs/state.py` policy before marking done.
- Cross user leak: `JobsService` keeps a class level `_jobs` dict (`jobs/service.py:80`). Widget service must not copy that pattern. Use Postgres rows scoped by `user_id`.
- Truncation: card and connector paths slice content for context budgets. Note every truncation in refresh prompts or the model will invent deadlines. Cap tables at 50 rows and feeds at 30 entries, same as schemas above.
- SSE scale: one stream per dashboard is fine. Do not fan out per widget.
- Migration from local pins: `vigil_pinned_widgets` and `vigil.rail.panels` stay for v1. Import is a separate pass once the table and stream are live.

## 6. Priority List

| Priority | Item |
|---|---|
| P0 | Add `widgets` migration plus Pydantic schemas with forbid and type matched payload validation, and one API test on migrated Postgres for the CHECK constraint. |
| P0 | Add `create_widget` and `update_widget` tools at `local` approval, wired into the run tool registry, with user scoping. |
| P0 | Add `refresh_widget` to `JOB_TYPE_PRIORITIES` and `JOB_HANDLERS`, remove from unwired set when executor lands, add sweeper for `next_refresh_at`. |
| P1 | Build `WIDGET_MAP` with 4 templates plus fallback card, Zod `safeParse`, SSE `widget_updated` swap by id and version. |
| P1 | Render agent widgets in `WidgetsRail.tsx` as `Your panels`, keep `HarkLeftRail.tsx` system panels untouched for v1. |
| P2 | Event push from ingest (card creation enqueues refresh for matching `connector_id`), interactive row actions with version bump. |
| P2 | Import path for existing `localStorage` pins into `widgets` rows, then deprecate the local keys. |
