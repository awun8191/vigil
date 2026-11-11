# Cross-Stack Multi-Wave Execution

## Pattern Description

When work spans **multiple independent tech stacks** (backend Python/FastAPI, frontend React/TypeScript, documentation, DevOps configs) and the total task count exceeds `max_concurrent_children` (typically 3), use **stack-aware wave batching** to maximize parallelism while respecting concurrency limits.

## Wave Strategy Template

```
WAVE 1: Backend/core (3 tasks)
  ├── Task A: Backend bug fixes (Python source files)
  ├── Task B: Backend middleware/performance (new files)
  └── Task C: Backend health/infra (startup, monitoring)

→ After Wave 1 completes, run `pytest` to verify no regression

WAVE 2: Frontend + adjacent (3 tasks)
  ├── Task D: Frontend SEO/meta (index.html, JSON-LD, structured data)
  ├── Task E: Frontend components (ErrorBoundary, NotFound, lazy routes)
  └── Task F: Validation improvements (Pydantic schemas, API input checks)

→ After Wave 2 completes, run backend tests + ts-check

WAVE 3: Polish + docs (3 tasks)
  ├── Task G: Mobile responsiveness (CSS audit, breakpoints, touch targets)
  ├── Task H: Build tooling (Vite config, code splitting, base path)
  └── Task I: Documentation (README rewrite, AGENTS.md update)

→ Final verification: rebuild frontend, run full test suite
```

## Decision Matrix

Use this to decide which tasks go in which wave:

| Wave | Tasks | Rationale | Verification Gate |
|------|-------|-----------|-------------------|
| **1: Foundation** | Backend logic, models, middleware, DB | Core correctness must pass before anything that depends on API responses | `pytest` all pass |
| **2: Consumer layer** | Frontend components, validation schemas, UI errors | Depends on knowing API shape from Wave 1; validates at the Pydantic boundary | Tests + tsc |
| **3: Polish** | CSS, build config, docs, deployment | No code dependency — pure quality of life; can run after all functional work | Build succeeds, lint passes |

## Key Principles

### 1. Stack grouping beats random batching
Put all backend tasks in one wave and all frontend tasks in another, even if a backend task and a frontend task are technically independent. This keeps each subagent's mental model coherent — they stay within one framework/language per task.

### 2. Each wave has a verification gate
```
Wave 1 → pytest
Wave 2 → pytest + tsc --noEmit
Wave 3 → npm run build + pytest (full regression)
```
Verifying after each wave catches regressions early, preventing the "Wave 2 broke something from Wave 1" cascade.

### 3. Context density per task
Each task in the `delegate_task` array must include:
- **Full file paths** (not just filenames — disambiguates backend/ from frontend/)
- **What to read first** — tell the subagent which file(s) establish context
- **The exact goal** — one sentence on what success looks like
- **Tools allowed** — `["file", "terminal"]` or `["search_files"]` as appropriate

### 4. Verify after every wave, not just at the end
Run a lightweight gate after each wave (tests, type-check, or build). If Wave 2 breaks something Wave 1 built, you want to know before Wave 3 starts piling on.

## Real Example (from AWUN project)

```
Total tasks: 9 (3 backend, 4 frontend, 2 docs/config)

WAVE 1 (3 tasks — backend):
  - Fix error messages in search route
  - Add gzip + cache-control + body-size middleware
  - Add ChromaDB startup health check

  → Verification: 121 pytest passed

WAVE 2 (3 tasks — frontend + validation):
  - SEO meta tags + JSON-LD + Open Graph in index.html
  - ErrorBoundary + NotFoundPage + React.lazy() routes
  - Input validation on API routes (Pydantic + handlers)

  → Verification: tsc --noEmit clean, pytest 121 passed

WAVE 3 (3 tasks — polish):
  - Mobile CSS audit (7 files: sidebar collapse, grid breakpoints, touch targets)
  - Vite config fix (base path, manualChunks function syntax)
  - README rewrite + AGENTS.md patches

  → Final verification: npm run build (1.44s), pytest 121 passed
```

## Pitfalls

- **Overloading Wave 1**: Resist putting 5+ backend tasks in Wave 1 just because they're all backend. The concurrency limit is 3 — exceed it and the tool errors. Split into back-to-back waves if truly needed.
- **Hidden dependencies**: A "docs" task that needs to reference final API specs is a Wave 3 task, not Wave 1. Read the files your task depends on first.
- **Task-level vs wave-level verification**: Subagents report their individual task results, but YOU must run the cross-cutting verification (full test suite, full build) after each wave.
