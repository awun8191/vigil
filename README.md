<div align="center">

<img src="assets/social-preview.png" alt="Vigil: a self-hosted proactive AI assistant with approval-gated execution" width="100%">

# Vigil

### A self-hostable proactive AI assistant with approval-gated sandboxed execution

Vigil watches your inbox, tasks, and deadlines. It triages each event with a cheap classifier,
sends you an action card, and runs the long jobs inside a Docker sandbox.
Every action that leaves your machine waits for your approval.

[Quickstart](#quickstart) · [Documentation](#documentation) · [Configuration](#configuration) · [Development](#development)

[![CI](https://github.com/awun8191/vigil/actions/workflows/ci.yml/badge.svg)](https://github.com/awun8191/vigil/actions/workflows/ci.yml)
![License](https://img.shields.io/badge/license-Apache--2.0-blue)
![Python](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16%20%2B%20pgvector-4169E1?logo=postgresql&logoColor=white)
![Tests](https://img.shields.io/badge/tests-612-informational)
![Status](https://img.shields.io/badge/status-alpha-orange)

</div>

---

## Quickstart

### Requirements

- Docker with the Compose plugin
- Python 3.12 or newer and [uv](https://docs.astral.sh/uv/)
- PostgreSQL 16 with the `pgvector` extension (Compose provides this)

### Run with Docker Compose (easiest)

```bash
git clone https://github.com/awun8191/vigil.git
cd vigil

export SECRET_KEY=$(openssl rand -hex 32)
docker compose -f deploy/docker-compose.yml up --build
```

The API listens on `http://localhost:8000`.

### Run with the startup script

The startup script starts the Web UI, the API, and the background worker together.

```bash
./start.sh
```

The script:
- Checks that uv, Node.js, and npm are installed
- Creates `backend/.env` from `backend/.env.example` if it does not exist
- Installs Python and npm dependencies
- Tests the PostgreSQL connection and runs schema migrations
- Starts the API, the web client, and the worker

Options:
- `./start.sh --no-worker` — Start only the Web UI and API
- `./start.sh --api-port 8080` — Change the API port
- `./start.sh --mock` — Run the frontend with fixture data (no backend required)
- `./start.sh --help` — List all options

### Pairing your device

1. Open the web UI at `http://localhost:5173`
2. Navigate to `http://localhost:5173/pair`
3. Look at the terminal where Vigil is running. The API prints a pairing code on startup:

   ```
   Temporary Pairing Code: VIG-7K2M4Q
   ```

4. Enter that code in the web UI to pair your device

The pairing code is one-time and expires after 5 minutes.
After pairing, your device gets a permanent bearer token.

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

To run everything in one process (local development only):

```bash
export EMBED_WORKER=true
uv run uvicorn src.main:app --reload --port 8000
```

### Run the web client

```bash
cd web
npm install
npm run dev          # talks to the API on port 8000 (proxies /api)
npm run dev:mock     # simulation mode with fixture data, no backend
```

### Run the tests

```bash
cd backend
env -u PYTHONPATH uv sync --quiet
env -u PYTHONPATH uv run pytest -q
```

The suite has 612 tests. Tests that need PostgreSQL or Docker skip when the
service is absent. Read the skip count before you read the pass count.

---

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

---

## Documentation

| Document | Content |
| --- | --- |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Full technical specification: schema, subsystems, roadmap |
| [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md) | Recall, throughput, memory, and sandbox measurements with commands |
| [`docs/SPEC-REVIEW.md`](docs/SPEC-REVIEW.md) | Architecture defects with an acceptance test for each fix |
| [`docs/design.md`](docs/design.md) | Web client design system |
| [`docs/TEST_INFRA.md`](docs/TEST_INFRA.md) | Test layers, fixtures, and services |

---

## Project layout

```text
vigil/
├── assets/           Social preview images
├── backend/          FastAPI service, worker, migrations, tests
│   └── connectors/   MCP connector servers (Gmail)
├── deploy/           Docker Compose stack
├── docs/             Architecture, reviews, benchmarks, guides
├── scripts/          Benchmark harness, helper scripts
└── web/              React 19 + Vite client
```

---

## Contributing

Read [`CONTRIBUTING.md`](docs/CONTRIBUTING.md) before you open a pull request.
Short version:

1. Open an issue that describes the defect or the feature.
2. Branch from `main`.
3. Keep the module contract: Protocols in `repository.py`, mechanism in `service.py`, a router factory in `router.py`.
4. Run `env -u PYTHONPATH uv run pytest -q` in `backend/`.
5. Use Conventional Commits and one atomic change per commit.

Report a vulnerability through [`SECURITY.md`](docs/SECURITY.md).

---

## License

Apache License 2.0. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).

<div align="center">

Built by [Dauda Nasir](https://github.com/awun8191) · [LinkedIn](https://www.linkedin.com/in/nasir-dauda-729357361)

</div>
