# E2E Test Infra: vigil-agent (Phase 1)

## Test Philosophy
- Opaque-box, requirement-driven, and architectural-invariant enforcement.
- Verification mechanism: `pytest` and `pytest-asyncio` using `httpx.AsyncClient` against FastAPI ASGI app, plus AST validation for strict architectural rules.
- Methodology: Category-Partition + Boundary Value Analysis + Pairwise Interactions + Real-World Workflows.

## Feature Inventory Test Mapping
| # | Feature | Test Tier 1 | Test Tier 2 | Test Tier 3 | Test Tier 4 |
|---|---------|:-----------:|:-----------:|:-----------:|:-----------:|
| F01 | Monorepo Scaffolding | 5 checks | Path boundaries | Monorepo import | Project build |
| F02 | UV Python Env | 5 deps | Version constraints | Dep conflicts | Clean sync |
| F03 | Core Settings | 5 default configs | Missing envs/types | Env override | Full app load |
| F04 | AppException Hierarchy | 5 exception types | Inheritance tree | Custom status codes | Error bubbling |
| F05 | Uniform Error Serialization | 5 status codes | Empty details / bad JSON | Request ID match | Full error flow |
| F06 | Structured Logging | 5 log levels | Missing contextvars | Request ID propagate | Error trace |
| F07 | Security Primitives | 5 token tests | Empty/Unicode secrets | Encrypt/decrypt roundtrip | Auth token flow |
| F08 | Request ID Middleware | 5 header cases | Malformed UUID / empty | Propagation to response | End-to-end trace |
| F09 | Timing Middleware | 5 latency tests | Instant execution | Header presence | Performance gate |
| F10 | Idempotency Middleware | 5 mutating methods | Duplicate key / expired | In-flight conflict | Full replay flow |
| F11 | Root API Router | 5 module routes | Bad prefix / 404 | Multi-router merge | Full API mount |
| F12 | 14 Domain Skeletons | 14 modules present | Submodule completeness | Module isolation | App load |
| F13 | Pure Protocols in repo.py | 14 repos AST | Zero SQL / Zero ORM | Duck typing conformance | Mock repo swap |
| F14 | Module Services & DTOs | 14 service classes | Constructor DI verification | Protocol satisfaction | Mock injection |
| F15 | Module Router Factories | 14 factory funcs | Zero global APIRouter | Dynamic settings inject | Prefix mounting |
| F16 | Central Dependencies Hub | Central re-exports | Missing provider checks | Override in test | DI resolution |
| F17 | External Integrations Repo | 5 external protocols | Protocol runtime checks | Service integration | Mock adapter |
| F18 | Working Health Module | Health endpoint 200 | DB disconnected/disabled | Header assertions | Monitoring probe |
| F19 | Device Pairing Flow | Pair code exchange | Invalid/expired code | Re-pairing idempotency | Mobile pair flow |
| F20 | Startup ASCII QR Code | ASCII QR generation | Terminal width limits | Plaintext fallback | Server boot |
| F21 | Authenticated Device Me | Valid bearer token | Invalid / missing token | Token expiration | Session access |
| F22 | Docker Compose Harness | Compose syntax | Port collisions / missing env | Multi-service network | Local deploy |
| F23 | .env.example Coverage | All keys present | Default placeholders | Settings parity | Fresh clone setup|
| F24 | Pytest Test Suite | Clean run (0 errs) | Async fixture isolation | Coverage threshold | CI pipeline |
| F25 | Docker Compose Config | Valid config command | Service dependencies | Healthcheck config | Production readiness |

## Test Architecture
- Test runner: `cd backend && uv run pytest -v`
- Pass/Fail Semantics: Exit code 0, 0 failures, 0 errors.
- Structure:
  - `backend/tests/unit/`:
    - `test_settings.py`
    - `test_exceptions.py`
    - `test_security.py`
    - `test_protocols.py` (AST enforcement of zero SQL/ORM in `repository.py`)
    - `test_router_factories.py` (AST enforcement of router factory pattern across all 14 modules)
    - `test_dependencies.py`
    - `test_qr.py`
  - `backend/tests/integration/`:
    - `test_middlewares.py` (Request ID, Timing, Idempotency)
    - `test_error_contract.py` (Uniform JSON schema validation)
    - `test_health.py` (200 OK, X-Request-ID, X-Process-Time)
    - `test_device_pairing.py` (Pairing code -> token -> /auth/me authentication)
  - `backend/tests/e2e/`:
    - `test_system_bootstrap.py` (Full FastAPI lifecycle, ASCII QR startup, module route registration)
    - `test_docker_compose.py` (Subprocess verification of `docker compose -f deploy/docker-compose.yml config`)
