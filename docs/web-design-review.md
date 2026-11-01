# Virgil Web Design Spec: Review Against the Actual Backend

Reviewed: `design.md` (commit b35a6d8) against `backend/src/modules/*` as built through Phase 3.
Verdict: the aesthetic direction is right and buildable. The spec has seven places where it
contradicts or silently ignores the backend, and three internal defects that will cause rework
if they ship as written.

---

## Part 1: Blocking mismatches (spec vs. real API)

### 1.1 Card archetypes do not exist in the schema

`design.md` section 5.1 defines three card types: `ACTION READY`, `GENTLE REMINDER`,
`PRE-CONFIGURED ROUTINE`. None of these map to anything the backend emits.

The real `CardResponse` (`backend/src/modules/cards/data.py:18`):

| Field | Actual domain |
|---|---|
| `type` | `email`, `calendar`, `alert` |
| `urgency` | `critical`, `high`, `medium`, `low` |
| `status` | `new`, `seen`, `in_progress`, `awaiting_approval`, `done`, `dismissed`, `expired` |
| `due_at` | datetime or null |
| `actions` | list of server-defined action buttons |
| `thread_id` | connector thread for grouping |

What the UI should actually render, derived from these fields:

- `type` drives the leading icon: `Mail` / `Calendar` / `TriangleAlert` from lucide.
- `urgency` drives a 2px leading rail or a dot, not a badge. `critical` and `high` get accent,
  `medium` and `low` stay neutral. This is the only place urgency should be visible.
- `status` drives the trailing state chip. `awaiting_approval` is the one that earns a filled
  accent chip, because it is the only status where the user is the blocker.
- `due_at` renders a relative time (`in 2h`, `overdue 40m`), not an absolute timestamp.

`PRE-CONFIGURED ROUTINE` has no backend entity at all. The closest is the `skills` module which
is a CRUD list of agent skill definitions (`GET/POST/PATCH/DELETE /skills`), not launchable
routines. Either drop that archetype from v1 or add a `routines` module. Do not build the card
against a type that will return zero rows forever.

### 1.2 Card actions are server-driven, not hard-coded

`CardActionSchema` (`cards/data.py:8`) is `{id, label, action_type, payload}` and `action_type`
is documented as `run`, `dismiss`, `reply`. The spec hard-codes `[ Review & Send ]` and
`[ Dismiss ]` as fixed pills. That inverts the contract.

Correct model:
- Render `card.actions` in order, primary = index 0.
- Dispatch on `action_type`:
  - `run` -> `POST /runs` with `payload`
  - `dismiss` -> `POST /cards/{id}/dismiss`
  - `reply` -> open chat composer seeded with `payload`
  - anything unknown -> render as a disabled ghost so new server action types degrade
    gracefully instead of crashing the card.
- Action button labels come from `label`, never from a client-side map keyed on `id`.

The `Discuss with Virgil` bridge is fine as a client concern, but it must not occupy the
primary slot, because the primary slot belongs to the server's first action.

### 1.3 Approvals are a separate module from cards

`POST /cards/{id}/feedback` takes `useful | not_useful | dismissed | spam`. That is telemetry,
not authorisation. The approve/reject path is `GET /approvals` and
`POST /approvals/{approval_id}/decision`.

The spec conflates the two under one `Review & Send` button. A card in `awaiting_approval`
status means there is a pending approval record; the card itself cannot resolve it. The UI needs
to fetch the linked approval and post the decision there, then let the card transition. If no
approval exists, the card action is not "send", it is whatever `actions[0]` says.

### 1.4 There is no push channel for cards

The web spec describes Home as a live surface ("Virgil is monitoring in the background", the
zen empty state implies it updates itself). The only SSE endpoint on the backend is
`GET /chat/sessions/{session_id}/stream`. `runs` has `GET /runs/{run_id}/events`, which is
poll-shaped, not a subscription.

So Home has three options:
1. Poll `GET /cards` on an interval plus on window focus. Cheapest, adequate at this
   throughput, requires no backend change. Recommended for v1.
2. Add `GET /cards/stream` (SSE) to the cards module.
3. Add one multiplexed `GET /events/stream` broadcasting card, approval, and run events.

Pick one and write it into the spec. Right now the spec assumes a channel that does not exist,
and a frontend built against that assumption will spend a day discovering it.

### 1.5 EventSource cannot carry the device token

Auth is `Authorization: Bearer vgt_...` (`POST /auth/pair`). Browser `EventSource` cannot set
request headers. This is a hard blocker for the chat stream as specced, and it is the single
most important omission in the document.

Options:
- **Recommended:** do not use `EventSource`. Use `fetch()` with `Authorization` and read
  `response.body.getReader()`, decoding SSE frames manually. This keeps bearer-only auth, works
  with the existing middleware, and gives you `AbortController` for cancellation and a
  `Last-Event-ID` header on resume.
- Alternative: put the device token in an httpOnly `Secure` `SameSite=Strict` cookie and let
  `EventSource` send credentials. Simpler reader, but you now need CSRF protection on every
  mutating route and the token stops being portable to the Flutter mobile client.
