# Agent Widgets Build Plan (P0 + P1)

Source of truth: `docs/agent-widgets-review.md`. Scope is P0 + P1 only. P2 items are listed as follow-ups and are not to be built in this plan.

Conventions: user-facing prose says `the model`, never a vendor name. No em-dash characters in repo text, use hyphens or commas. `HarkLeftRail.tsx` stays untouched.

## 0. Verified anchor points

All paths below were read before writing this plan:

- Left rail panels are hardcoded to 4 ids: `web/src/components/hark/HarkLeftRail.tsx:24` defines `PanelId = today | usage | agenda | waiting`, and `web/src/components/hark/HarkLeftRail.tsx:26` stores choice in `localStorage` key `vigil.rail.panels`.
- Rail cards are views over cards: `web/src/components/hark/HarkLeftRail.tsx:186` builds `waitingCards`, `web/src/components/hark/HarkLeftRail.tsx:195` builds `agendaCards` from `useCards()`.
- `Create a new panel` only opens a toggle menu: `web/src/components/hark/HarkLeftRail.tsx:403` is the menu wrapper, `web/src/components/hark/HarkLeftRail.tsx:415` renders the toggle list. It creates no record.
- Home rail today is local pins plus status sections: `web/src/components/home/WidgetsRail.tsx:53` reads `usePinnedWidgets()`, `web/src/components/home/WidgetsRail.tsx:61` renders `Pinned Micro-Apps`, `web/src/components/home/WidgetsRail.tsx:92` renders `iframe sandbox="allow-scripts" srcDoc={widget.content}`.
- Pin store is localStorage with cross-tab sync: `web/src/components/chat/ArtifactCard.tsx:27` defines `vigil_pinned_widgets`, `web/src/components/chat/ArtifactCard.tsx:50` dispatches `vigil:pinned-widgets-changed`.
- Artifacts are file backed, not JSON payload backed: `backend/src/modules/artifacts/data.py:8` requires `run_id, kind, path, size_bytes`, `backend/src/modules/artifacts/data.py:11` fixes kinds to `document | diff | log | export | micro_app | screenshot`.
- Tool harness has 15 tools and no widget tool: `backend/src/modules/runs/tools/__init__.py:30` sets `TOOL_COUNT = 15`, `backend/src/modules/runs/tools/__init__.py:26` defines levels `read | local | external`. No file under `backend/src/modules/runs/tools/` mentions widget (verified by search).
- Tool arg idiom is Pydantic `extra="forbid"`: `backend/src/modules/runs/tools/delivery.py:37` and `delivery.py:54` are the pattern to copy.
- Queue has 7 fixed types: `backend/src/modules/jobs/service.py:25` defines `JOB_TYPE_PRIORITIES` with 7 entries. `backend/src/modules/jobs/service.py:80` holds the class level `_jobs` dict that widget code must not copy (use Postgres rows scoped by `user_id`).
- Worker dispatch is a fixed table: `backend/src/worker.py:244` defines `JOB_HANDLERS`, `backend/src/worker.py:259` defines `UNWIRED_JOB_TYPES`, `backend/src/worker.py:48` raises `UnknownJobTypeError`, `backend/src/worker.py:53` raises `JobHandlerUnavailableError`.
- Cards carry the feed idiom widgets reuse: `backend/src/modules/cards/data.py:29` has `proactivity_tag`, `backend/src/modules/cards/data.py:30` has `verb`.
- Migrations live in `backend/alembic/versions/`, head is `0010` (`down_revision = "0009"` in `0010_vault_secrets.py`). Chain is `0001` through `0010`, no `widgets` table exists.
- Frontend SSE idiom is fetch plus reader, never EventSource: `web/src/lib/sse.ts:24` exports `createEventStream`, `web/src/types/index.ts:357` documents why EventSource is not used (needs `Authorization` header). Runs streaming precedent: `openRunStream` at `web/src/lib/api.ts:305` built on `createEventStream` at `web/src/lib/sse.ts:24`; the fetch-then-subscribe merge is documented in the `useRun` doc comment at `web/src/queries/runs.ts:50`.

