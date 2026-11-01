# Project: vigil-agent (Phase 1: Foundations & Architecture Scaffold)

## Architecture
Vigil is an open-source, self-hostable proactive AI assistant and execution harness. Phase 1 establishes the rock-solid foundations and module-driven architecture scaffold:
- **FastAPI Backend (`backend/`)**: Modular architecture with dynamic router factories and ASGI middleware pipeline.
- **Strict Protocol Boundaries**: Every domain module defines persistence and external interaction solely through `@runtime_checkable typing.Protocol` abstractions in `repository.py`. Zero SQL, zero ORM, zero HTTP calls in `repository.py`.
- **Constructor Dependency Injection**: `service.py` implements all domain logic, accepting `session` and `settings` via constructor injection.
- **Factory Router Pattern**: Zero bare module-level global `APIRouter`s. Every module exposes `build_<module>_router(settings: Settings) -> APIRouter`.
- **Central Dependency Hub**: `backend/src/dependencies.py` re-exports all providers for one-stop injection.
- **Uniform Error Handling**: All errors inherit from `AppException` returning `{"code", "message", "request_id", "details"}` with correlation header `X-Request-ID`.
- **Security & Pairing**: SHA-256 token hashing, AES-256-GCM secret encryption, startup ASCII QR code pairing flow.
- **Containerized Deployment**: Multi-service Docker environment (`postgres` with pgvector, `api`, `worker`).

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| F01 | Monorepo Directory Scaffolding | Top-level folders: `connectors/gmail/`, `mobile/`, `web/`, `skills/`, `deploy/`, `docs/` | M1 | R1 |
| F02 | UV Python Environment | `backend/pyproject.toml` targeting Python >=3.12 with all core and test dependencies | M1 | R1 |
| F03 | Core Settings Configuration | Pydantic v2 `BaseSettings` loading DB, ports, secrets, tiered models, and LLM keys | M2 | R2 |
| F04 | Uniform Exception Hierarchy | `AppException` with `NotFoundError`, `UnauthorizedError`, `ForbiddenError`, `ConflictError`, `ValidationError` | M2 | R2 |
| F05 | Uniform Error Serialization | FastAPI exception handlers returning `{code, message, request_id, details}` echoing `X-Request-ID` | M2 | R2 |
| F06 | Structured Logging Pipeline | JSON/text logging with contextvars propagating `request_id` | M2 | R2 |
| F07 | Security & Crypto Primitives | SHA-256 `hash_token`, `verify_token` and AES-256-GCM `encrypt_secret`, `decrypt_secret` | M2 | R2 |
| F08 | Request ID Middleware | ASGI middleware extracting or generating UUID `X-Request-ID` header and contextvar | M2 | R2 |
| F09 | Timing Middleware | ASGI middleware measuring request processing latency in `X-Process-Time` | M2 | R2 |
| F10 | Idempotency Middleware | ASGI middleware enforcing `Idempotency-Key` validation on mutating requests | M2 | R2 |
| F11 | Root API Router Factory | `create_api_router(settings)` dynamically aggregating module routers under `/api/v1` | M2 | R2 |
| F12 | 14 Domain Module Skeletons | Scaffold modules (`health`, `auth`, `cards`, `triage`, `runs`, `approvals`, `chat`, `artifacts`, `memory`, `compaction`, `skills`, `connectors`, `settings`, `jobs`) | M3 | R3 |
| F13 | Pure Repository Protocols | Strict `@runtime_checkable typing.Protocol` abstractions in `repository.py` with 0 SQL/ORM | M3 | R3 |
| F14 | Module Services & DTOs | `service.py` constructor DI and `data.py` Pydantic DTOs for all 14 modules | M3 | R3 |
| F15 | Module Router Factories | `build_<module>_router(settings)` factory across all 14 modules | M3 | R3 |
| F16 | Central Dependencies Hub | `backend/src/dependencies.py` centrally re-exporting all providers | M3 | R3 |
| F17 | External Integration Protocols | `backend/src/integrations/repositories.py` abstracting Jev, LLM, Sandbox, MCP, Notifications | M3 | R3 |
| F18 | Working Health Module | `GET /api/v1/health` returning liveness and DB readiness probe | M4 | R4 |
| F19 | Device Pairing Flow | `POST /api/v1/auth/pair` validating pairing code and issuing long-lived bearer token | M4 | R4 |
| F20 | Startup ASCII QR Code | Generator printing terminal ASCII QR code and temporary pairing code on boot | M4 | R4 |
| F21 | Authenticated Device Identity | `GET /api/v1/auth/me` authenticating bearer token against SHA-256 hash | M4 | R4 |
| F22 | Docker Compose Harness | `deploy/docker-compose.yml` defining `postgres` (pgvector:pg16), `api`, `worker` | M5 | R5 |
| F23 | Environment Configuration Doc | Exhaustive `.env.example` documenting all configuration keys and defaults | M5 | R5 |
| F24 | E2E & Architectural Tests | Unit, contract, AST architectural validation, and integration tests passing via `uv run pytest` | M6 | Acceptance |
| F25 | Docker Compose Validation | Verification that `docker compose -f deploy/docker-compose.yml config` passes cleanly | M6 | Acceptance |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Monorepo & Python Environment | F01, F02: `pyproject.toml`, `uv sync`, directory scaffolding | none | DONE |
| M2 | Core Config, Middlewares & Exceptions | F03, F04, F05, F06, F07, F08, F09, F10, F11: `src/config/`, `src/middlewares/` | M1 | DONE |
| M3 | Domain Module Protocols & Skeletons | F12, F13, F14, F15, F16, F17: 14 domain modules, protocols, router factories, central deps | M2 | IN_PROGRESS |
| M4 | Working Health & Device Auth Modules | F18, F19, F20, F21: operational health & auth endpoints, ASCII QR, token hashing | M3 | PLANNED |
| M5 | Deployment & Database Harness | F22, F23: `deploy/docker-compose.yml`, `.env.example`, docker config verification | M1 | PLANNED |
| M6 | Testing, Verification & Hardening | F24, F25: complete unit/contract/AST test suites, E2E test verification, audit | M4, M5 | PLANNED |