- Do not put the token in the query string. It lands in access logs and `Referer`.

### 1.6 `Idempotency-Key` is mandatory and the spec never mentions it

Middleware F10 rejects mutating requests without `Idempotency-Key`. Every `POST` and `PATCH`
from `services/api.ts` must generate a key (`crypto.randomUUID()` per logical user action, held
stable across retries) and replay the same key on network retry. Otherwise a flaky connection
during `Send` produces a 400, not a duplicate-safe retry.

Corollary for the UI: the send button must distinguish "network failed, safe to retry with same
key" from "server rejected, do not retry". Two different toasts, two different behaviours.

### 1.7 There is no route a first-run user can reach

`POST /auth/pair` needs a pairing code. The spec puts device pairing inside the Settings sheet.
The Settings sheet is reachable only from the dock. The dock is app chrome that only renders
once you are inside the app. With no token, every request 401s and there is no defined surface
for that state.

Needed: a `/pair` route that renders before the app shell, containing the code input and the QR
scanner affordance. Backend already gives you the code at boot (ASCII QR printer) and
`GET /api/v1/connectors/accounts` is the natural first check for "paired but unconfigured".

---

## Part 2: Internal defects in the spec

### 2.1 The mobile dock collides with the input pill

Section 4 puts the dock at bottom-centre on `< 768px`. Section 5.2 puts the floating input pill
at `bottom-8` centred. On mobile both occupy the same box. Home's cards also scroll underneath
both, so you get three overlapping floating layers.

Resolve with an explicit mobile stack, bottom to top:
1. `env(safe-area-inset-bottom)` padding.
2. Input pill (`bottom-4`).
3. Dock (`bottom-[calc(4.5rem+env(safe-area-inset-bottom))]`), or fold the dock into the input
   pill's left slot on Chat and hide it entirely.
4. Add `pb-[calc(8rem+env(safe-area-inset-bottom))]` to the scroll container so the last message
   is not permanently hidden behind the pill.

### 2.2 The motion spec contradicts itself

Section 2.3 lists `cubic-bezier(0.25, 1, 0.5, 1)` and then `stiffness 400, damping 30`. Those
are two different systems. The cubic-bezier is monotonic with no overshoot; a spring at
k=400, c=30, m=1 has damping ratio zeta = 30 / (2*sqrt(400)) = 0.75, which is underdamped and
overshoots by roughly 2.8%. They will not look the same, and picking one per-component by vibes
produces exactly the inconsistency the spec is trying to avoid.

Decide: one motion primitive. If staying pure CSS, write a `linear()` easing that approximates
the spring and use it everywhere. If using Framer Motion, delete the cubic-bezier from the tokens
and use the spring config. Do not ship both.

### 2.3 The zero-emoji rule is broken by the brand mark and the diagrams

The rule is correct and worth keeping, but `Virgil ✦` uses a Unicode glyph as an icon. `✦`
renders at different sizes and baselines across font stacks and is a real risk of tofu on Linux
boxes without a fallback. Use an inline SVG spark (or `<Sparkles />` at a tuned size) and keep
the glyph out of the DOM. The ASCII diagrams also use `⌂ 💬 ⚙ 🎙`; that is documentation, not
code, but someone will copy-paste the mockup.

### 2.4 Theme tokens are prose, not code

The palette table is unenforceable as written. Two themes across roughly fifteen components will
drift within a week. Needed before any component is written: one token block.

```css
:root {
  --surface-base: #FBF9F5;
  --surface-card: #FFFFFF;
  --hairline: rgba(0,0,0,0.05);
  --text-primary: #0F172A;
  --text-muted: #64748B;
  --accent: #0284C7;
  --r-card: 28px;
  --r-inner: calc(28px - 20px);
}
[data-theme="dark"] {
  --surface-base: #070A10;
  --surface-card: rgba(255,255,255,0.05);
  --hairline: rgba(255,255,255,0.08);
  --text-primary: #F8FAFC;
  --text-muted: #94A3B8;
  --accent: #38BDF8;
}
```

Set `data-theme` on `<html>`, not a React class on a wrapper. It avoids a flash of wrong theme
and lets `color-scheme` propagate to form controls and scrollbars.

### 2.5 Accessibility is entirely absent

Nothing in the document covers keyboard, focus, motion preference, or screen readers. For a chat
app with streaming this is not optional:

- Streaming assistant messages need `aria-live="polite"` on the message list container, and
  the thinking capsule needs `role="status"`. Without it the entire product is silent to a
  screen reader.
- `ThinkingCapsule` uses `animate-pulse`; gate it behind `motion-safe:` and honour
  `prefers-reduced-motion` for every transition, including the ambient background drift.
- The dock has three icon-only buttons. Each needs `aria-label` and `aria-current="page"`.
- `focus-visible` rings on every interactive surface. The translucent hairline borders do not
  survive as a focus indicator, and `border-white/[0.08]` on `rgba(255,255,255,0.05)` cards
  sits below the 3:1 non-text contrast floor for a UI boundary, so card edges are effectively
  invisible to low-vision users. Raise the hairline to `0.12` in dark mode or add a 1px inner
  highlight via `box-shadow: inset 0 1px 0 rgba(255,255,255,0.06)`.