## 1. Migration design (P0)

New file: `backend/alembic/versions/0011_widgets.py`, `down_revision = "0010"`, `revision = "0011"`.

Table `widgets`:

- `id` UUID PK, `default=uuid4`.
- `user_id` UUID FK `users.id` `nullable=False`, `ondelete="CASCADE"`. Every read and write filters on this column.
- `title` Text `nullable=False`.
- `widget_type` Text `nullable=False` with CHECK `widget_type in ('metric_card', 'data_table', 'task_list', 'status_feed')`.
- `data_payload` JSONB `nullable=False`.
- `refresh_config` JSONB `nullable=True`.
- `refresh_interval_minutes` Integer `nullable=True` with CHECK `in (15, 60, 240, 1440)` or null.
- `next_refresh_at` timestamptz `nullable=True`.
- `version` Integer `nullable=False`, `server_default="1"`.
- `created_at`, `updated_at` timestamptz `nullable=False`, `server_default=sa.func.now()`.
- Indexes: `ix_widgets_user_id (user_id)`, `ix_widgets_user_refresh (user_id, next_refresh_at)` for the sweeper, unique is not needed beyond PK.

Downgrade drops indexes then the table.

Notes and pitfalls:

- Keep raw cron out. The model sends only `refresh_interval_minutes` from the allowlist, the server computes `next_refresh_at`.
- Keep `refresh_config` a discriminated union (`connector_query | static` for v1). Free text stays in `prompt_hint`, never executed as code.
- The repo memory warns that Alembic CHECKs do not run in unit tests that build from metadata, so add one API level test on a migrated dev DB that inserts a bad `widget_type` and expects 422 (see test plan).

## 2. Pydantic v2 schemas (P0)

New file: `backend/src/modules/widgets/data.py`. All models use `ConfigDict(extra="forbid")`, mirroring `backend/src/modules/runs/tools/delivery.py:37`.

- `WidgetType = Literal["metric_card", "data_table", "task_list", "status_feed"]`.
- `MetricPayload`: `value` (max 64), `delta` (optional, max 64), `caption` (optional, max 200).
- `TablePayload`: `columns` list 1..8, `rows` list of string lists, 0..50 rows.
- `TaskItem`: `id` (max 64), `label` (max 200), `done=False`.
- `TaskListPayload`: `items` max 50.
- `FeedEntry`: `ts` (max 64), `text` (max 280). `FeedPayload`: `entries` max 30.
- `ConnectorQueryRefresh`: `kind="connector_query"`, `connector_id` (max 64), `query` (max 500), `prompt_hint` optional (max 1000).
- `StaticRefresh`: `kind="static"`.
- `RefreshConfig = ConnectorQueryRefresh | StaticRefresh` (discriminated by `kind`).
- `WidgetCreateRequest`: `title` 1..120, `widget_type`, `data_payload` union of the 4 payloads, `refresh` optional, `refresh_interval_minutes` optional `Literal[15, 60, 240, 1440]`.
- `WidgetUpdateRequest`: `widget_id`, `data_payload` union. Type match is checked against the stored `widget_type`.
- `WidgetResponse`: `id, user_id, title, widget_type, data_payload: dict, refresh_config: dict | None, refresh_interval_minutes: int | None, next_refresh_at: datetime | None, version: int, updated_at: datetime`, `from_attributes=True`.

Validation rule: on create and update, check `widget_type` matches payload shape before insert. Return the Pydantic error to the model so it can retry, same pattern as tool arg validation in `backend/src/modules/runs/tools/__init__.py:136`.

Service layer: new `backend/src/modules/widgets/service.py` plus repository. Methods: `create(user_id, req)`, `get(user_id, widget_id)`, `list(user_id)`, `update_payload(user_id, widget_id, payload)` (bumps `version`, sets `updated_at`), `claim_due_refresh(limit)` for the sweeper (`next_refresh_at <= now`, ordered by `next_refresh_at`, `SKIP LOCKED`). No class level dict cache (contrast `backend/src/modules/jobs/service.py:80`).

