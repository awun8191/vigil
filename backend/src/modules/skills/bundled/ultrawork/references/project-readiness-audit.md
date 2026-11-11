# Project Readiness Audit with Ultrawork

A systematic, wave-based approach to bringing a project to production readiness. Covers backend hardening, frontend performance, SEO, mobile responsiveness, security, documentation, and data seeding — all executed in parallel waves.

## When to Use

- User asks to "bring the project as close to ready as possible"
- Pre-launch hardening sprint
- Production-readiness checklist execution
- Taking over a project that needs a comprehensive uplift before demo/deploy

## Process Overview

```
Phase 0: Document immersion → Phase 1: Baseline → Phase 2: Categorize → Phase 3: Parallel waves → Phase 4: Verify → Phase 5: Seed data → Phase 6: Final verification
```

## Phase 0: Document Immersion (15 min)

Read every project document to understand scope, architecture, and current state:

| Document | What to extract |
|----------|-----------------|
| `PROJECT.md` | Product vision, architecture, tech stack, deployment model |
| `STAGE1.md` / `PHASE1.md` | Current phase scope, completion criteria, what's considered "done" |
| `AGENTS.md` / `CLAUDE.md` | Project layout, package managers, available commands, test structure |
| `README.md` | Current claimed state — note discrepancies |

Key outputs: project root, backend/app dirs, package managers, test count, known issues.

## Phase 1: Establish Baseline

Run these in parallel (batch as `delegate_task` if you need to, but these are quick):

```bash
# Backend baseline
cd BACKEND && uv run pytest tests/ -q --tb=short   # test count + pass rate
cd BACKEND && uv run python -c "from src.main import create_app; create_app()"  # imports OK?

# Frontend baseline
cd FRONTEND && npm run build 2>&1 | tail -5          # build time + output size
cd FRONTEND && npx tsc --noEmit                      # type check

# Data baseline
ls -lh data/                                         # seed data present?
```

Record: test count, build time, any errors. These are your success criteria.

## Phase 2: Categorize Improvements

Group work into independent buckets. Typical categories for a readiness audit:

| Bucket | Typical Items | Wave Priority |
|--------|--------------|---------------|
| **Backend perf** 🚀 | GZip compression, Cache-Control headers, connection pooling | Wave 1 |
| **Security** 🔒 | CORS hardening, body size limits, rate limiting, security headers, input validation, firestore rules | Wave 1 |
| **Error handling** 🩺 | Health checks (DB, vector store), fallback search (when AI API unavailable), graceful degradation paths | Wave 1 |
| **Frontend perf** ⚡ | Lazy loading routes, code splitting (vendor/UI chunks), `loading="lazy"` on images, build optimization | Wave 2 |
| **SEO** 🌐 | Open Graph meta tags, Twitter Cards, JSON-LD structured data, robots.txt, sitemap.xml, proper title/description | Wave 2 |
| **Mobile** 📱 | Responsive grids (1-col at 480px), touch targets (44px min), iOS-safe font sizes, hamburger menus, horizontal table scroll | Wave 2 |
| **Docs** 📝 | README rewrite, AGENTS.md update, .env.example completeness, API reference | Final |
| **Data** 🗄️ | Seed data script, dual-write to default + test paths, data integrity verification | Final |

## Phase 3: Parallel Execution Waves

