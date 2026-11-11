# Vigil Backend Service

FastAPI backend engine, stateless agent runtime loop, and asynchronous task execution harness for Vigil.

Provides modular domain endpoints, pgvector long-term memory, PostgreSQL-backed `SKIP LOCKED` job queue, sandboxed Docker execution, secure device pairing, and strict protocol-driven repositories.

---

## Quickstart

### Prerequisites
- Python 3.12+ (CPython 3.12 or 3.13)
- [uv](https://docs.astral.sh/uv/) package manager
- PostgreSQL 16+ with `pgvector` extension (e.g. `docker compose -f deploy/docker-compose.yml up -d postgres`)

### Setup & Run

1. **Install dependencies**:
   ```bash
   uv sync
   ```

2. **Configure environment**:
   ```bash
   cp .env.example .env
   # Ensure DATABASE_URL and SECRET_KEY are configured
   ```

3. **Apply database schema migrations**:
   ```bash
   uv run alembic upgrade head
   ```

4. **Start the API server**:
   ```bash
   uv run uvicorn src.main:app --reload --port 8000
   ```

5. **Start the background job worker** (in a second terminal):
   ```bash
   env -u PYTHONPATH uv run python -m src.worker
   ```
   > **Note**: The API process does not poll the job queue by default. For single-process development, set `EMBED_WORKER=true` in `.env` to run the worker inside the API lifespan, or run the root startup script (`../start.sh`).

---

## All-In-One Startup Script

From the repository root, you can start the API, the worker, and the web frontend together:

```bash
../start.sh
```

---

## Architectural Invariants

Every domain module under `src/modules/` adheres to strict architectural contracts:

1. **Protocol Repositories (`repository.py`)**:
   - Defines persistence and external interactions strictly through `@runtime_checkable typing.Protocol` abstractions.
   - Zero SQL, zero ORM, and zero external HTTP calls in `repository.py`.
2. **Constructor Dependency Injection (`service.py`)**:
   - Accepts dependencies (`session`, `settings`, etc.) via constructor injection.
3. **Router Factory (`router.py`)**:
   - Every module exposes `build_<resource>_router(settings: Settings) -> APIRouter`.
   - Never defines a bare global module-level `APIRouter`.
4. **Uniform Error Serialization**:
   - All errors inherit from `AppException` and return `{ "code", "message", "request_id", "details" }`.
   - Propagates `X-Request-ID` and `X-Process-Time` response headers.
5. **Money**:
   - Stored as `cost_micros` (integer count of micro-USD). Never float.

---

## Testing

Run the test suite:

```bash
env -u PYTHONPATH uv run pytest -q
```

To run a specific test suite:
```bash
uv run pytest tests/unit/test_chat_sql_paging.py -v
```
