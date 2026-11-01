# Vigil Web Design Specification

> Implementation contract for `web/`. This document is the single source of truth for the Vigil
> web client. It supersedes `web/design.md`, `docs/superpowers/specs/2026-11-01-web-ui-design.md`,
> and the earlier draft of this file.

Design intent: an ultra-clean, comforting, engaging surface. It rejects DevOps terminal
aesthetics and generic assistant slop in favour of the atmospheric calm of Google DeepMind, the
tactile ergonomics of Apple, and the warm, pillowy friendliness of Hark.

---

## 1. Product Surfaces

Vigil is a proactive, self-hostable personal AI assistant. The web client has three surfaces and
one gate:

| Route | Surface | Purpose |
|---|---|---|
| `/pair` | Gate | Device pairing. Renders before the app shell when no token exists. |
| `/` | Home | Proactive briefing: action cards, the approval queue, and what Vigil is watching. |
| `/chat` | Chat | Direct conversational canvas with streaming responses. |
| `/chat/:sessionId` | Chat | Deep link into a specific session. |
| `/settings` | Settings | Route-backed frosted sheet over the current surface. |

There is **no top navbar**. The top of the viewport stays airy. The only persistent brand element
is a discrete `Vigil` mark with an inline SVG spark in the upper-left margin.

Settings is a **route**, rendered as an intercepting sheet over the surface you came from. It is
not local state. This gives deep links, working browser back, and shareable URLs.

---

## 2. Stack Decision

**Vite + React 19 + TypeScript + Tailwind CSS v4 + lucide-react.**

Rejected Next.js App Router. Rationale: this is a single-user authenticated client with no SEO
surface and no server rendering requirement. Streaming responses need custom request headers
(bearer token), which is straightforward in a SPA and awkward through an edge route handler. Vite
keeps the mental model small: one origin, one build, one `index.html`.

Routing: `react-router-dom` v7 in declarative mode.

State: `zustand` for cross-surface state (session, theme, connection health), `@tanstack/react-query`
for server cache and polling. No Redux, no context soup.

Animation: CSS only, using a spring-approximating `linear()` easing (section 6). No Framer Motion
in v1. If a component genuinely needs layout animation later, that is a scoped addition, not a
default.

---

## 3. Backend Contract

The web client is built against `backend/src/modules/*` as it actually exists. Do not invent types.

### 3.1 Authentication

| Call | Shape |
|---|---|
| `POST /api/v1/auth/pair` | `{pairing_code: "VIG-XXXXXX", device_name: string}` returns `{device_token: "vgt_...", device_id, device_name}` |
| `GET /api/v1/auth/me` | `Authorization: Bearer vgt_...` returns `{device_id, device_name, created_at, last_seen_at}` |
| `POST /api/v1/auth/revoke` | invalidates the current device token |

The pairing code is printed as an ASCII QR on backend boot. The web client accepts it as text or
scans it.

### 3.2 Cards (the real schema)

`CardResponse` from `backend/src/modules/cards/data.py`:

```ts
type Urgency = 'critical' | 'high' | 'medium' | 'low';
type CardStatus = 'new' | 'seen' | 'in_progress' | 'awaiting_approval' | 'done' | 'dismissed' | 'expired';

interface CardAction {
  id: string;
  label: string;
  action_type: 'run' | 'dismiss' | 'reply' | string;
  payload: Record<string, unknown>;
}

interface Card {
  id: string;
  user_id: string;
  item_id: string | null;
  thread_id: string | null;
  type: string;              // 'email' | 'calendar' | 'alert'
  urgency: Urgency;
  title: string;
  summary: string | null;
  due_at: string | null;     // ISO datetime
  status: CardStatus;
  actions: CardAction[];
  feedback: string | null;
  created_at: string;
  updated_at: string;
}
```

Card list responses are `{items, total, limit, offset}`.

There is no `ACTION READY` / `GENTLE REMINDER` / `PRE-CONFIGURED ROUTINE` taxonomy. Those were
invented and have no backend entity. Card visual affordances derive from the real fields:

