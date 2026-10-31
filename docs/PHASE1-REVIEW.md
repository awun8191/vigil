# Phase 1 Review - virgil-agent

**Commit reviewed:** `085e38f` feat: scaffold Phase 1 foundations
**Tests run:** `uv run pytest -q` in `backend/`
**Result:** 259 passed in 8.10 seconds
**Date:** 2026-10-31

---

## 1. Verdict

The scaffold is good work. The module structure is correct, the protocol boundary holds, and the test suite covers more than the specification asked for.

Phase 1 does not close yet. Two defects in the authentication path make the default deployment insecure. Neither test suite catches them, because the tests use the same bypass as the application.

Everything in Section 2 was verified by running the code. Nothing here is inferred from reading.

---

## 2. Verified working

| Claim | Evidence |
|---|---|
| Test suite passes | 259 passed, 0 failed, 8.10 s |
| App factory builds | `create_app()` mounts all 14 modules |
| Uniform error contract | `AppException` handlers return `{code, message, request_id, details}` |
| Correlation headers | `X-Request-ID` and `X-Process-Time` present on responses |
| AES-256-GCM round trip | `encrypt_secret` then `decrypt_secret` returns the plaintext |
| Constant-time token compare | `hmac.compare_digest` in `verify_token` |
| Protocol purity | AST tests reject SQL and ORM imports in `repository.py` |
| Router factory pattern | AST tests reject a bare global `APIRouter` |
| Structured logging | JSON lines with `request_id` contextvar |
| Docker Compose | 3 services defined, Postgres healthcheck present |

The strongest part is the AST enforcement. Most teams assert the architecture in review. This repository asserts it in CI.

---

## 3. Blockers

### P1-1. Any token that starts with `vgt_test` authenticates

`backend/src/modules/auth/service.py:228`

```python
if not device and self.settings.APP_ENV in ("test", "development") and clean_token.startswith("vgt_test"):
```

`APP_ENV` defaults to `development`. `deploy/docker-compose.yml` sets `APP_ENV: ${APP_ENV:-development}`.
So a stock deployment accepts any bearer token whose value starts with `vgt_test`.

**Proof, run against the real app factory:**

```
vgt_testtoken             -> 200
vgt_test_anything_i_want  -> 200
vgt_prod_real_token       -> 401
```

This is a wildcard bypass, not four seeded test values. The prefix check on line 228 admits the whole namespace.

**Fix.** Delete the branch. Seed test devices from a fixture in `tests/conftest.py` alone.
Guard the seeding class with `if self.settings.APP_ENV == "test"` and nothing else.
Add a regression test that asserts 401 for `vgt_test_anything` with `APP_ENV=development`.

### P1-2. All authentication state lives in class level dictionaries

`AuthService._active_pairing_codes`, `_devices`, `_devices_by_id`, `_users` are `ClassVar` dicts.

Consequences:

1. Every device token dies on restart. The user must re-pair after each deploy.
2. The pairing code printed at boot dies on restart. The QR code in the log becomes stale.
3. Two API replicas do not share state. A token issued by replica A fails on replica B.
4. Nothing reaches the `devices` or `users` table defined in the specification.

This contradicts architectural tenet 2. Run state and identity state must be durable.

**Fix.** Implement `AuthRepository` against the `AsyncSession` that the service already receives. The service ignores its `session` argument today.
Phase 2 delivers the tables. Until then, keep the in-memory store behind a `DeviceStore` Protocol and mark it `development` only, so the production path cannot use it.

**Note the interaction with P1-1.** Fixing P1-1 alone leaves a system where the only working credentials are the ones that die on restart.

### P1-3. The readiness probe returns 200 when the database is down

`GET /api/v1/health` with an unreachable Postgres:

```
200 {'status': 'degraded', 'database': 'error', ...}
```

A readiness probe that returns 200 keeps the instance in the load balancer. Compose and Kubernetes both read the status code, not the body.

**Fix.** Return 503 when `database_connected` is false. Keep 200 for liveness only. Split the two endpoints.

### P1-4. The default encryption key is public, and Compose never overrides it

`Settings.SECRET_KEY` defaults to a constant 64 character hex string. `.env.example` repeats it.
`deploy/docker-compose.yml` passes `DATABASE_URL` and `APP_ENV` to the containers. It does not pass `SECRET_KEY`.

Result: every stock deployment encrypts connector credentials with a key that is published in the repository.

**Fix.**
1. Add a validator. If `APP_ENV == "production"` and `SECRET_KEY` equals the default, raise at startup.
2. Add `SECRET_KEY: ${SECRET_KEY:?SECRET_KEY is required}` to both services. The `:?` form fails the compose file when the value is missing.
3. Add `env_file: ../deploy/.env` so the file is the single source.

### P1-5. The worker steals long jobs and marks stub work complete

Two problems in one loop.

**The reaper.** `reap_stale_locks(timeout_seconds=300)` runs every poll cycle against `locked_at`. The worker never updates `locked_at` after the claim.
Any job that runs longer than 5 minutes is reaped and claimed by a second worker. Two containers then write to the same workspace. This is defect B2 from the specification review, now in code.

