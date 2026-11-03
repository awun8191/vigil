# API Contract Sync Review, Vigil Web vs Vigil Backend

Scope: every wire type in `web/src/types/index.ts` (221 lines), every endpoint in `web/src/lib/api.ts` / `lib/contract.ts`, every query hook in `web/src/queries/`, compared against the full backend surface: 16 routers under `backend/src/modules/*/router.py`, their Pydantic DTOs in `modules/*/data.py`, and `schemas/connectors.py`.

The chat stream and run stream frame formats were read at the transport level (`frontend: lib/sse.ts`, `backend: modules/runs/stream.py, modules/chat/router.py`), not inferred from types.

Created: 2026-11-03.

| Verdict key | Meaning |
|---|---|
| SYNC | Frontend type and backend schema agree field for field |
| DRIFT | Both exist but disagree |
| FRONT ONLY | Defined in `web/src/types/index.ts`, nothing on the backend emits it |
| BACK ONLY | Backend endpoint + schema exist, no frontend type, no api method, no hook |

## Verdict per module

| Module | Verdict | Frontend coverage |
|---|---|---|
| cards | SYNC | list, dismiss, feedback |
| approvals | SYNC | list, get, decision |
| chat | SYNC (fragile) | sessions, messages, stream |
| health | SYNC | GET /health |
| auth | SYNC | pair, me, revoke |
| settings | DRIFT, see below | GET settings, GET limits only |
| connectors | BACK ONLY (partly) | list accounts, sync only |
| skills | BACK ONLY | planned in .kiro spec, not built |
| runs | DRIFT + dead path | createRun only, and it is broken end to end |
| artifacts | BACK ONLY | none |
| audit | BACK ONLY | none |
| memory | BACK ONLY | none |
| pricing | BACK ONLY | none |
| jobs | BACK ONLY | none |
| triage | BACK ONLY | none |
| compaction | BACK ONLY | none |

## Field level drift

### 1. `RunStatus.stale_thread_paused` (- frontend only, contract violation)
`web/src/types/index.ts:112` keeps `'stale_thread_paused'` in the `RunStatus` union. `backend/src/modules/runs/data.py:38-45` does not have it, and `runs/state.py:88` states it explicitly: "Decision 1 fixes this list. There is no stale_thread_paused." The `.kiro` spec Requirement 6.3 says remove it from the union. It has not been done. Any `switch` or render branch keyed on that literal is unreachable against the live backend. Remove it.

### 2. `Run` is missing `cost_micros`
Backend emits both, `runs/data.py:49-54`:
```
cost_micros: int   # source of truth
cost_usd: float    # display only
```
Frontend `Run` (types/index.ts:116-127) keeps only `cost_usd`. Add `cost_micros: number` and keep `cost_usd` for display. If the plan ever needs exact summing across runs (usage page), it must sum `cost_micros`, never floats.

### 3. Card feedback `note` is accepted and silently dropped by the backend router
Frontend sends `{ feedback, note }` (`types/index.ts:50-53`, `api.ts:177-179`). The backend schema accepts it (`cards/data.py` CardFeedbackRequest: feedback + note) but `cards/router.py:142` calls `service.record_feedback(card_id, payload.feedback)` and never forwards `payload.note`. Contract drift between the backend's own schema and its own router. Decide: persist note in cards feedback, or delete `note` from both sides. Do not leave a field the client can send and the server pretends to accept.

### 4. Card action of type `run` is guaranteed to fail (dead integration path)
`TaskCard.tsx:42-50` posts `api.createRun(action.payload, card.id, scope)` and discards the returned `Run` object in `onSuccess`. Two contract breakages:

- `RunCreateRequest` (`runs/data.py:8-12`) requires `config_id: UUID` (no default, required field). No backend entity ever synthesizes a card action containing `config_id`. The only card creator in the live path is triage (`triage/service.py:128-129`), which emits exactly two action types: `reply` (payload `{thread_id}`) and `dismiss` (payload `{}`). There is no `run` action producer anywhere in the backend. Any card button with `action_type === 'run'` posts a 422 and shows an error.
- `onSuccess` ignores `data.id`. The run id is thrown away at the exact point where "chat message to run_id bridge" would need it. No query cache entry, no subscription, nothing.

### 5. Skill toggle: spec and backend disagree on the verb
`.kiro/specs/connectors-skills-settings-ui/requirements.md` Requirement 4.3: "PATCH to /api/v1/skills/{id} with the enabled field". Backend (`skills/router.py:25-26, 75-96`) implements `POST /{skill_id}/enable` and `POST /{skill_id}/disable` only. No PATCH route exists. Same class of divergence on install: spec says "POST to /api/v1/skills", backend route is `POST /skills/install`. One of the two sides must change before hooks are written, otherwise the settings UI gets built against a verb that does not exist.

