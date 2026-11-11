# AWUN Wave 1 — May 2026

Concrete example of the Issue-Driven PM Loop pattern. Three P3 issues delegated in parallel across backend services, API layer, and frontend.

## Issues Selected

| Issue | Priority | Domain | Success |
|-------|----------|--------|---------|
| AWU-27: Backend Polish | P3 | Backend Python | ✅ Complete (40 tests pass) |
| AWU-25: Standardize API errors | P3 | API Middleware | ⚠️ Timed out, but 50 tests pass (work done) |
| AWU-24: Frontend cleanup | P3 | React TypeScript | ⚠️ Timed out, build verified (work done) |

## Task Contexts (the good)

Each task included:
- Project root path
- Specific file paths to read first
- Exact verification commands
- Expected output format

Example (AWU-27):
```
Context: "Project: /home/hermes/Documents/Projects/AWUN.
Read BACKEND/src/services/storage_service.py first, then other services.
Check for ThreadPoolExecutor shutdown, PIL Image.close(), httpx client cleanup.
Use .venv/bin/python. Run tests: cd BACKEND && .venv/bin/pytest tests/test_storage.py tests/test_health.py -v"
```

## What Worked

- **Domain separation**: backend vs API vs frontend = zero merge conflicts
- **Verification commands**: each sub-agent knew exactly how to verify
- **Parent review**: after timeout, parent checked file timestamps, ran tests/build directly
- **Linear closures**: all three closed with comments within 2 minutes of verification

## What Failed: 2/3 tasks timed out at 600s

Root cause: tasks AWU-25 and AWU-24 were too broad ("review and fix everything in this area"). Sub-agents spent 30+ API calls exploring/reading before editing, hitting the timeout.

AWU-27 succeeded because the task was narrower ("fix specific classes of leaks in 3 files").

## Lessons

1. **Scope matters**: "Fix PIL Image leaks in storage_service.py" beats "Polish the backend"
2. **Check partial work**: timed-out tasks still made usable changes — verify before re-delegating
3. **Infra discoveries happen**: AWU-24 uncovered that Node 18 was too old for Vite 8, requiring a Node upgrade (handled by parent inline)
4. **Linear comments matter**: each issue got a summary comment — future sessions know exactly what was done

## Sub-agent model config

Per user direction: all sub-agents use `deepseek-v4-flash` via `opencode-go` provider, set in config delegation section. This was configured mid-session and may not take effect until gateway restart.