### Batch sizing
- `delegate_task` enforces `max_concurrent_children` = **3 tasks max per batch**
- For workloads above 3, split into sequential waves of 3
- Similar-sized tasks within a wave (don't mix a 5-minute task with a 30-second one)
- Track progress with `todo` — update after each wave so parent context stays current

### Rich context template per task
```python
delegate_task(tasks=[
    {
        "goal": "Backend: Add X middleware to improve Y",
        "context": (
            "File: /path/to/main.py -- the FastAPI app factory. "
            "Read it first. The middleware stack order is documented in comments. "
            "Add the new middleware in the correct position. "
            "Run tests after changes: `cd BACKEND && uv run pytest tests/ -q`"
        ),
        "toolsets": ["terminal", "file"]
    },
    ...  # max 3 tasks
])
```

### Verification after each wave
- Quick check: `pytest` + `npm run build` (non-blocking — let test run in background while you preview)
- Re-read any files the parent previously read before editing in subsequent waves (subagents modify files; stale cached views cause "re-read before editing" warnings)

### Execution note for background processes
```bash
# GOOD — uses project venv via uv
cd BACKEND && uv run uvicorn src.main:app --reload

# BAD — sourcing venv directly can conflict with Hermes VIRTUAL_ENV
source .venv/bin/activate && uvicorn src.main:app

# DO NOT use shell background (&) in foreground commands
# Use terminal(background=true) for long-lived processes
```

## Phase 4: Verify After Each Wave

After each wave completes:

1. **Run backend tests**: `uv run pytest tests/ -q --tb=short`
2. **Build frontend**: `npm run build` (or `tsc --noEmit` if just changing TS)
3. **Check build output**: verify no warnings, acceptable chunk sizes
4. **Check import health**: `python -c "from src.main import create_app; create_app()"`
5. **Update todo**: mark wave tasks complete

If any check fails, identify which task caused it and fix before proceeding.

## Phase 5: Seed Demo Data

Before calling done, ensure the app works without any configuration:

```bash
# Seed script writes to both default AND test paths
cd BACKEND && uv run python scripts/seed_data.py

# Verify data
python -c "
import json
vendors = json.load(open('data/vendors.json'))
products = json.load(open('data/products.json'))
print(f'{len(vendors)} vendors, {sum(len(v) for v in products.values())} products')
"
```

**Why dual-write matters**: The app reads from `data/vendors.json` and `data/products.json` by default. Tests override to `data/test_vendors.json` and `data/test_products.json`. Both must be populated.

## Phase 6: Final Verification

```bash
# 1. Backend tests pass
cd BACKEND && uv run pytest tests/ -q

# 2. Frontend builds
cd FRONTEND && npm run build

# 3. Backend app imports clean
cd BACKEND && uv run python -c "from src.main import create_app; create_app()"

# 4. Seed data intact
ls -lh data/products.json data/vendors.json

# 5. Key integration: fallback search works without API keys
cd BACKEND && uv run python -c "
import asyncio
from src.services.search_service import SearchService
from src.repositories.factory import create_product_repository
svc = SearchService(product_repo=create_product_repository())
async def t():
    r = await svc.search(query_text='test', top_k=1)
    print(f'Fallback search works: {r[\"count\"]} results, type={r[\"search_type\"]}')
asyncio.run(t())
"
```

## Common Pitfalls

| Pitfall | Prevention |
|---------|-----------|
| **MAX_CONCURRENT_CHILDREN limit hit** | Batch max 3 tasks per delegate_task call. Error: "Too many tasks: N provided, but max_concurrent_children is 3" |
| **Stale file reads after parallel edits** | Re-read files the parent read before editing in subsequent waves. Subagents modify files and the parent's cached view becomes stale. |
| **Background process stuck** | Use `terminal(background=true)` not shell `&`. Check logs with `process(action="log")`. Kill with `process(action="kill")`. |
| **VIRTUAL_ENV mismatch** | Always use `uv run <command>` in backend dir. Don't `source .venv/bin/activate`. |
| **Frontend API calls broken after refactor** | Always run `npm run build` after frontend changes — TypeScript catches integration errors. |
| **Search returns 503 instead of results** | If embedding API is unavailable, the search service should catch EmbeddingError and fall back to keyword search. If it doesn't, the route handler is returning the error before the fallback. |
| **Backend won't start after middleware changes** | Trace back from the import error. FastAPI loads middleware via `app.add_middleware()` at module level, so import failures crash the server. |

## Example Task Breakdown (from AWUN project)

```
Wave 1 (Backend core):
  Task 1: Fix error messages, add input validation
  Task 2: Add GZip + Cache-Control + body size limit middleware
  Task 3: Add ChromaDB health check

Wave 2 (Backend security + search):
  Task 1: CORS hardening, security headers, rate limiting
  Task 2: Fallback keyword search when embedding unavailable
  Task 3: Add image_base64 support to POST search endpoint

Wave 3 (Frontend performance):
  Task 1: SEO overhaul (OG, Twitter, JSON-LD, meta tags)
  Task 2: Lazy loading routes + ErrorBoundary + NotFoundPage
  Task 3: Mobile responsiveness (sidebar, grids, touch targets)

Wave 4 (Frontend + docs):
  Task 1: Vite build optimization, loading=lazy on images, robots.txt + sitemap.xml
  Task 2: Firestore rules, startup script env checks
  Task 3: AGENTS.md + README.md update

Wave 5 (Data + final verify):
  Task 1: Seed data script (280 products + vendor)
  Task 2: Final verification (tests + build + integration)
```

Total: 5 waves × 3 tasks = 15 tasks, 29 discrete improvements over ~3 sessions.