- Auto-expanding textarea must not swallow `Enter` for newline; specify `Enter` sends,
  `Shift+Enter` newlines, and both must be reachable and documented in `aria-describedby`.

### 2.6 Layered backdrop blur will tank the frame budget

`backdrop-blur-2xl` on the dock, `backdrop-blur-3xl` on the settings sheet, frosted cards, plus
an animated ambient mesh behind everything, means multiple full-viewport filter passes per
frame. This is the classic cause of 20fps scrolling on mid-range Android and on Firefox under
Wayland (which your own machine runs).

Mitigation that keeps the look:
- Render the ambient mesh as a fixed, static `::before` with `filter: blur(80px)` on a small
  scaled-up element. Blur it once, do not animate the filter.
- Allow at most one `backdrop-filter` layer per viewport at a time. The dock and the sheet
  should not both be live.
- On cards, drop `backdrop-filter` entirely. `rgba(255,255,255,0.05)` over a dark base reads as
  frosted without the filter pass. Reserve real blur for the two chrome surfaces.

### 2.7 Missing states

The document specifies one empty state and no failure states. Before implementation, define:
- Chat: send failed (retry with same idempotency key), stream dropped (reconnect with
  `Last-Event-ID`), assistant errored mid-stream, empty session.
- Home: loading skeleton, load failure with retry, card count zero (the zen state exists),
  stale data indicator when polling is backing off.
- Settings: backend unreachable, DB `disabled` (health returns `database: "disabled"`, which is a
  valid state, not an error), unpaired connector.
- Global: offline banner, 401 handling that routes to `/pair` instead of looping.

### 2.8 Simulation mode needs to be build-time, not runtime

`services/mockData.ts` is good, but "standalone simulation mode works out of the box" must be
gated by an explicit flag (`VITE_VIRGIL_MOCK=1`) compiled out of production builds. Runtime
detection ("no token, show mocks") means a user who loses their token silently sees fake cards
and believes the assistant is working. That is worse than an error.

---

## Part 3: What the spec gets right, keep it

- No top navbar. The `Virgil ✦` corner mark plus a floating dock is the correct call, and it is
  what separates this from the generic dashboard every assistant ships.
- The floating hero input pill with `+ / mic / send` matches the Hark reference and is the right
  primary affordance. Auto-expanding input, waveform on record, send button lighting up: keep.
- Distinct user (boxed, right) vs. assistant (unboxed, spark-marked, left) message treatment.
  This is better than symmetric bubbles for an agent that mostly talks.
- `ThinkingCapsule` as a collapsed expandable pill instead of raw tool output. This is the whole
  product thesis in one component.
- Dual theme with a warm light mode rather than the usual dim grey. Cream `#FBF9F5` is a real
  differentiator against every navy-and-slate self-hosted tool.
- Strict lucide + `strokeWidth 1.5`. Keep the rule, fix the two violations.
- Centred `max-w-3xl` reading width with generous vertical rhythm.

---

## Part 4: Decisions needed before code

| # | Decision | Options | Recommendation |
|---|---|---|---|
| D1 | Project name | `Virgil` (repo, spec, `VGL-` codes) vs `Vigil` (your message) | Virgil. Backend already ships `VGL-` pairing codes and `virgil-agent` paths. Renaming now costs a migration; renaming later costs more. |
| D2 | Framework | Vite + React SPA, or Next.js App Router | Vite + React. The app is a single authenticated client for one user, there is no SEO surface, no server rendering need, and SSE with bearer headers is straightforward in a SPA. Next adds an edge-runtime constraint on the stream for zero benefit here. |
| D3 | Home freshness | Poll, or add SSE endpoint | Poll v1, plus on-focus refetch. Add `/events/stream` only if rule-based push becomes a requirement. |
| D4 | Token storage | bearer in memory + persisted in IndexedDB, or httpOnly cookie | Bearer, held in memory, refreshed via a stored refresh token in IndexedDB. Keeps uniformity with the Flutter client. Never `localStorage`. |
| D5 | Routines archetype | build it, or drop for v1 | Drop for v1. No backend entity. Reintroduce when a `routines` module exists. |

---

## Part 5: Suggested build order

1. Token + theme + type scale (`design/tokens.css`, `tailwind.config`, `Plus Jakarta Sans` self-hosted woff2 subset).
2. `AmbientBackground`, `HeaderBrand`, `FloatingSideDock`. Static, no data.
3. `Pair` route and `services/api.ts` with idempotency and 401 interception.
4. Chat: `MessageStream`, `MessageItem`, `FloatingInputPill`, fetch-based SSE reader, reconnect.
5. Home: `TaskCard` driven by real `CardResponse`, action dispatch table, polling.
6. `SettingsSheet` as a route-backed sheet, health badges wired to `GET /health` and `GET /settings`.
7. Passes: keyboard and screen reader, reduced motion, mobile stack, contrast audit, then perf.