## Interface Contracts

### Error Response Contract
All error responses from FastAPI endpoints must conform to:
```json
{
  "code": "ERROR_CODE_STRING",
  "message": "Human-readable explanation",
  "request_id": "uuid-string",
  "details": null | {}
}
```
Headers:
- `X-Request-ID`: echoed from request or newly generated UUID.
- `X-Process-Time`: execution latency formatted e.g. `0.001234s`.

### Router Factory Contract
Every module router must be defined as:
```python
def build_<module>_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/<module>", tags=["<module>"])
    ...
    return router
```
No module may define a bare global `router = APIRouter()`.

### Repository Protocol Contract
Every `src/modules/<module>/repository.py` must contain:
```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class <Module>Repository(Protocol):
    ...
```
Zero imports of `sqlalchemy`, `sqlmodel`, `tortoise`, or raw SQL.

### Security Contract
- `hash_token(token: str) -> str`: Returns SHA-256 hex digest.
- `verify_token(token: str, token_hash: str) -> bool`: Constant-time comparison.
- `encrypt_secret(plaintext: str, secret_key: str) -> str`: AES-256-GCM base64 encoded.
- `decrypt_secret(ciphertext: str, secret_key: str) -> str`: AES-256-GCM decrypt.

### Auth & Health Wire Contracts
- `GET /api/v1/health` -> `200 OK` `{"status": "ok", "version": "0.1.0", "database": "connected" | "disabled"}`
- `POST /api/v1/auth/pair` with `{"pairing_code": "VIG-...", "device_name": "My Device"}` -> `200 OK` `{"device_token": "vgt_...", "device_id": "uuid", "device_name": "My Device"}`
- `GET /api/v1/auth/me` with `Authorization: Bearer vgt_...` -> `200 OK` `{"device_id": "uuid", "device_name": "My Device", "created_at": "...", "last_seen_at": "..."}`

## Code Layout
```
/home/nasbombz/Documents/Projects/vigil-agent/
├── backend/
│   ├── pyproject.toml
│   ├── uv.lock
│   ├── src/
│   │   ├── main.py
│   │   ├── dependencies.py
│   │   ├── config/
│   │   │   ├── settings.py
│   │   │   ├── exceptions.py
│   │   │   ├── logging.py
│   │   │   ├── security.py
│   │   │   └── router.py
│   │   ├── middlewares/
│   │   │   ├── request_id.py
│   │   │   ├── timing.py
│   │   │   └── idempotency.py
│   │   ├── integrations/
│   │   │   └── repositories.py
│   │   ├── schemas/
│   │   └── modules/
│   │       ├── health/
│   │       ├── auth/
│   │       ├── cards/
│   │       ├── triage/
│   │       ├── runs/
│   │       ├── approvals/
│   │       ├── chat/
│   │       ├── artifacts/
│   │       ├── memory/
│   │       ├── compaction/
│   │       ├── skills/
│   │       ├── connectors/
│   │       ├── settings/
│   │       └── jobs/
│   └── tests/
│       ├── conftest.py
│       ├── unit/
│       └── integration/
├── connectors/gmail/
├── mobile/
├── web/
├── skills/
├── deploy/
│   ├── docker-compose.yml
│   └── Dockerfile.api
├── docs/
├── .env.example
├── README.md
└── PROJECT.md
```
