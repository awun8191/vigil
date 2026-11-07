<div align="center">

<img src="assets/social-preview.png" alt="Vigil: a self-hosted proactive AI assistant with approval-gated execution" width="100%">

# Vigil

### A self-hostable proactive AI assistant with approval-gated sandboxed execution

Vigil watches your inbox, tasks, and deadlines. It triages each event with a cheap classifier,
sends you an action card, and runs the long jobs inside a Docker sandbox.
Every action that leaves your machine waits for your approval.

[Quickstart](#quickstart) · [Architecture](#architecture) · [API](#api-surface) · [Documentation](#documentation) · [Contributing](#contributing)

[![CI](https://github.com/awun8191/vigil/actions/workflows/ci.yml/badge.svg)](https://github.com/awun8191/vigil/actions/workflows/ci.yml)
![License](https://img.shields.io/badge/license-Apache--2.0-blue)
![Python](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16%20%2B%20pgvector-4169E1?logo=postgresql&logoColor=white)
![Tests](https://img.shields.io/badge/tests-612-informational)
![Status](https://img.shields.io/badge/status-alpha-orange)

</div>

---

## What Vigil is

Vigil is an autonomous personal assistant that you run on your own server.
It polls your connected accounts, decides which events need attention, and turns them into cards.
A card carries a summary, a priority, and a set of actions.
You approve or reject each action on the web client.

Approved work runs in a sandbox container. The sandbox has a fixed tool schema, a memory cap, a CPU cap, and a workspace quota.
Vigil never holds a shell open on the host.

The runtime keeps no state in memory between turns.
A run that waits for you is a set of database rows, not a sleeping process.
An approval that arrives three days later resumes the run in a new container.

Batteries included: one PostgreSQL instance runs the relational state, the job queue, and the vector memory.
You do not need Redis, Celery, or a separate vector database.

## Why Vigil exists

| Problem in a typical agent framework | What Vigil does instead |
| --- | --- |
| Execution state lives in a Python thread or generator | Run state is a database row. A paused run frees its process. |
| Parallel tool calls break the provider protocol | A barrier synchronizer satisfies every tool call ID before the next model turn. |
| Redis, Celery, and a vector database for one assistant | One PostgreSQL 16 instance runs the queue, the state, and the memory. |
| Integrations hard-coded into the agent loop | MCP connectors with an approval policy for each tool. |
| The agent runs shell commands on your host | A fixed-schema runner executes inside a capped container. |
| A restart loses every queued run | The queue uses `FOR UPDATE SKIP LOCKED` and a heartbeat reaper. |

## Architecture

```mermaid
flowchart LR
  G[Gmail connector] --> T[Triage<br/>Jev classifier]
  M[Other MCP connectors] --> T
  T --> Q[(PostgreSQL 16<br/>jobs, runs, cards, memory)]
  Q --> C[Action cards<br/>web client]
  C -->|approval| W[Worker<br/>6-stage loop]
  W --> B[Barrier<br/>synchronizer]
  B --> S[Sandbox<br/>Docker runner]
  B -->|external side effect| H[Human<br/>approval]
  H -->|decision| B
  S --> Q
  W --> Q
```

The loop has six stages: load context, compact context, call the model, partition the tool calls, execute, and persist.
The partition step splits parallel tool calls into immediate work, background jobs, and gated approvals.
The barrier holds the turn open until every tool call ID has a result row.

## Features

| Subsystem | What it does |
| --- | --- |
| **Stateless runtime** | Runs move through `QUEUED`, `RUNNING`, `AWAITING_APPROVAL`, `AWAITING_BACKGROUND_JOB`, `COMPLETED`, and `FAILED`. The process exits at each pause. |
| **Barrier synchronizer** | Groups parallel tool calls and persists partial results, so a resume keeps the provider protocol valid. |
| **Postgres job queue** | Claims work with `SKIP LOCKED` and ages the priority. A reaper recovers a job when the heartbeat stops for 90 seconds. |
| **Long-term memory** | `pgvector` with 384 dimension vectors, a time decay with a 30 day half-life, a 0.35 floor, and a 0.65 override threshold. |
| **Context compaction** | Counts tokens with the model tokenizer, then applies a two-tier summary. An artifact becomes a stub, not a truncation. |
| **Proactive triage** | Batches connector items into one cheap classifier call, then converts the result into cards with an action set. |
| **Sandbox runner** | Fixed tool schema, CPU and memory caps, a workspace quota, and a snapshot on every pause. |
| **MCP connectors** | Gmail ships in the box. Calendar, Slack, and GitHub follow the same connector protocol. |
| **Skills** | Install, list, toggle, and remove a skill pack from the API. |
| **Device pairing** | A QR code on boot, a short-lived pairing code, SHA-256 token hashes, and AES-256-GCM secret storage. |
| **Web client** | React 19 and Vite. Home briefing, streaming chat, and settings pages. A monochrome design system. |

## Quickstart

### Requirements

- Docker with the Compose plugin
- Python 3.12 or newer and [uv](https://docs.astral.sh/uv/) for local development
- PostgreSQL 16 with the `pgvector` extension, only when you do not use Compose

### Run the stack

```bash
git clone https://github.com/awun8191/vigil.git
cd vigil

export SECRET_KEY=$(openssl rand -hex 32)
docker compose -f deploy/docker-compose.yml up --build
```

The API listens on `http://localhost:8000`.
Check readiness:

```bash
curl -fsS http://localhost:8000/api/v1/health/ready
```

On boot the API prints a pairing QR code and a short code such as `VIG-7K2M4Q`.
Open the web client and pair the device with that code.

### Run the backend from source

```bash
cd backend
uv sync
cp .env.example .env          # set DATABASE_URL and SECRET_KEY
uv run alembic upgrade head
uv run uvicorn src.main:app --reload --port 8000
```

Start the worker in a second terminal:

```bash
cd backend
env -u PYTHONPATH uv run python -m src.worker
```

The API does not poll the job queue. It has no Docker socket, so a job it
claimed would fail at its first sandbox tool call. For single-process local
development you can set `EMBED_WORKER=true` and skip the second terminal, but
then the API process must own the Docker socket. Keep it false whenever
`python -m src.worker` runs.

### Run the web client

```bash
cd web
npm install
npm run dev          # fixture data, no backend
npm run dev:live     # talks to the API on port 8000
```

### Run the tests

```bash
cd backend
env -u PYTHONPATH uv sync --quiet
env -u PYTHONPATH uv run pytest -q
```

The suite has 612 tests. Tests that need PostgreSQL or Docker skip when the service is absent.
Read the skip count before you read the pass count.

## Configuration

Vigil reads environment variables through `pydantic-settings`.
The full list is in [`backend/.env.example`](backend/.env.example).
These keys matter most:

| Key | Default | Purpose |
| --- | --- | --- |
| `SECRET_KEY` | none, required | Encrypts stored connector credentials. Generate it with `openssl rand -hex 32`. |
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@localhost:5432/vigil` | Async PostgreSQL DSN. |
| `APP_ENV` | `development` | `development`, `staging`, `production`, or `test`. A production boot refuses the default key. |
| `PORT` | `8000` | API port. |
| `JEV_API_KEY` | empty | Key for the cheap triage classifier. |
| `FAST_LLM_MODEL` | `gpt-4o-mini` | Model for summaries and compaction. |
| `REASONING_LLM_MODEL` | `muse-spark-1.3-contributor` | Model for the tool loop. |
| `EMBEDDING_MODEL` | `bge-small-en-v1.5` | Local int8 ONNX embedder, 384 dimensions, CLS pooling. |
| `MEMORY_RETRIEVAL_HALF_LIFE_DAYS` | `30.0` | Decay rate for memory scores. |
| `SANDBOX_IMAGE` | `vigil-sandbox:latest` | Image for the sandbox runner. |
| `WORKSPACE_BASE_PATH` | `/var/vigil/workspaces` | Host path for workspace snapshots. |

Money is stored as `cost_micros`, an integer count of micro-USD. No column holds a float.

## API surface

The service exposes 46 paths under `/api/v1`. Every module follows the same contract: a `repository.py` of Protocols, a `service.py` with the mechanism, and a `build_<resource>_router(settings)` factory.

| Module | Example paths |
| --- | --- |
| `health` | `GET /health`, `GET /health/live`, `GET /health/ready` |
| `auth` | `POST /auth/pair`, `GET /auth/me`, `POST /auth/revoke` |
| `cards` | `GET /cards`, `POST /cards`, `PATCH /cards/{id}`, `POST /cards/{id}/feedback` |
| `triage` | `GET /triage/recent`, `POST /triage/items/{id}` |
| `runs` | `POST /runs`, `GET /runs/{id}/steps`, `GET /runs/{id}/events`, `POST /runs/{id}/cancel` |
| `approvals` | `GET /approvals`, `POST /approvals/{id}/decision` |
| `chat` | `GET /chat/sessions`, `GET /chat/sessions/{id}/stream` (SSE) |
| `artifacts` | `POST /artifacts`, `GET /artifacts/{id}/download` |
| `memory` | `POST /memory/search`, `DELETE /memory/chunks/{id}`, `PATCH /memory/chunks/{id}/exclude` |
| `compaction` | `POST /compaction/dry-run`, `POST /compaction/run/{id}` |
| `skills` | `GET /skills`, `POST /skills/install`, `PATCH /skills/{id}/toggle` |
| `connectors` | `GET /connectors/accounts`, `POST /connectors/accounts/{id}/sync` |
| `jobs` | `GET /jobs`, `POST /jobs/enqueue` |
| `settings` | `GET /settings`, `GET /settings/limits`, `GET /settings/audit-logs` |

Every error uses one envelope:

```json
{
  "code": "RESOURCE_NOT_FOUND",
  "message": "The requested card was not found",
  "request_id": "c678d3cc-6998-4027-8b57-7540a1659011",
  "details": null
}
```

The response also echoes `X-Request-ID` and `X-Process-Time`.

## Project layout

```text
vigil/
├── backend/          FastAPI service, worker, migrations, tests
├── web/              React 19 + Vite client
├── connectors/       MCP connector servers (Gmail today)
├── deploy/           Docker Compose stack
├── docs/             Architecture, reviews, benchmarks
├── mobile/           Flutter client, planned
└── scripts/          Benchmark harness
```

## Measured limits

The project records measurements, not estimates.
These numbers come from `docs/BENCHMARKS.md` on a 2 vCPU host with real PostgreSQL and a real Docker daemon.

| Measurement | Measured | Target | State |
| --- | --- | --- | --- |
| Recall@50 at 100k rows | 0.561 (`ef_search` 200), 0.585 at 1000 | 0.95 | Open |
| Indexer throughput | 4.08 chunks/s | n/a | Encoder fix merged, re-measure pending |
| Peak indexer memory | 395 MB | 350 MB | Encoder fix merged, re-measure pending |
| Sandbox escape surface | fixed schema, no host socket | no shell on host | Passing |

Read [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md) before you quote a number.
The pre-fix figures are the baseline, not a current verdict.

## Status

Vigil is alpha software. The backend and the web client run end to end on `main`.

- [x] Module scaffold, 14 domain modules, uniform error contract
- [x] PostgreSQL schema, migrations `0001` to `0003`, job queue with heartbeats
- [x] Stateless run loop and multi-tool barrier
- [x] Docker sandbox runner with snapshot and resume
- [x] `pgvector` memory, time decay, and two-tier compaction
- [x] Gmail MCP connector
- [x] Device pairing with hashed tokens
- [x] Web client: briefing, chat, settings
- [ ] Recall tuning for the HNSW index
- [ ] Flutter mobile client
- [ ] Calendar, Slack, and GitHub connectors

## Documentation

| Document | Content |
| --- | --- |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Full technical specification: schema, subsystems, roadmap |
| [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md) | Recall, throughput, memory, and sandbox measurements with commands |
| [`docs/SPEC-REVIEW.md`](docs/SPEC-REVIEW.md) | Architecture defects with an acceptance test for each fix |
| [`docs/PHASE1-REVIEW.md`](docs/PHASE1-REVIEW.md) | Scaffold review and the authentication bypass it found |
| [`design.md`](design.md) | Web client design system |
| [`TEST_INFRA.md`](TEST_INFRA.md) | Test layers, fixtures, and services |

## Contributing

Read [`CONTRIBUTING.md`](CONTRIBUTING.md) before you open a pull request.
Short version:

1. Open an issue that describes the defect or the feature.
2. Branch from `main`.
3. Keep the module contract: Protocols in `repository.py`, mechanism in `service.py`, a router factory in `router.py`.
4. Run `env -u PYTHONPATH uv run pytest -q` in `backend/`.
5. Use Conventional Commits and one atomic change per commit.

Report a vulnerability through [`SECURITY.md`](SECURITY.md). Do not open a public issue for a security defect.

## License

Apache License 2.0. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).

<div align="center">

Built by [Dauda Nasir](https://github.com/awun8191) · [LinkedIn](https://www.linkedin.com/in/nasir-dauda-729357361)

</div>