API router `backend/src/modules/widgets/router.py`, prefix `/widgets`, wired into the app factory next to the cards router pattern (`backend/src/modules/cards/router.py:35` uses `APIRouter(prefix="/cards")`). Register `GET /stream` before `GET|PATCH /{id}` in the new router so `/stream` never 422s on UUID parsing:

- `POST /api/v1/widgets` (422 on bad type or payload).
- `GET /api/v1/widgets` (scoped list, ordered by `updated_at` desc).
- `GET /api/v1/widgets/stream` (stateless cursor poll over `(updated_at, version)` modeled on `backend/src/modules/runs/stream.py:1`, with `POLL_INTERVAL_SECONDS = 0.5` at `backend/src/modules/runs/stream.py:19` and `Last-Event-ID` replay on the `parse_last_event_id` pattern at `backend/src/modules/runs/stream.py:39`; emits `widget_updated {id, version, data_payload, updated_at}`, one stream per dashboard, auth header via `createEventStream`).
- `PATCH /api/v1/widgets/{id}` (payload update, version bump).

## 3. Tool specs (P0)

New module `backend/src/modules/runs/tools/widgets.py`, registered in `backend/src/modules/runs/tools/__init__.py:179` `TOOLS` tuple, bumping `TOOL_COUNT` at `backend/src/modules/runs/tools/__init__.py:32` from 15 to 17.

`ToolContext` (`backend/src/modules/runs/tools/__init__.py:71`) currently carries only `runner, container_id, delivery_dir, llm, secrets`, with no identity. All three construction sites build it without identity: `backend/src/dependencies.py:770` and the two MCP-wrapper sites at `backend/src/modules/runs/service.py:69` and `backend/src/modules/runs/service.py:86`. So first:

- Add `run_id: UUID | None = None` and `user_id: UUID | None = None` to `ToolContext`. Neither field is ever set from tool args; the model never supplies identity.
- Populate both in `RunToolExecutor.execute` at `backend/src/dependencies.py:770`, which owns `session` plus `run_id` via `build_tool_executor` at `backend/src/dependencies.py:902`, by loading the run owner with the existing `_run_owner_id` pattern and passing `run_id=self.run_id, user_id=owner`.
- Pass both through (or explicitly `None`) at the two `backend/src/modules/runs/service.py` MCP-wrapper sites. Widget tool handlers reject `None` identity with a failed `ToolResult`, they never fall back to an arg-supplied id.

`create_widget`, level `local`:

- Args model: `WidgetCreateRequest` shape (`title`, `widget_type`, `data_payload`, optional `refresh`, optional `refresh_interval_minutes`), `extra="forbid"`, no `user_id` field. Any `user_id` in args is rejected by `extra="forbid"`.
- Handler: reads `user_id` from the `ToolContext` only and returns a failed `ToolResult` when it is `None`, validates type matches payload, inserts the row scoped to the context `user_id`, computes `next_refresh_at = now + interval` when a refresh is set, enqueues a `refresh_widget` job when `refresh.kind == "connector_query"`, returns `{widget_id, widget_type, title, version: 1}`.
- Failure mode: validation error returns a failed `ToolResult` with the Pydantic message so the model can retry, never raises across the boundary (same contract as `backend/src/modules/runs/tools/__init__.py:136`).

`update_widget`, level `local`:

- Args model: `widget_id`, `data_payload` union, `extra="forbid"`, no `user_id` field.
- Handler: reads `user_id` from the `ToolContext` only and returns a failed `ToolResult` when it is `None`, loads the row scoped by that `user_id`, rejects cross user ids with not found, validates payload against stored `widget_type`, bumps `version`, sets `updated_at`, returns `{widget_id, version}`.
- Both tools are `local`, not `read`, so the existing run approval policy gates dashboard writes. Do not mark them `read` to avoid silent writes.