**The dispatch.** `src/worker.py:50`

```python
await asyncio.sleep(0.01)  # Simulate non-blocking task
await jobs_service.mark_completed(job.id)
```

Every claimed job is marked completed, whatever its type. A `gmail_send` job reports success without sending. That is worse than a crash, because the audit trail records a delivery that never happened.

**Fix.**
1. Add `heartbeat_at`. Update it every 30 seconds. Reap on `heartbeat_at` with a 90 second limit.
2. Replace the sleep with a dispatch table. An unknown job type must fail with a clear error, never complete.
3. Add the `priority` column now, while the table does not exist yet. Changing it later means a migration on live data.

### P1-6. Idempotency protection is per process only

`IdempotencyMiddleware` builds an `InMemoryIdempotencyStore` for each app instance.
The API and the worker are separate containers. Uvicorn with more than one worker multiplies the stores.

A retried `POST /api/v1/auth/pair` or a future `POST /approvals/{id}/decision` is protected only if it lands on the same process. That is the case the header exists to prevent.

**Fix.** Add the `idempotency_keys` table from the specification review (C6) and implement the Postgres store. Keep the in-memory store for tests. Select the store in the composition root.

---

## 4. Medium issues

| # | Issue | Location | Fix |
|---|---|---|---|
| M1 | Router factory swallows `ImportError` and logs at debug. A broken module becomes a silent 404 | `src/config/router.py:56` | Log at warning. Fail in production |
| M2 | `pytest`, `pytest-asyncio`, `httpx` are runtime dependencies | `pyproject.toml:23-24` | Move to `[dependency-groups] dev` |
| M3 | Two import roots `pythonpath = [".", "src"]`. `src.config.settings` and `config.settings` become distinct classes | `pyproject.toml:32` | Keep `"."` only |
| M4 | `get_db_session` yields a fake session that returns 1 when the engine fails | `src/dependencies.py:92-112` | Restrict to test. Raise in production |
| M5 | `importlib` router aggregation hides the dependency graph from static analysis | `src/config/router.py:45` | Use explicit imports. The factory pattern stays |
| M6 | No `api` healthcheck and no `worker` dependency on `api` in Compose | `deploy/docker-compose.yml` | Add healthcheck and `depends_on` |
| M7 | Status table is stale. M3 says IN_PROGRESS, M4 to M6 say PLANNED, while the code and tests are complete | `PROJECT.md:46-51` | Update before the next commit |
| M8 | `VGL-` pairing prefix and `APP_NAME: Virgil` disagree with the project name Vigil | multiple | Pick one name |

---

## 5. Specification drift in the README

The README is the specification. It still carries every defect from the earlier review. Phase 2 will implement these as written unless the document changes first.

| Section | Current text | Correction |
|---|---|---|
| 5 intro | 16 tables | 17 tables, 21 after the additions |
| 5.1 | `__tablename__` derived from the class name | Derivation produces `memorychunks` and `cardss`. Declare each name |
| 5.5 | `cost_usd: float` | `cost_micros: bigint` |
| 5.6 | `Vector(1536)` | `Vector(384)` for bge-small-en-v1.5 |
| 7.1 | "OpenAI text-embedding-3-small, 1536 dimensions" | Local bge-small-en-v1.5, 384 dimensions, int8 |
| 7.1 | "marked with an `indexed_at` timestamp" | No such column is defined |
| 7.2 | Override threshold 0.85 | Never fires for this model. Correct answers measure 0.679 to 0.801 |
| 8.1 | Order by `run_at` only | No priority means an approval waits behind poll jobs |
| 8.2 | Reap on `locked_at` | Reap on `heartbeat_at` |
| 11.2 | Socket proxy allows `POST /containers/create` | The proxy filters routes, not payloads. The agent can request a privileged container |
| 13 | Phase 1 exit criteria met | Met on paper. Two of three criteria depend on state that dies on restart |

---

## 6. Phase 1 exit criteria

The stated criteria are three:

| Criterion | Status | Note |
|---|---|---|
| `uv run pytest` passes 100 percent | Met | 259 passed |
| Server boots | Met | Verified through the app factory |
| Pairing endpoint generates a device token | Met, with a caveat | The token works until the process restarts |

Phase 1 is therefore complete against the letter of the checklist, and not complete against its intent. The scaffold is real. The identity it issues is not durable.

---

## 7. Priority order

1. **P1-1.** Remove the `vgt_test` prefix bypass. This is a security defect in the default configuration.
2. **P1-4.** Refuse to boot on a production environment with the default key, and pass `SECRET_KEY` through Compose.
3. **P1-2.** Move devices and pairing codes to the database. This unblocks Phase 2.
4. **P1-3.** Return 503 from readiness.
5. **P1-5.** Add `heartbeat_at` and `priority`, and remove stub completion.
6. **P1-6.** Postgres idempotency store.
7. **Section 5.** Correct the README before Phase 2 writes models against it.

Items 1, 2, and 4 are small. Items 3, 5, and 6 need Phase 2 tables, so they belong in that phase, not in a Phase 1 patch.