| Field | Renders as |
|---|---|
| `type` | Leading lucide icon: `Mail`, `Calendar`, `TriangleAlert`. Unknown types fall back to `Circle`. |
| `urgency` | A 3px leading rail on the card, plus a dot for `critical`. Not a badge, not a colour wash. `critical`/`high` use accent, `medium`/`low` stay neutral. |
| `status` | Trailing state chip. Only `awaiting_approval` earns a filled accent chip, because it is the one status where the user is the blocker. |
| `due_at` | Relative time (`in 2h`, `overdue 40m`), computed client-side, `title` attribute holds the absolute timestamp. |
| `actions[]` | Buttons, rendered in server order. Index 0 is primary. |

### 3.3 Card actions are server-driven

The client never decides what a card's buttons are. Rules:

- Render `card.actions` in order. Never hard-code "Review & Send" or "Dismiss".
- Dispatch on `action_type`:

| `action_type` | Client behaviour |
|---|---|
| `run` | `POST /api/v1/runs` with `{...payload, card_id}`, then navigate to the run's progress view |
| `dismiss` | `POST /api/v1/cards/{id}/dismiss`, optimistic removal |
| `reply` | Navigate to `/chat` with the composer seeded from `payload` |
| anything else | Render as a disabled ghost button so new server action types degrade instead of crashing |

`POST /api/v1/cards/{id}/feedback` takes `{feedback: 'useful' | 'not_useful' | 'dismissed' | 'spam', note?}`.
That is telemetry, not authorisation. It belongs behind an overflow menu, never as the primary button.

### 3.4 Approvals are a separate module

Authorisation lives in `approvals`, not `cards`:

| Call | Shape |
|---|---|
| `GET /api/v1/approvals` | pending approvals |
| `GET /api/v1/approvals/{approval_id}` | one approval with its preview |
| `POST /api/v1/approvals/{approval_id}/decision` | body `{decision: "approved" | "rejected", decision_note?}`, returns `{approval_id, status, run_id, resumed}` |

`RunCreateRequest` requires `config_id`, so the card's `run` payload must carry one. The client
never invents a `config_id`: if the payload lacks it the backend rejects the request and the UI
surfaces that message.

A card in `awaiting_approval` has a linked approval record. The card cannot resolve itself. The
UI fetches the approval, shows the preview, posts the decision to `/approvals/{id}/decision`, and
lets the card transition on the next poll or refetch.

### 3.5 Chat

| Call | Shape |
|---|---|
| `GET /api/v1/chat/sessions` | `ChatSession[]`, keyed by `session_id` (not `id`) |
| `POST /api/v1/chat/sessions` | body `{title?}`, 201, returns `ChatSession` |
| `GET /api/v1/chat/sessions/{id}/messages` | `ChatMessage[]` (`id`, `role`, `content`, `created_at`) |
| `POST /api/v1/chat/sessions/{id}/messages` | body `{content}`, returns `ChatMessage` |
| `GET /api/v1/chat/sessions/{id}/stream?q=<prompt>` | `text/event-stream` |

**The stream carries the prompt in `q`.** It emits bare `data: <token>` frames with no event names
and no event ids, terminated by `data: [DONE]`. Because the prompt travels in the query string,
the client must **not** also POST the same text to `/messages`: that would process the turn twice.
Sending therefore means appending the user message locally, then opening the stream.

`Last-Event-ID` resume is inert until the backend emits ids. Reconnects still work through the
backoff in `lib/sse.ts`.

### 3.6 Home freshness: poll, do not imagine a push channel

There is no `/cards/stream`. The only SSE endpoints are the chat stream and `GET /runs/{id}/events`.
Home therefore uses `react-query` with `refetchInterval: 60_000`, `refetchIntervalInBackground: false`,
and `refetchOnWindowFocus: true`. Show a stale indicator when the last successful poll is older
than 3 minutes. If rule-based push becomes a requirement, the backend grows `/api/v1/events/stream`
and this section is amended.

### 3.7 Idempotency is mandatory