User scoping test: two users, one widget row, second user update returns not found and version is unchanged.

## 4. Worker spec (P0)

1. `backend/src/modules/jobs/service.py:25`: add `"refresh_widget": 40` to `JOB_TYPE_PRIORITIES`. Priority sits below `index_memory` (50) and above `poll_connector` (20).
2. `backend/src/worker.py:244` `JOB_HANDLERS`: add `refresh_widget: handle_refresh_widget`. Remove nothing from `backend/src/worker.py:259` `UNWIRED_JOB_TYPES` (that set only tracks the 4 preexisting unwired types).
3. `handle_refresh_widget(job, context)`:
   - Parse payload `{widget_id, user_id}`. Missing ids raise `JobHandlerUnavailableError` (same style as `backend/src/worker.py:104`).
   - Load row scoped by `user_id`. If missing, mark permanent failure, no retry.
   - Switch on `refresh_config.kind`. Only `connector_query` runs in v1: call the existing connector query path with `connector_id` and `query`, truncate to schema caps (tables 50 rows, feeds 30 entries), revalidate payload against stored `widget_type` with the Pydantic models, update `data_payload`, bump `version`, set `next_refresh_at = now + refresh_interval_minutes`.
   - `static` kind: only recompute `next_refresh_at`, no data change.
   - Unknown kind: permanent failure, no payload write.
4. Sweeper for `next_refresh_at`: add a 60s cadence widget sweep in `run_worker` next to the idle sweep at `backend/src/worker.py:533`. The sweeper queries due rows (`next_refresh_at <= now`, limit 50) and enqueues one `refresh_widget` job per row with `run_at = next_refresh_at`. Dedupe: skip rows that already have a pending `refresh_widget` job for that `widget_id`. No Redis, no Postgres LISTEN for v1, reuse the `SKIP LOCKED` claim path in `backend/src/modules/jobs/service.py:132`.
5. No event push after commit: the worker only commits the row plus `version` bump and `updated_at`. There is no SSE channel, bus, or publish primitive to call. The dashboard sees the change on the next cursor poll of `GET /api/v1/widgets/stream` (see section 2). Frontend swaps on version change (see section 5).

Retry policy: transient connector errors retry with `JOB_RETRY_DELAY_SECONDS` (`backend/src/worker.py:38`); validation errors and missing rows are permanent.

## 5. Frontend spec (P1)

New directory `web/src/components/widgets/` (does not exist today, verified). Files: `MetricCard.tsx`, `DataTable.tsx`, `TaskList.tsx`, `StatusFeed.tsx`, `FallbackCard.tsx`, `registry.tsx`.

`registry.tsx`:

- Zod discriminated union on `widget_type` with the 4 payload shapes, mirroring the backend caps.
- `WIDGET_MAP = { metric_card: MetricCard, data_table: DataTable, task_list: TaskList, status_feed: StatusFeed }`.
- `WidgetView({ widget })`: `safeParse`, render fallback on failure. Fallback shows title plus `updated_at`, never a blank rail.
- Unknown `widget_type` from the backend renders the fallback card, it never throws.

`web/src/components/home/WidgetsRail.tsx` changes:

- Add a `Your panels` section above `Pinned Micro-Apps` (`web/src/components/home/WidgetsRail.tsx:61`). Fetch via a new `useWidgets()` hook (`GET /api/v1/widgets`), `safeParse` each row, render through `WIDGET_MAP`.
- Subscribe with `createEventStream` (`web/src/lib/sse.ts:24`) to `GET /api/v1/widgets/stream`, a stateless cursor poll modeled on `backend/src/modules/runs/stream.py:1` with `Last-Event-ID` replay. On `widget_updated {id, version, data_payload}`, replace JSON in state by id only when `version` is newer. One stream per dashboard, not one per widget.
- Keep the existing `Pinned Micro-Apps` section and the `allow-scripts` iframe at `web/src/components/home/WidgetsRail.tsx:92` unchanged for v1. No migration of `vigil_pinned_widgets` (`web/src/components/chat/ArtifactCard.tsx:27`) in this plan.
- `web/src/components/hark/HarkLeftRail.tsx` is untouched. No imports, no new panel ids, no edits to `HarkLeftRail.tsx:24` or the menu at `HarkLeftRail.tsx:403`.

