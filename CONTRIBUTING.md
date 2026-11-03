# Contributing to Vigil

Thanks for your interest. This document describes how to set up the project, the rules that a change must follow, and the review gate.

Vigil is alpha software. The interfaces move. Open an issue before you write a large change, so we agree on the shape first.

## Setup

Requirements: Docker, Python 3.12 or newer, [uv](https://docs.astral.sh/uv/), and Node 20 or newer for the web client.

```bash
git clone https://github.com/awun8191/vigil.git
cd vigil/backend

env -u PYTHONPATH uv sync
cp .env.example .env
```

Start PostgreSQL with the Compose stack:

```bash
docker compose -f deploy/docker-compose.yml up -d postgres
```

Apply the migrations and run the API:

```bash
uv run alembic upgrade head
uv run uvicorn src.main:app --reload
```

The API does not poll the job queue by default, so a run you start stays queued
with no error. Run the worker in a second terminal:

```bash
env -u PYTHONPATH uv run python -m src.worker
```

For a single-process local setup, set `EMBED_WORKER=true` in `.env` and skip the
second terminal. Never do both: two pollers on one queue claim the same work.

The `env -u PYTHONPATH` prefix matters on a host that exports a Hermes or system `PYTHONPATH`. Without it, the interpreter loads a foreign `pydantic_core` and the tests fail at import.

## Rules for a change

1. **Module contract.** Every domain module under `backend/src/modules/` has five files: `repository.py`, `service.py`, `data.py`, `dependencies.py`, and `router.py`.
   - `repository.py` holds `@runtime_checkable` Protocols and DTOs only. No SQL, no ORM, no HTTP.
   - `service.py` holds the mechanism. It receives `session` and `settings` through the constructor.
   - `router.py` exposes `build_<resource>_router(settings) -> APIRouter`. Never a module-level `APIRouter`.
2. **Models.** Every SQLAlchemy model declares `__tablename__` explicitly. Do not derive the name.
3. **Money.** Store money in `cost_micros`, a `bigint` of micro-USD. Never use a float.
4. **Embeddings.** Vectors are `Vector(384)` for `bge-small-en-v1.5`. Change the model in one place, not in a query.
5. **Errors.** Raise a subclass of `AppException`. The response envelope is `{code, message, request_id, details}`.
6. **Migrations.** Add a new revision under `backend/alembic/versions/`. Do not edit an applied revision. Check the highest number before you pick one.
7. **Secrets.** Never commit `.env`. Never log a token, a key, or a decrypted credential.

## Tests

Run the suite from `backend/`:

```bash
env -u PYTHONPATH uv run pytest -q
```

The tree has 612 tests. Tests that need PostgreSQL or Docker skip when the service is absent.
Read the skip count before the pass count: a climbing skip count is a missing service, not a pass.

A new behavior needs a test that fails before the change and passes after it.
A claim in a pull request needs a command that produces the number.

## Commits and pull requests

- Use Conventional Commits: `feat(scope): summary`, `fix(scope): summary`, `docs(scope): summary`.
- Keep one atomic change per commit. Do not mix a refactor with a behavior change.
- Branch from `main`. Rebase on `main` before you request a review.
- Fill the pull request template. State the change, the reason, and the evidence.

## Review gate

Every change needs a review. The reviewer checks the property, not the color of the test run:

- Does the test fail without the change?
- Does a new route appear in `openapi.json` with the expected schema?
- Does a migration leave the database in the state that the description claims?

A green suite is not a result on its own.

## Code of conduct

Participation follows the [Contributor Covenant](CODE_OF_CONDUCT.md).

## License

A contribution is licensed under the Apache License 2.0. See [LICENSE](LICENSE).
