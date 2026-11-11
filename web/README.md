# Vigil Web

Vite + React 19 + TypeScript client for Vigil. Home briefing, streaming chat, and settings.
The full specification is `../design.md`.

## Run

### All-in-one (recommended)
From the repository root, start the web client, the agents API, and the worker together:
```sh
./start.sh
```

### Standalone Web Client
```sh
npm install
npm run dev          # live mode: proxies /api to 127.0.0.1:8000
```

The app requires a paired device. Pairing codes are printed by the backend as an ASCII QR on boot
(`VIG-XXXXXX`). Enter the code at `http://localhost:5173/pair`.

## Simulation mode (Mock)

```sh
npm run dev:mock     # fixture data, no backend required
```

`dev:mock` runs with fixture data and does not connect to the backend. This is a build-time switch (`VITE_VIGIL_MOCK=1`) and can
never activate accidentally at runtime: a device that loses its token in live mode will see an error, not fabricated cards.
A persistent banner marks simulation mode.

## Build

```sh
npm run build        # tsc --noEmit && vite build
npm run typecheck
```

## Notes for whoever touches this next

- The whole UI codes against `VigilApi` in `src/lib/contract.ts`. `lib/api.ts` is the live
  implementation, `lib/mock.ts` the fixture one, chosen in one place at the bottom of `api.ts`.
- Every mutating request needs an idempotency scope. `lib/api.ts` throws in development if you
  forget, because the backend rejects the request anyway.
- Chat streaming uses `fetch` plus a body reader in `lib/sse.ts`. Do not swap in `EventSource`:
  it cannot send the `Authorization` header, and putting the token in the query string leaks it
  into access logs and `Referer`.
- Card buttons come from `card.actions`. Nothing about the action set is hard-coded, and an
  unknown `action_type` renders disabled rather than crashing.
- Motion uses one easing, `--ease-spring` in `src/styles/tokens.css`. Do not introduce a second
  curve.