`IdempotencyMiddleware` rejects mutating requests without an `Idempotency-Key` header. Every
`POST`, `PATCH`, `PUT`, and `DELETE` from `lib/api.ts` must:

1. Generate `crypto.randomUUID()` per **logical user action**, not per attempt.
2. Hold that key stable across retries of the same action.
3. Replay the identical key on network retry so a dropped connection cannot double-send.

The UI must distinguish three failure classes and never merge them:

| Class | Meaning | UI |
|---|---|---|
| Network or timeout | Request may have landed | "Retry" reusing the same key |
| Server 4xx | Rejected, did not land | Show `error.message`, do not auto-retry |
| Server 409 | Idempotent replay of a completed action | Treat as success, refetch |

### 3.8 Health and settings

| Call | Shape |
|---|---|
| `GET /api/v1/health` | `{status: "ok", version, database: "connected" | "disabled"}` |
| `GET /api/v1/settings` | current settings |
| `GET /api/v1/settings/limits` | plan limits |
| `GET /api/v1/connectors/accounts` | linked accounts |

`database: "disabled"` is a **valid state**, not an error. Render it as a neutral amber badge,
never as a failure.

---

## 4. Design Tokens

Tokens are CSS custom properties set on `<html data-theme="...">`. Set the attribute in a small
inline script in `index.html` before paint, so there is no flash of the wrong theme. Never scope
the theme class to a React wrapper.

```css
:root {
  color-scheme: light;

  --surface-base: #FBF9F5;
  --surface-raised: #FFFFFF;
  --surface-sunken: #F4F1EA;
  --surface-card: #FFFFFF;
  --surface-chrome: rgba(255, 255, 255, 0.72);

  --hairline: rgba(15, 23, 42, 0.07);
  --hairline-strong: rgba(15, 23, 42, 0.12);

  --text-primary: #0F172A;
  --text-secondary: #475569;
  --text-muted: #64748B;
  --text-inverse: #FFFFFF;

  --accent: #0284C7;
  --accent-hover: #0369A1;
  --accent-soft: rgba(2, 132, 199, 0.10);
  --accent-glow: rgba(2, 132, 199, 0.22);

  --urgency-critical: #DC2626;
  --urgency-high: #EA580C;
  --urgency-medium: #64748B;
  --urgency-low: #94A3B8;

  --r-card: 28px;
  --r-inner: 12px;
  --r-control: 14px;
  --r-pill: 999px;

  --shadow-card: 0 1px 2px rgba(15, 23, 42, 0.04), 0 12px 32px -12px rgba(15, 23, 42, 0.10);
  --shadow-chrome: 0 8px 40px -8px rgba(15, 23, 42, 0.18);

  --ease-spring: linear(0, 0.006, 0.025 2.8%, 0.101 6.1%, 0.539 18.9%, 0.721 23.4%,
    0.849 28%, 0.937 32.6%, 0.995 37.6%, 1.019 42.2%, 1.026 47.1%, 1.021 54%,
    1.001 68.8%, 0.996 82.5%, 1);
  --dur-fast: 120ms;
  --dur-base: 220ms;
  --dur-slow: 380ms;
}

[data-theme="dark"] {
  color-scheme: dark;

  --surface-base: #070A10;
  --surface-raised: #0D1320;
  --surface-sunken: #05080D;
  --surface-card: #101828;
  --surface-chrome: rgba(13, 19, 32, 0.72);

  --hairline: rgba(255, 255, 255, 0.12);
  --hairline-strong: rgba(255, 255, 255, 0.18);

  --text-primary: #F8FAFC;
  --text-secondary: #CBD5E1;
  --text-muted: #94A3B8;
  --text-inverse: #070A10;

  --accent: #38BDF8;
  --accent-hover: #7DD3FC;
  --accent-soft: rgba(56, 189, 248, 0.12);
  --accent-glow: rgba(56, 189, 248, 0.28);

  --urgency-critical: #F87171;
  --urgency-high: #FB923C;
  --urgency-medium: #94A3B8;
  --urgency-low: #64748B;

  --shadow-card: 0 1px 0 rgba(255, 255, 255, 0.04) inset, 0 16px 40px -16px rgba(0, 0, 0, 0.7);
  --shadow-chrome: 0 12px 48px -12px rgba(0, 0, 0, 0.8);
}
```

