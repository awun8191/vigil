# Agent Widgets Build Plan Review

Scope: `docs/agent-widgets-build-plan.md` (P0 + P1) audited against `docs/agent-widgets-review.md` (spec) and the repo, all paths re-read this session.

## Verdict: REJECT

The plan traces to real files and respects scope, schema, migration, queue, and frontend-placement invariants. But it is not buildable as written: one P0 user-scoping step has no implementation path (HI), and the SSE publish mechanism it specifies does not exist in the repo (MED). Fix the findings below and resubmit.

## Findings

1. **Severity: HI. Plan section: 3, Tool specs (`create_widget` / `update_widget` handler: "resolves `user_id` from the run owner"). Violated invariant: tools at local approval with user scoping (P0).** `ToolContext` (`backend/src/modules/runs/tools/__init__.py:71`) carries only `runner, container_id, delivery_dir, llm, secrets`; it has no `run_id`, no `user_id`, no session. All three construction sites (`backend/src/dependencies.py:770`, `backend/src/modules/runs/service.py:69`, `backend/src/modules/runs/service.py:86`) build it without identity, so the handler cannot resolve the run owner as written. A builder improvising here may put `user_id` in model-controlled tool args, which would allow cross-user writes. Concrete fix: add `run_id: UUID | None = None` and `user_id: UUID | None = None` to `ToolContext`; populate both in `RunToolExecutor.execute` (`dependencies.py:770`, which owns `session` + `run_id` via `build_tool_executor` at `dependencies.py:902`) by loading the run owner, pass them through (or explicitly `None`) at the two `runs/service.py` MCP-wrapper sites with widget tools rejecting `None`, and keep the section 3 user-scoping test.

2. **Severity: MED. Plan section: 2 (router `GET /widgets/stream`) and 4 step 5 ("the worker publishes `widget_updated` ... on the dashboard SSE channel"). Violated invariant: every plan claim traces to a real path; no invented infra.** No SSE channel, bus, or publish primitive exists in the repo. The established SSE idiom is a stateless cursor poll over a store, per `backend/src/modules/runs/stream.py:1` ("The stream is a cursor read ... then polls again after a short pause", `POLL_INTERVAL_SECONDS = 0.5` at `stream.py:19`), consumed via `createEventStream` (`web/src/lib/sse.ts:24`, used by `openRunStream` at `web/src/lib/api.ts:305`). Concrete fix: drop the worker-publish step (the worker only commits the row + `version` bump); specify the widget stream as a cursor poll over `(updated_at, version)` modeled on `runs/stream.py`, with `Last-Event-ID` replay, so the section 5 id+version swap works with no new infra.

3. **Severity: LOW. Plan section: 2 (API router route list). Violated invariant: buildable as written.** The plan lists `PATCH /widgets/{id}` and `GET /widgets/stream` without registration order. If `/{id}` (UUID) is registered first, `GET /widgets/stream` 422s on UUID parsing. Concrete fix: state that `GET /stream` is registered before `GET|PATCH /{id}` in `backend/src/modules/widgets/router.py`.

4. **Severity: LOW. Plan section: 0 ("Runs streaming precedent: `web/src/queries/runs.ts:51`"). Violated invariant: every plan claim traces to a real path:line.** Line 51 of `runs.ts` is inside a doc comment, and `runs.ts` never references `createEventStream` (only `web/src/lib/sse.ts` and `web/src/lib/api.ts` do). Concrete fix: cite the real precedent, `openRunStream` at `web/src/lib/api.ts:305` built on `createEventStream` at `web/src/lib/sse.ts:24`; the section 5 subscribe instruction is otherwise correct as written.

5. **Severity: LOW. Plan section: 7, task 5 verify (`pytest ... tests/unit/test_tool_registry.py`). Violated invariant: test plan names real suite paths.** No `test_tool_registry.py` exists under `backend/tests/unit/` (registry-adjacent coverage lives in `test_tools.py` / `test_job_dispatch.py`), and it is not unambiguously listed as a new file to create. Concrete fix: change the verify command to extend the existing `tests/unit/test_tools.py` (or explicitly list `test_tool_registry.py` as a new file to create with registry-level assertions).

## What checked out (no action)