### 6. Settings is read-only in the frontend for no backend reason
Backend has `PATCH /api/v1/settings` with `UserSettingsUpdateRequest` (all four fields optional: spending cap, polling interval, approval defaults, preferred model tier), `settings/router.py`. Frontend `queries/settings.ts` has no update mutation and `SettingsPage.tsx` contains no PATCH call of any kind. The usage/spend controls the Usage section would exist to change have no mutation path. Unbuilt, not blocked.

### 7. MCP server shapes exist on the backend only
`connectors/router.py:31-61` defines `MCPServerResponse` (id, name, transport, command, url, enabled, created_at), `MCPDiscoveryResponse` (server_name, job_id, status), and post-registration returns `202` + `MCPDiscoveryResponse` with `status_code = HTTP_202_ACCEPTED` (`router.py:151`). Matches spec Requirement 6.2 which requires `MCPServerResponse`, `RegisterMCPServerRequest`, `MCPDiscoveryResponse` in `web/src/types/index.ts`. None exist. `queries/skills.ts` from Requirement 7.1 does not exist either (only `approvals.ts, cards.ts, chat.ts, settings.ts` in `web/src/queries/`).

### 8. Frontend privacy/grep note on the chat stream
`GET /chat/sessions/{id}/stream?q=<user prompt>` puts the full user prompt into a URL. That lands in server access logs, proxy logs, and browser history. The REPL-style replacement is POST `/sessions/{id}/messages` returning `StreamingResponse` (persist user message server side, stream the reply), which also removes the current double-processing ambiguity documented at length in `queries/chat.ts:54-64`. This is a wire shape decision that must be made before the ChatService stub is replaced with a real LLM, because persistence semantics change with it.

## Backend surface with no frontend face ( BACK ONLY )

| Endpoint group | Wire types needed | Frontend priority |
|---|---|---|
| `GET/POST /runs`, `GET /runs/{id}`, `/steps`, `/events` (SSE), `/cancel` | `RunCancelResponse` (`run_id, status, cancelled_at`); `RunStep` is already typed in index.ts but unused | Immediate, this is the agent surface |
| `/artifacts`, `/artifacts/{id}`, `/run/{run_id}` | `ArtifactCreateRequest`, `ArtifactResponse`, `ArtifactKind` | Run detail view |
| `/audit` (cursor page), `/settings/audit-logs` | `AuditLogResponse`, `AuditPageResponse` (items, next_cursor) | Moderately useful, exercises cursor pagination differently from cards offset pagination |
| `/memory/*` (search GET+POST, chunk get, exclude patch, delete) | `MemorySearchRequest`, `MemorySearchResultItem`, `MemorySearchResponse`, `MemoryChunkResponse`, `MemoryExcludeRequest` | Low until memory UI is on the map |
| `/pricing` | `ModelPriceResponse`, `ModelPriceUpsert` | Admin only |
| `/jobs` (stats, get, enqueue) | `EnqueueJobRequest`, `JobResponse`, `QueueStatsResponse` | Ops only |
| `/triage/*` | `TriageItemRequest`, `TriageResultResponse` | Only when manual triage trigger UI exists |
| `/compaction/*` | both request and response types | Ops only |

## Run stream frame contract ( what the frontend must consume )

From `runs/stream.py:67-89`, the frames the backend actually emits:
```
id: <step_number>          # integer, the reconnect cursor
event: <event_type>        # one of thought | tool_call | tool_result | approval_required | run_completed | run_failed
data: {"run_id": "...", "step_number": <n>, "event_type": "...", "payload": {...}, "created_at": "..."}
```
Plus `:keepalive` comment lines and a "run_completed" or "run_failed" event as the last frame, then the stream closes.

Compare chat frames: bare `data: <raw token>` with no `id:` and no event field, terminated by `data: [DONE]` (`chat/router.py:95-100`). The two streams have two different frame grammars. The frontend transport (`lib/sse.ts`) parses both correctly today because it ignores `id` and `event` on the chat stream, but the typed surface must distinguish them: a generic `RunSseFrame` with `id`, `event_type`, `data: RunStep` for runs, and the `[DONE]` sentinel handling for chat.

## Incorrect contract claim to fix