**Contrast floor.** Dark-mode content cards use a solid `#101828` with `--hairline` at `0.12`, not
`rgba(255,255,255,0.05)` with a `0.08` edge. The translucent version measures under the 3:1
non-text contrast requirement, which makes card boundaries invisible to low-vision users. The
frosted look is achieved with `--surface-chrome` on the two chrome surfaces only, never on content
cards.

---

## 5. Typography

`Plus Jakarta Sans`, self-hosted as `woff2` subsets in `web/public/fonts/`. Four files, weights
400/500/600/700, `font-display: swap`, latin `unicode-range` only. No Google Fonts request, this is
a self-hostable product.

```
--text-display:  clamp(1.75rem, 1.2rem + 2vw, 2.5rem)   /* 28-40px, -0.02em */
--text-title:    1.375rem                                /* 22px, 600, -0.015em */
--text-body:     0.9375rem                               /* 15px, 400, line-height 1.65 */
--text-small:    0.8125rem                               /* 13px, 500 */
--text-meta:     0.75rem                                 /* 12px, 500, 0.04em, uppercase for tags */
```

Tone: human, calm, reassuring, concise. Sentence case everywhere except small uppercase metadata
tags (`AWAITING APPROVAL`, `36 MIN AGO`).

---

## 6. Geometry and Motion

### 6.1 Geometry

- Cards: `border-radius: var(--r-card)` (28px).
- Nested children use `var(--r-inner)`, the outer radius minus the padding, so curves stay
  concentric. Never nest two 28px radii.
- Controls: `var(--r-control)`. Pills, docks, avatars: `var(--r-pill)`.
- Ambient shadows only. No hard 1px borders on cards. The hairline is a subtle inset highlight.

### 6.2 Motion, one primitive

**`--ease-spring` is the only easing in the application.** It is a CSS `linear()` approximation of a
spring at stiffness 400, damping 30, mass 1 (damping ratio 0.75, roughly 2.8 percent overshoot).

Do not add `cubic-bezier(0.25, 1, 0.5, 1)`. It is monotonic with no overshoot and will not match
spring-driven components, which is exactly the inconsistency this section exists to prevent.

Durations: `--dur-fast` for hover and press, `--dur-base` for surface transitions, `--dur-slow` for
the settings sheet. Animate transforms and opacity only. Never animate `height`, `width`, `top`, or
`filter`.

All of it sits behind `@media (prefers-reduced-motion: no-preference)`. Under `reduce`, transitions
collapse to `1ms` and the ambient background stops drifting entirely.

---

## 7. Iconography

**Zero emoji as icons.** Every icon comes from `lucide-react` at `strokeWidth={1.5}`
(`1.75` for filled actions), sized 16/18/20.

The brand spark is an **inline SVG**, not the `✦` character. A Unicode glyph renders at different
sizes and baselines per font stack and is a real risk of tofu on Linux systems without coverage.
`components/layout/SparkMark.tsx` exports a 4-point spark path.

| Surface | Icon | Stroke | Size |
|---|---|---|---|
| Home nav | `Home` | 1.5 | 18 |
| Chat nav | `MessageSquare` | 1.5 | 18 |
| Settings nav | `Settings` | 1.5 | 18 |
| Attach | `Plus` | 1.5 | 18 |
| Voice | `Mic` | 1.5 | 18 |
| Send | `ArrowUp` | 1.75 | 18 |
| Stop stream | `Square` | 1.75 | 16 |
| Approve | `Check` | 1.75 | 16 |
| Dismiss | `X` | 1.5 | 16 |
| Card type: email | `Mail` | 1.5 | 18 |
| Card type: calendar | `Calendar` | 1.5 | 18 |
| Card type: alert | `TriangleAlert` | 1.5 | 18 |
| Approval blocking | `ShieldCheck` | 1.5 | 16 |
| Due time | `Clock` | 1.5 | 14 |
| Run or automation | `Zap` | 1.5 | 16 |
| Thinking | `Sparkles` | 1.5 | 14 |
| Context bridge | `ArrowUpRight` | 1.5 | 14 |
| Retry | `RotateCw` | 1.5 | 14 |
| Theme light | `Sun` | 1.5 | 16 |
| Theme dark | `Moon` | 1.5 | 16 |
| Pairing | `Smartphone` | 1.5 | 16 |
| Expand or collapse | `ChevronDown` | 1.5 | 14 |