- Anchors real: `HarkLeftRail.tsx:24/26` panels, `:186/:195` card views, `:403/:415` toggle menu; `WidgetsRail.tsx:53/61/92-95` pins + `allow-scripts` iframe; `ArtifactCard` pin key + cross-tab event (file lines 28/51, plan inherits the review's off-by-one citations, symbols real); `artifacts/data.py:8/11`; `runs/tools/__init__.py:26/32/136/179` levels, count, boundary, registry; `delivery.py:37/54` forbid idiom; `jobs/service.py:25/80/132` priorities, `_jobs` warning, claim path; `worker.py:38/48/53/104/244/259/533` retry, errors, dispatch, unwired set, idle sweep; `cards/data.py:29/30/43`; `cards/router.py:35`; `sse.ts:24`; `types/index.ts:356-359`; migration chain `0001`-`0010` with `0010 -> 0009` verified, no `widgets` table anywhere.
- Scope: P0 + P1 only, `HarkLeftRail.tsx` untouched, pins unmigrated, P2 confined to section 9 follow-ups.
- Migration: `user_id` FK `CASCADE` with per-query scoping, `widget_type` and interval CHECKs, both indexes, downgrade drops indexes then table, migrated-DB 422 test.
- Schemas: `extra="forbid"` everywhere, type-matched payload validation, 422 contract, Zod mirror + fallback card, caps match review (50 rows / 30 entries).
- Queue/worker: `"refresh_widget": 40` fits the real gap (50/20), both registration points named, 60s sweeper with dedupe, no LISTEN/NOTIFY/Redis, no raw cron, `UNWIRED_JOB_TYPES` correctly untouched (4 preexisting entries verified).
- Frontend: `Your panels` in `WidgetsRail.tsx` above pins, id+version swap, one stream per dashboard, fixed registry, no free-HTML generation.

## Re-audit trigger

Resubmit after findings 1-2 are addressed; 3-5 can ride along in the same revision.

## Re-review verdict (2026-11-12): APPROVE

Re-read `docs/agent-widgets-build-plan.md` against each finding above and the cited code this session. All 5 are fixed, no new HI/MED issues.

- Finding 1 (HI, ToolContext identity): FIXED. Plan section 3 now adds `run_id: UUID | None = None` and `user_id: UUID | None = None` to `ToolContext`, never from tool args, populates both in `RunToolExecutor.execute` via the `_run_owner_id` pattern, passes both through (or explicit `None`) at the MCP-wrapper sites, and rejects `None` with a failed `ToolResult`. Verified current code has no identity: `backend/src/modules/runs/tools/__init__.py:71` (frozen dataclass, only `runner, container_id, delivery_dir, llm, secrets`), `backend/src/dependencies.py:770` (`ToolContext(` built without identity), `backend/src/modules/runs/service.py:69` and `:86` (both wrapper sites build `ToolContext` without identity); supporting path real: `backend/src/dependencies.py:902` (`build_tool_executor` owns `run_id`), `_run_owner_id` at `backend/src/dependencies.py:728` used at `:714`.
- Finding 2 (MED, SSE publish): FIXED. Worker-publish step is gone; section 4 step 5 commits row plus `version` bump only, section 2 specifies `GET /widgets/stream` as a stateless cursor poll over `(updated_at, version)` with `Last-Event-ID` replay. Verified no publish infra exists (search over `backend/src` for publish/bus/`widget_updated`/LISTEN returns no SSE primitive) and the precedent is real: `backend/src/modules/runs/stream.py:1` (cursor read), `:19` (`POLL_INTERVAL_SECONDS = 0.5`), `:39` (`parse_last_event_id`).
- Finding 3 (LOW, route order): FIXED. Section 2 and task 4 state `GET /stream` is registered before `GET|PATCH /{id}` in the new router so `/stream` never 422s on UUID parsing.
- Finding 4 (LOW, runs precedent citation): FIXED. Section 0 cites `openRunStream` at `web/src/lib/api.ts:305` built on `createEventStream` at `web/src/lib/sse.ts:24`, with the fetch-then-subscribe merge documented in the `useRun` doc comment at `web/src/queries/runs.ts:50`. Verified all three: `web/src/lib/sse.ts:24`, `web/src/lib/api.ts:305`, `web/src/queries/runs.ts:50`.
- Finding 5 (LOW, test path): FIXED. Task 5 verify now runs `tests/unit/test_tools.py tests/unit/test_widgets_tools.py`, and section 7 line 193 marks `test_tools.py`, `test_jobs_queue.py`, `test_migrations.py` as existing suites extended in place with `test_widgets_*` as new files. Verified `backend/tests/unit/test_tools.py` and `backend/tests/unit/test_job_dispatch.py` exist and no `test_tool_registry.py` exists.

No new HI/MED: no new infra (no Redis, no LISTEN/NOTIFY, no publish primitive), queue/worker claims re-verified (`backend/src/modules/jobs/service.py:25` priorities, `backend/src/worker.py:244` handlers, `:259` unwired set of 4, `:38` retry delay, `:48`/`:53`/`:104` error paths), no `widgets` table or module exists yet (new files correctly marked as new).