`lib/api.ts:100-103` states "The backend rejects it otherwise" ( about missing idempotency scope headers ). False against the current backend. `middlewares/idempotency.py:109-111`: "If header was not supplied, request proceeds unmanaged." The backend only manages requests that carry the header; it never enforces presence. The enforcement is purely frontend policy plus the 422/409 paths inside the middleware. The comment in `api.ts` must be corrected so nobody depends on a backend rejection that does not exist.

The envelope shape (`code, message, request_id, details`) in `ApiErrorEnvelope` matches what the backend emits in `middlewares/*` and `config/exceptions.py`, including `X-Request-ID` response headers.

## Plan

### Phase 0, contract hygiene ( mechanics, do first )
| # | Change | Where |
|---|---|---|
| 0.1 | Delete `stale_thread_paused` from `RunStatus` union | `web/src/types/index.ts:112`, spec Req 6.3 |
| 0.2 | Add `cost_micros: number` to `Run` | `types/index.ts` |
| 0.3 | Card `note`: either persist in backend ( UPDATE feedback recording ) or remove from both sides | `cards/router.py:142`, `cards/repository.py`, `types/index.ts:50-53` |
| 0.4 | Correct the false idempotency comment, decide the frontend-scoped header is policy not contract | `lib/api.ts:100-104` |
| 0.5 | Decide `StreamMessage` / `FailureClass` fate: implement 'sending'/'failed'/'replayed' rendering or delete the dead types | `types/index.ts:97-105` |

### Phase 1, run surface ( the actual agent integration )
| # | Change | Notes |
|---|---|---|
| 1.1 | Type `createRun` body properly: `RunCreateRequest { config_id, card_id?, initial_prompt? }`, method returns `Run` | `lib/contract.ts`, `lib/api.ts` |
| 1.2 | Fix the card-to-run flow: define which entity synthesizes `run` actions WITH a valid `config_id` ( triage stage 2, or a device-level default AgentConfig ). Until a producer exists, the `run` action type stays dead on cards and the button should not render | `triage/service.py`, `cards` |
| 1.3 | Capture `data.id` in `TaskCard`'s `onSuccess`, store it (cache or navigation state), show progress | `TaskCard.tsx:44-46` |
| 1.4 | Add `api.getRun`, `api.getRunSteps`, `api.cancelRun`, `api.openRunStream` with typed `RunSseFrame` events from the frame spec above | `lib/contract.ts`, `lib/api.ts`, `lib/sse.ts` |
| 1.5 | `queries/runs.ts`: `useRun(runId)` (GET + SSE merge on same key), `useRunSteps(runId, sinceStep)`, `useCancelRun` | new file |
| 1.6 | `ThinkingCapsule` consumes real step events, the `steps` prop already exists in its signature, feed it from `useRun` instead of a static array | `ThinkingCapsule.tsx:11`, `MessageStream.tsx` |
| 1.7 | Route for run detail ( or push-model: capsule + card status polling stay the only surfaces, decide ) | `routes` |

### Phase 2, settings module per .kiro spec
| # | Change | Notes |
|---|---|---|
| 2.1 | Add `SkillResponse`, `SkillInstallRequest`, `SkillToggleRequest`, `MCPServerResponse`, `RegisterMCPServerRequest`, `MCPDiscoveryResponse` to `types/index.ts` matching `skills/data.py` and `connectors/router.py` | Req 6.1, 6.2 |
| 2.2 | Resolve the PATCH vs enable-disable divergence before writing `useToggleSkill`: backend has POST enable/disable, spec says PATCH `/{id}` with `enabled`. Cheapest fix is updating the spec to the backend's POST form, since refactoring two endpoints for one boolean is not worth it | Req 4.3 vs `skills/router.py` |
| 2.3 | `queries/skills.ts` with `useSkills`, `useInstallSkill`, `useToggleSkill`; add `useMCPServers`, `useRegisterMCPServer`, `useUnregisterMCPServer` | Req 7.1, 7.2 |
| 2.4 | Skills + MCP sections in `SettingsPage` with type badges per Req 1-5 | `settings/sections.tsx` |
| 2.5 | `api.updateSettings` PATCH mutation so settings stop being read-only | backend ready already |

### Phase 3, deferred chunks
Artifacts ( run detail view dependency ), audit logs ( settings sub-page ), memory search, admin: pricing, jobs, triage manual trigger, compaction. None of these block the chat or run surfaces.

## Sequencing note
Phase 1.2 is the decision gate for the whole run surface: the backend has a real state machine, barrier, approval flow, and SSE streaming, but the frontend cannot reach any of it today because no backend entity puts a `config_id` in a card action and TaskCard throws the run id away. Without 1.2, phases 1.3-1.7 have nothing meaningful to display.