---

## 8. Navigation: Hovering Side Pill Dock

A compact pill floating detached from every edge.

- **Desktop (>= 768px):** fixed at `left-6`, vertically centred (`top-1/2 -translate-y-1/2`),
  vertical column of three icons.
- **Mobile (< 768px):** bottom centre, horizontal. **Stacked above the input pill, never
  overlapping it.** See 11.4 for the exact stack.
- **Surface:** `--surface-chrome` plus `backdrop-blur-2xl`, `1px solid var(--hairline)`,
  `border-radius: var(--r-pill)`, `box-shadow: var(--shadow-chrome)`.
- **Active item:** a spring capsule behind the icon, `--accent-soft` fill, `--accent` icon, plus a
  2px `--accent` dot beneath on mobile.
- Each button: `aria-label`, `aria-current="page"` when active, and a `focus-visible` ring of
  `0 0 0 2px var(--surface-base), 0 0 0 4px var(--accent)`.

Three items only: Home, Chat, Settings.

---

## 9. The Pairing Gate (`/pair`)

The app shell must never render unauthenticated. Routing rule:

```
no token in memory  ->  redirect to /pair, preserving the intended destination
token present       ->  proceed
401 from any call   ->  clear token, redirect to /pair, do not loop
```

`/pair` is its own blank surface: ambient background, brand mark, and one card containing:

- A 6-character code input, auto-uppercasing and formatting to `VIG-XXXXXX` as the user types.
- A "Scan QR" affordance opening the device camera (`BarcodeDetector` where available, a clear
  fallback message where not).
- A device name field, defaulted from the user agent platform.
- Submit posts to `/auth/pair`, stores the token, then routes to `/`.

Errors: an unrecognised code (401) shows "That code was not recognised. It expires 10 minutes after
boot." Never show a raw backend error string on this surface.

Token storage: **in memory for the session, persisted in IndexedDB** so a reload does not force
re-pairing. Never `localStorage`, never a query string, never a cookie in v1. `POST /auth/revoke`
deletes the record.

---

## 10. Home View

The calm morning deck. No dashboard feel.

### 10.1 Header

A single greeting line at `--text-display`, weight 600: time-aware (`Good morning`, `Good evening`),
followed by a muted sentence stating what Vigil is watching. No count badges, no KPI tiles.

### 10.2 Card stack

Ordered by urgency rank, then `due_at` ascending, then `created_at` descending. Grouped under
`Today`, `Tomorrow`, `Later`, `No date`.

Layout: single column, `max-w-[680px]`, centred, `gap-3`. Two columns only at `>= 1280px` and only
when there are more than six cards.

### 10.3 Card anatomy

```
+------------------------------------------------------------------+
| | [icon]  Title                                     [state chip]  |
| |         Summary line, two lines max, muted                     |
| |         [Clock] due in 2h        [primary] [ghost] [...]       |
+------------------------------------------------------------------+
  ^ 3px urgency rail
```

- Title: `--text-title`, weight 600, max two lines, `text-wrap: balance`.
- Summary: `--text-body`, `--text-muted`, clamped to two lines, expands in place.
- Actions: from `card.actions`. Primary is a filled accent pill, the rest are ghost pills. More
  than two collapse the excess into an overflow menu.
- The `Discuss in Chat` bridge (`ArrowUpRight`, 14px) sits in the footer, never in the primary slot.
- The card is an `<article>`. Only interactive elements are focusable; the card itself is not a
  button.

### 10.4 Empty state

When every group is empty: centred, generous vertical space, a muted spark mark, the line "You're
all caught up. Vigil is still watching.", and the last poll time. No illustration, no CTA clutter.