Styling: reuse existing `card` and `rounded-squircle-card` classes from `WidgetsRail.tsx`, no new design system.

## 6. Test plan

Backend (pytest, Postgres backed where noted):

1. Migration upgrade and downgrade: `head` includes `widgets`, downgrade one step drops it, upgrade again restores it. Extends the pattern in `backend/tests/integration/test_migrations.py:100`.
2. CHECK constraint on migrated DB: insert bad `widget_type`, expect 422 from `POST /api/v1/widgets`. Insert bad `refresh_interval_minutes` (for example 5), expect 422. This covers the metadata-only test gap noted in the review.
3. Schema validation: each of the 4 payload shapes accepts good input and rejects wrong shape with `extra="forbid"` (unknown key rejected, overlong strings rejected, over-cap rows rejected).
4. Type matched enforcement: create `metric_card` with table payload fails; `update_widget` with mismatched payload fails and `version` is unchanged.
5. Tool registry: `TOOL_COUNT` is 17, `create_widget` and `update_widget` resolve with level `local`, bad args return failed `ToolResult` instead of raising.
6. User scoping: user B cannot read or update user A widget (404), list returns only own rows.
7. Worker dispatch: `resolve_handler("refresh_widget")` returns a handler; unknown types still raise `UnknownJobTypeError` (`backend/src/worker.py:48`).
8. Sweeper: due rows (`next_refresh_at <= now`) get exactly one `refresh_widget` job each; future rows get none; rerun does not duplicate pending rows.

Frontend (typecheck plus focused tests):

9. Registry: `safeParse` accepts the 4 shapes, rejects mismatched payload, unknown type renders `FallbackCard` with title.
10. SSE swap: `widget_updated` with a higher `version` replaces the row, stale `version` is ignored, unrelated ids are untouched.
11. No-regression: `HarkLeftRail.tsx` unchanged (4 panel ids intact), `Pinned Micro-Apps` section still renders from localStorage when the widgets API is empty or errors.

## 7. Ordered coder-sized task breakdown

Each task is one reviewable change with its verify command. Stop after P1 task 13. No P2 work.

P0 backend:

1. Migration `0011_widgets`: create `backend/alembic/versions/0011_widgets.py` (`down_revision = "0010"`), upgrade, downgrade, indexes.
   Verify: `cd backend && alembic upgrade head && alembic downgrade -1 && alembic upgrade head`
2. Schemas `backend/src/modules/widgets/data.py`: 4 payloads, 2 refresh kinds, create and update requests, response, `extra="forbid"` everywhere.
   Verify: `cd backend && python -m pytest tests/unit/test_widgets_schemas.py -q`
3. Service plus repository `backend/src/modules/widgets/`: `create`, `get`, `list`, `update_payload` with `user_id` scoping, `version` bump, `next_refresh_at` computation.
   Verify: `cd backend && python -m pytest tests/unit/test_widgets_service.py -q`
4. Router `backend/src/modules/widgets/router.py` plus app wiring: `POST`, `GET`, `PATCH`, `GET /stream` registered before `GET|PATCH /{id}`, cursor-poll skeleton returning SSE headers modeled on `backend/src/modules/runs/stream.py:1`; 422 on bad type and bad interval on a migrated DB.
   Verify: `cd backend && python -m pytest tests/integration/test_widgets_api.py -q`
5. Tools `backend/src/modules/runs/tools/widgets.py` plus registry wiring in `backend/src/modules/runs/tools/__init__.py:179`, `TOOL_COUNT` 15 to 17, both tools at level `local`, with `run_id` plus `user_id` threaded through `ToolContext` (`backend/src/modules/runs/tools/__init__.py:71`) from `backend/src/dependencies.py:770` and rejected when `None`.
   Verify: `cd backend && python -m pytest tests/unit/test_tools.py tests/unit/test_widgets_tools.py -q`
