# Agent Widgets P0+P1 Build Review

Scope: working tree on `vigil-agent/widgets-p0p1-build`, audited against
`docs/agent-widgets-build-plan.md` (APPROVED), prior findings in
`docs/agent-widgets-plan-review.md`, and the review body in the scratch
`vigil-build-review-body.md`. Code audited directly, not suite output.

## Verdict: REJECT

Two structural findings break the P0 loop (model creates widget, user sees
panel, worker refreshes it). Fix findings 1-2 and resubmit. Finding 3 rides
along.

## Findings

1. **Severity: HI. File: `backend/src/modules/runs/tools/widgets.py:44-49`
   (`_widgets_service`) and `:120` (`JobsService(session=None, ...)`).
   Violated invariant: one Postgres store scoped by `user_id` for tools, API,
   and worker (plan sections 2-4; check 1).** Both services are built with
   `session=None`, so `_is_db_session_active()` is False and every tool call
   takes the in-memory branch: widget rows land in the process-local
   `WidgetsService._widgets` dict and the `refresh_widget` job lands in the
   process-local `JobsService._jobs` dict. In production the API list/stream
   (`get_widgets_service` with a real session) reads Postgres, so
   tool-created widgets are invisible to `GET /widgets` and
   `GET /widgets/stream`; worse, `handle_refresh_widget` loads via
   `context.session` (real DB), so a connector refresh of a tool-created row
   misses and fails permanently. Unit tests pass only because they share one
   process with the ClassVar. Concrete fix: thread the DB session through
   `ToolContext` (populated in `RunToolExecutor.execute` next to `run_id` /
   `user_id`, passed through at the two `runs/service.py` MCP-wrapper sites),
   build both services with it in the widget tools, and fail loudly when the
   session is absent instead of silently writing to memory.

2. **Severity: MED. File: `backend/src/modules/widgets/service.py:35`
   (`_widgets: ClassVar[dict[UUID, WidgetDTO]] = {}`). Violated invariant: no
   class-level state holding rows (plan section 2, "No class level dict cache
   (contrast `backend/src/modules/jobs/service.py:80`)"; check 6).** All reads
   filter by `user_id`, so there is no active cross-user read leak, but rows
   of every user accumulate unbounded in process memory, leak across requests
   and tests, and the tool path (finding 1) depends on exactly this shared
   dict. Concrete fix: make the memory store an instance-level dict used only
   by tests (or a dedicated in-memory repository double); never a ClassVar;
   production paths always use the Postgres session.

3. **Severity: LOW. File: `.commandcode/taste/taste/taste.md:4` (added line).
   Violated invariant: no em-dash characters in added repo text.** The
   auto-added taste entry contains "remote, asks". Unrelated to the widget
   build, but the gate is strict. Concrete fix: drop or reword that hunk
   ("remote, asks") before landing.

## What checked out (no action)

- Migration `0011`: `down_revision = "0010"`, `user_id` FK `CASCADE`, both
  CHECKs, both indexes (`ix_widgets_user_id`, `ix_widgets_user_refresh`),
  downgrade drops indexes then table. Nullable interval passes CHECK on NULL
  correctly.
- Router: `POST`, `GET` (list), `GET /stream` before `PATCH /{widget_id}`;
  `Last-Event-ID` wins over `since`; `ValidationError`/`ValueError` mapped to
  422; every read scoped by `user.id`; integration tests assert 422 on bad
  type, bad interval, mismatched payload, plus CHECK-level rejects.
- Schemas: `extra="forbid"` on every input model, discriminated refresh
  union, type-matched validation on create and update, version bump plus
  `updated_at` on payload write.
- Tools: both level `local`, no `user_id` in args, `None` identity rejected
  with failed `ToolResult`, `TOOL_COUNT` 15 to 17, `run_id`/`user_id`
  populated in `RunToolExecutor.execute` and passed through at both
  MCP-wrapper sites.
- Worker: `"refresh_widget": 40` in `JOB_TYPE_PRIORITIES`, handler in
  `JOB_HANDLERS`, 60s sweep next to the idle sweep, `SKIP LOCKED` claim with
  limit 50, pending-job dedupe, commit-only handler with no publish primitive
  (all `process_job` paths end in exactly one commit), permanent vs transient
  error split correct.
- Frontend: `WIDGET_MAP` plus `FallbackCard`, Zod `safeParse` with strict
  shapes and backend caps, `Your panels` above `Pinned Micro-Apps`, one
  dashboard-level stream with version-gated merge, pins section and
  `allow-scripts` iframe untouched, `HarkLeftRail.tsx` untouched, relative
  URLs only (`buildUrl`/`request`), mock returns empty list.
- Hygiene: no vendor model names in added lines, no P2 scope creep (no row
  actions, no ingest event push, no pin import), no hardcoded API base URLs.

## Re-audit trigger

Resubmit after findings 1-2 are fixed; 3 can ride along in the same revision.
Re-verify with the widget backend suites plus the migration suite on a
migrated dev DB, and confirm a tool-created widget is readable via
`GET /widgets` in the same test.

## Re-audit 2026-11-12: APPROVE

Both reworked findings hold on direct code read, and no new HI/MED findings exist.

1. Session threading (was HI): `ToolContext` carries `session` (`backend/src/modules/runs/tools/__init__.py:89-90`). `RunToolExecutor.execute` populates `run_id`, `user_id`, and `session` together (`backend/src/dependencies.py:783-792`). Both `MCPToolRegistryExecutor.execute` and `execute_external` forward `session` (`backend/src/modules/runs/service.py:69-77, 89-97`). The widget tools build `WidgetsService` and `JobsService` with the executor session and raise on `None` (`backend/src/modules/runs/tools/widgets.py:44-56, 127-128`); the harness boundary turns that raise into a failed `ToolResult`, never a silent memory write. Unit tests cover the `None` path (`test_create_widget_rejects_missing_session`, `test_update_widget_rejects_missing_session`); widget tools plus widget schemas suites pass (36 passed).
2. Instance state (was MED): no `ClassVar` remains in `backend/src/modules/widgets/service.py` (grep returns zero). The store is an instance dict, optionally carried from a session stub `_widgets_memory` used only by tests (`service.py:38-44`). Production always takes the Postgres branch: `_is_db_session_active` checks for a real `AsyncSession` and every method queries Postgres first; `get_widgets_service` always passes a real session (`dependencies.py:10-15`). The worker refresh path also loads through `context.session` (`backend/src/worker.py:325-328`).

No new HI/MED: widget reads stay scoped by `user_id`, the connector refresh job enqueues on the same session as the widget row, and the worker keeps the permanent vs transient error split. The taste.md added line still contains an em dash character, which stays LOW and does not block the P0 loop.