### 10.5 Loading and failure

- Initial load: three skeleton cards at matching geometry, `animate-pulse` gated by `motion-safe`.
- Load failure: inline banner at the top of the stack, "Couldn't reach Vigil", `Retry` (`RotateCw`).
  Never a full-page error.
- Stale: muted `Last updated 4m ago` under the header when polling has not succeeded recently.
  Clicking it forces a refetch.

---

## 11. Chat View

### 11.1 Stream

- `max-w-3xl` centred, `pt-24`, bottom padding per 11.4.
- **User messages:** right-aligned, `--surface-raised`, `1px solid var(--hairline)`,
  `rounded-[20px]`, `max-w-[85%]`.
- **Vigil messages:** left, unboxed typography, heralded by the inline SVG spark at 14px in
  `--accent`. No bubble, no border. Markdown rendered through a hardened renderer, code blocks in a
  `--surface-sunken` panel.
- Timestamps on hover only, `--text-meta`, muted.
- Auto-scroll pins to the bottom only when the user is already within 80px of it. Never yank a user
  who has scrolled up.

### 11.2 Thinking capsule

While a run is active, render a compact frosted capsule in the position the response will occupy:

```
[spark] Scanning calendar and inbox...            [ChevronDown]
```

- `Sparkles` at 14px in `--accent`, `animate-pulse` under `motion-safe` only.
- Expands to a step list (`GET /runs/{run_id}/steps`) with per-step duration. Tool output is never
  dumped raw; each step links to the run detail view.
- `role="status"`, `aria-live="polite"`.

### 11.3 The floating input pill

Suspended bottom-centre, `max-w-2xl w-full`.

```
+------------------------------------------------------------------+
|  (+)  Ask Vigil anything, or assign a task...         (mic) (up) |
+------------------------------------------------------------------+
```

- `+` opens an attachment sheet. Files post as artifacts (`POST /artifacts`).
- Auto-expanding textarea, 1 to 6 rows. `Enter` sends, `Shift+Enter` inserts a newline. The
  contract is stated in `aria-describedby`.
- Mic morphs into a 5-bar `--accent` waveform while recording. `Escape` cancels.
- Send is a circular button, `--accent` fill when content exists, `--text-muted` disabled otherwise.
- While streaming, send becomes `Square` (stop), aborting the reader and posting cancel.

### 11.4 The mobile stack, no overlaps

Mobile is where the earlier draft broke: both the dock and the input pill sat bottom-centre at the
same offset, producing three overlapping floating layers.

Fixed stack, bottom to top:

```
1. safe area   padding-bottom: env(safe-area-inset-bottom)
2. input pill  bottom: calc(1rem + env(safe-area-inset-bottom))
3. dock        bottom: calc(5.5rem + env(safe-area-inset-bottom))
4. scroll      padding-bottom: calc(11rem + env(safe-area-inset-bottom))
```

On Chat under 768px the dock may instead fold into the input pill's left edge, in which case item 3
is omitted and item 4 drops to `calc(7rem + env(safe-area-inset-bottom))`.

### 11.5 Streaming transport

`EventSource` cannot send an `Authorization` header. Do not use it. Use `fetch` and read the body
stream:

```ts
const res = await fetch(url, {
  headers: { Authorization: `Bearer ${token}`, Accept: 'text/event-stream' },
  signal: controller.signal,
});
const reader = res.body!.pipeThrough(new TextDecoderStream()).getReader();
// buffer until a blank line, then dispatch each `event:` / `data:` frame
```

Requirements:

- Send `Last-Event-ID` on reconnect when the server supplies event ids.
- Exponential backoff: 500ms, 1s, 2s, 4s, capped at 15s, max 6 attempts, then a manual `Reconnect`
  button.
- Abort on unmount and on session change. Guard against a stale reader writing into a new session.
- Never place the token in a query string. It lands in access logs and `Referer`.

### 11.6 Chat states