6. Queue registration: add `"refresh_widget": 40` in `backend/src/modules/jobs/service.py:25`.
   Verify: `cd backend && python -m pytest tests/integration/test_jobs_queue.py -q`
7. Worker handler plus sweeper: `handle_refresh_widget` in `backend/src/worker.py`, entry in `JOB_HANDLERS` at `backend/src/worker.py:244`, 60s due-row sweep next to `backend/src/worker.py:533`, commit only with `version` bump and no publish primitive.
   Verify: `cd backend && python -m pytest tests/unit/test_worker_refresh_widget.py tests/integration/test_widgets_refresh.py -q`

P1 frontend:

8. Templates: `MetricCard`, `DataTable`, `TaskList`, `StatusFeed`, `FallbackCard` under `web/src/components/widgets/`.
   Verify: `npm --prefix web run typecheck`
9. Registry `web/src/components/widgets/registry.tsx`: Zod union, `WIDGET_MAP`, `WidgetView` with `safeParse` plus fallback.
   Verify: `npm --prefix web run typecheck`
10. Data hook `web/src/queries/widgets.ts`: list fetch, SSE subscribe via `createEventStream` (`web/src/lib/sse.ts:24`), swap by id when `version` is newer.
    Verify: `npm --prefix web run typecheck`
11. `WidgetsRail.tsx` `Your panels` section above `web/src/components/home/WidgetsRail.tsx:61`, SSE wiring, fallback on parse failure, pins section untouched.
    Verify: `npm --prefix web run typecheck && npm --prefix web run build`
12. Frontend tests: registry parse and fallback, SSE version swap and stale ignore.
    Verify: `npm --prefix web run typecheck`
13. Full gate: backend widget suites plus migration suite plus web typecheck, confirm `HarkLeftRail.tsx` has no diff and no P2 code present.
    Verify: `cd backend && python -m pytest tests/integration/test_migrations.py tests/integration/test_widgets_api.py -q && git status --short && git diff --stat`

Suggested `test_widgets_*`, `test_worker_refresh_widget`, and `test_widgets_refresh` file names above are new files; `test_tools.py`, `test_jobs_queue.py`, and `test_migrations.py` are existing suites extended in place.

## 8. Open questions (max 5) with recommended defaults

1. Should `update_widget` allow title changes, or payload only? Default: payload only for v1, title stays user editable in the UI later without a tool change.
2. Should refresh support a `sql_view` kind in v1, or only `connector_query` and `static`? Default: only `connector_query` and `static`, add `sql_view` with P2 event push.
3. Should widget reads be paginated like cards (`backend/src/modules/cards/data.py:43` `CardListResponse`)? Default: no pagination for v1, dashboards hold few rows, order by `updated_at` desc.
4. Should the sweeper live in the main worker loop or a separate process? Default: main loop on the existing 60s cadence next to `backend/src/worker.py:533`, separate process only if refresh volume grows.
5. Should `widget_updated` carry the full payload or only id plus version with a refetch? Default: full payload for v1 (rows are small and capped), refetch only if payloads grow.

## 9. P2 follow-ups (out of scope, do not build here)

- Event push from ingest paths: card creation enqueues `refresh_widget` for widgets whose `refresh_config.connector_id` matches. No direct widget mutation from ingest code.
- Interactive row actions: row level `Refresh`, row `Archive`, or task toggle via `POST widget_id plus action` to the orchestrator, returning the new `version` for the SSE swap.
- Import path for existing local pins: migrate `vigil_pinned_widgets` (`web/src/components/chat/ArtifactCard.tsx:27`) and `vigil.rail.panels` (`web/src/components/hark/HarkLeftRail.tsx:26`) into `widgets` rows, then deprecate the local keys.