| State | UI |
|---|---|
| Empty session | Centred spark, "What are we working on?", three suggested prompts from recent card titles |
| Sending | User message appears optimistically with a muted clock icon |
| Send failed, network | Message marked, `Retry` reusing the same idempotency key |
| Send failed, 4xx | Inline error under the message with the server `message` field |
| Stream dropped | Muted "Reconnecting" banner, auto backoff, then a manual button |
| Stream error mid-message | Partial text kept, error chip appended, `Retry` available |
| Session 404 | "This conversation no longer exists" plus a link back to the session list |

---

## 12. Settings Sheet (`/settings`)

Route-backed intercepting sheet. `rounded-[32px]`, `--surface-chrome`, `backdrop-blur-3xl`,
`1px solid var(--hairline)`. On mobile it becomes a bottom sheet at 92vh. Closes on `Escape`,
backdrop click, and browser back.

Sections:

1. **Appearance.** Two-pill toggle, `Sun` Cream and `Moon` Navy. Persisted to `localStorage` under
   `vigil.theme`; theme is not sensitive.
2. **This device.** From `GET /auth/me`: device name, paired date, last seen. `Revoke` with a
   confirm step.
3. **Connectors.** From `GET /connectors/accounts`. Each shows status and a `Sync` action
   (`POST /connectors/accounts/{id}/sync`).
4. **Health.** Three live badges: `GET /health` status, `database` (`connected` or `disabled`, and
   `disabled` renders neutral amber, never red), and the chat stream connection state.
5. **Limits.** From `GET /settings/limits`.

---

## 13. State and Data Flow

```
types/index.ts          Card, CardAction, Approval, ChatMessage, Session, ThinkingStep, Health, Theme
lib/api.ts              fetch wrapper: bearer, Idempotency-Key, 401 interception, error normalisation
lib/sse.ts              fetch-based SSE reader with reconnect and Last-Event-ID
lib/idempotency.ts      key generation and per-action memoisation
store/useSession.ts     token (memory), device, authed state
store/useTheme.ts       theme, persisted to localStorage
queries/cards.ts        react-query, 60s poll, focus refetch
queries/chat.ts         sessions, messages, stream subscription
```

`lib/api.ts` contract:

```ts
class ApiError extends Error {
  code: string;
  message: string;
  requestId: string;
  details: unknown;
  status: number;
}
```

It normalises the backend's `{code, message, request_id, details}` envelope and reads `X-Request-ID`
for support references. Auth failures surface as a distinct `AuthError` so the router can redirect
exactly once.

---

## 14. Accessibility (required, not optional)

This section gates merge, not a nice-to-have.

- **Live regions.** The message list container sets `aria-live="polite"` and
  `aria-relevant="additions text"`. The thinking capsule sets `role="status"`. Without these the
  product is silent to a screen reader.
- **Focus.** `:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }` on every
  interactive element. Never removed. The translucent hairline is not a focus indicator.
- **Keyboard.** Full traversal. `Escape` closes the sheet and cancels recording. `Tab` never enters
  an inert sheet. Focus is trapped in the sheet and restored to its trigger on close. A skip link
  targets the main region.
- **Reduced motion.** All transitions behind `prefers-reduced-motion: no-preference`. The ambient
  mesh is static when reduced.
- **Contrast.** Body text >= 4.5:1 on its surface, UI boundaries and icons >= 3:1. Dark-mode
  `--hairline` at `0.12` satisfies this; `0.08` does not.
- **Semantics.** Cards are `<article>` with `aria-labelledby` on the title. The dock is `<nav>` with
  `aria-label="Primary"`. Icon-only buttons all carry `aria-label`.
- **Textarea help.** `aria-describedby` states "Enter to send, Shift plus Enter for a new line".
- **Colour is never the only signal.** Urgency uses a rail plus text in the accessible name, not hue
  alone.

---

## 15. Performance Budget

The earlier draft stacked `backdrop-blur-2xl` on the dock, `backdrop-blur-3xl` on the sheet, blur on
every card, and an animated mesh behind all of it. That is multiple full-viewport filter passes per
frame and is the standard cause of 20fps scrolling on mid-range Android and on Firefox under Wayland.

Rules:

- The ambient mesh is a **static** fixed `::before`: two radial gradients, `filter: blur(80px)`,
  `transform: scale(1.15)`, `will-change: auto`. It does not animate except under `motion-safe` at a
  very low opacity rate, and never by animating the filter.
- **At most one `backdrop-filter` layer per viewport.** Dock and sheet are never both live; opening
  the sheet suppresses the dock's blur.
- **No `backdrop-filter` on content cards.** A solid `--surface-card` with a hairline reads as
  elevated without the filter pass.
- Fonts: woff2 subsets, preloaded, `font-display: swap`, latin only.
- Bundle: route-level `React.lazy` for `/settings`. Initial JS under 180KB gzipped.
- Icons: named imports only. Never `import * as Icons`.
- Target: Lighthouse desktop performance >= 95, no long task over 50ms during scroll.

---

## 16. Simulation Mode

`VITE_VIGIL_MOCK=1` at build time swaps `lib/api.ts` for a fixture-backed implementation so the UI
runs with zero backend.

This is **build-time only**. Runtime detection ("no token, so show mocks") is forbidden: a user who
loses their token would silently see fabricated cards and believe the assistant is working. That is
worse than an error. When mock mode is active, a persistent banner reads `Simulation mode, no backend`.

---

## 17. File Layout

```
web/
├── index.html                     # theme bootstrap script, font preloads
├── vite.config.ts
├── tailwind.config.ts
├── src/
│   ├── main.tsx
│   ├── App.tsx                    # router, providers, auth gate
│   ├── styles/tokens.css
│   ├── types/index.ts
│   ├── lib/
│   │   ├── api.ts
│   │   ├── sse.ts
│   │   ├── idempotency.ts
│   │   ├── errors.ts
│   │   └── mock.ts
│   ├── store/{useSession,useTheme}.ts
│   ├── queries/{cards,chat,approvals,health}.ts
│   ├── routes/
│   │   ├── PairRoute.tsx
│   │   ├── HomeRoute.tsx
│   │   ├── ChatRoute.tsx
│   │   └── SettingsRoute.tsx
│   └── components/
│       ├── layout/{AmbientBackground,BrandMark,FloatingDock,SparkMark}.tsx
│       ├── home/{BriefingHeader,CardStack,CardItem,CardActions,EmptyState,SkeletonCard}.tsx
│       ├── chat/{MessageStream,MessageItem,ThinkingCapsule,InputPill,StreamBanner}.tsx
│       ├── settings/SettingsSheet.tsx
│       └── ui/{Pill,IconButton,StateChip,UrgencyRail,Sheet}.tsx
```

---

## 18. Verification Checklist

- [ ] Zero emoji in UI code. All icons from `lucide-react`. Brand spark is inline SVG.
- [ ] Only one easing in the codebase (`--ease-spring`). No stray `cubic-bezier`.
- [ ] Tokens live in `styles/tokens.css` with `:root` and `[data-theme="dark"]`. No hard-coded hex in components.
- [ ] Every `POST`/`PATCH` sends `Idempotency-Key`, stable across retries.
- [ ] `/pair` renders before the shell; a 401 redirects there once and does not loop.
- [ ] Chat stream uses `fetch` plus a reader, not `EventSource`. Reconnect with backoff and `Last-Event-ID`.
- [ ] Card buttons render from `card.actions`, primary is index 0, unknown types degrade to disabled.
- [ ] Approvals post to `/approvals/{id}/decision`, not `/cards/{id}/feedback`.
- [ ] Home polls at 60s with focus refetch. No invented push channel.
- [ ] Mobile: dock, input pill, and scroll padding follow the 11.4 stack. Nothing overlaps at 375px.
- [ ] `aria-live="polite"` on the message list, `role="status"` on the thinking capsule.
- [ ] One `backdrop-filter` per viewport. No blur on content cards.
- [ ] `prefers-reduced-motion` honoured everywhere, including the ambient mesh.
- [ ] Mock mode is build-time only and shows a persistent banner.
- [ ] Lighthouse desktop performance >= 95.
