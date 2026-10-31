# Vigil (virgil-agent) - Specification Review

**Document:** SPEC-REVIEW.md
**Status:** Draft review of Draft 0.1
**Scope:** Architecture, data model, queue, security, retrieval
**Date:** 2026-10-31

---

## 0. How to read this document

Part A answers your embedding question with real numbers.
Part B lists the defects that block Phase 1 and Phase 2.
Part C gives the corrected schema.
Part D gives the queue, security, and retrieval changes.
Part E gives the revised phase gates.
Every fix has an acceptance test. Do not mark a fix done without the test.

---

# Part A - Embedding model decision

## A.1 The question

You asked to compare EmbeddingGemma at 300M parameters against smaller models under 100 MB.
The answer depends on three things:

1. The length of one chunk.
2. The language mix.
3. The CPU that runs the indexer.

## A.2 Measured numbers

Table A.1 gives retrieval quality. The metric is MTEB Retrieval nDCG@10 on the 15 English retrieval datasets.
Source: Snowflake Arctic Embed model card comparison tables.

| Model | Params | Dims | MTEB Retrieval nDCG@10 | License |
|---|---|---|---|---|
| snowflake-arctic-embed-xs | 22M | 384 | 50.15 | Apache 2.0 |
| snowflake-arctic-embed-s | 33M | 384 | **51.98** | Apache 2.0 |
| bge-small-en-v1.5 | 33M | 384 | 51.68 | MIT |
| cohere-embed-english-light-v3.0 | n/a | 384 | 51.34 | proprietary |
| text-embedding-3-small | n/a | 1536 | 51.08 | proprietary |
| snowflake-arctic-embed-m | 110M | 768 | 54.90 | Apache 2.0 |
| snowflake-arctic-embed-l | 335M | 1024 | 55.98 | Apache 2.0 |
| e5-small-v2 | 33M | 384 | 49.04 | MIT |
| all-MiniLM-L6-v2 | 22M | 384 | 41.95 | Apache 2.0 |
| gte-tiny | 17M | 384 | 44.92 | MIT |
| bge-micro-v2 | 17M | 384 | 42.56 | MIT |

Three results matter:

1. A 33M parameter model beats `text-embedding-3-small` at 1536 dimensions. Size is not the limit here.
2. `all-MiniLM-L6-v2` is the popular default. It scores 10 points lower. Do not use it.
3. The 22M and 17M models lose 6 to 8 points. They are below the floor for a memory system.

Table A.2 gives the EmbeddingGemma profile.
Source: Google Gemma model card and the Hugging Face release post.

| Property | Value |
|---|---|
| Parameters | 308M |
| Dimensions | 768, with MRL truncation to 512, 256, 128 |
| Context window | 2048 tokens |
| Languages | 100+ |
| Quantized RAM | under 200 MB (int8) |
| MTEB English v2, Mean (Task), 768d | 69.67 |
| MTEB English v2, Mean (Task), 512d | 69.18 |
| MTEB English v2, Mean (Task), 256d | 68.37 |
| MTEB English v2, Mean (Task), 128d | 66.66 |

Note the metric difference. Table A.1 uses retrieval nDCG@10. Table A.2 uses MTEB English v2 Mean (Task).
Do not compare the two columns directly.

## A.3 Static embeddings are not viable

Model2Vec and Potion produce vectors without a transformer forward pass.
They are very fast. The retrieval quality is too low for Vigil.

Source: Minish model2vec results page.

| Model | Retrieval score |
|---|---|
| all-MiniLM-L6-v2 (baseline) | 42.92 |
| potion-retrieval-32M | 35.06 |
| potion-base-32M | 32.67 |

`potion-retrieval-32M` reaches 81.7 percent of `all-MiniLM-L6-v2`. That baseline is already weak for memory recall.
Reject static embeddings for the memory module. They are valid only for a coarse first-pass filter.

## A.4 The real trade-off

The decision is not parameter count. The decision is chunk length and language.

**Argument for a 33M model at 384 dimensions**

- It matches or beats `text-embedding-3-small` on retrieval.
- It runs on CPU without a GPU.
- The artifact is 34 MB. The process is about 270 MB during a batch. See A.9.
- It is Apache 2.0 or MIT in every candidate.
- Your chunks are 4 to 6 conversation turns. Short text is the easy case.

**Argument for EmbeddingGemma at 308M**

- Context window is 2048 tokens. The small models stop at 512. A 6 turn chunk with a long email body exceeds 512 tokens. The tail is lost without an error.
- It covers 100+ languages. A Nigerian user receives English, Yoruba, Hausa, Igbo, and Pidgin text. A 33M English model degrades on the last four.
- MRL lets you store 512 or 256 dimensions. Storage grows 33 percent against a 384 dimension vector. That is small.
- The indexer is asynchronous. Throughput does not touch the user response path.

**Resolution of the two arguments above**

1. The 2048 token window does not matter if the chunker caps a chunk at 512 tokens. Add that cap to the chunking job and the advantage goes away.
2. The language argument depends on the users. The owner states that the users write English. The advantage goes away.
3. What remains is memory. The 33M model wins by a wide margin on a 2 GB host.

**Argument against both**

- Changing the model later forces a full reindex. Design for this on day one. See A.6.

## A.5 Decision record

**Status:** Decided 2026-10-31, revised the same day.
**Decision:** `BAAI/bge-small-en-v1.5`, 384 dimensions, int8 ONNX.
**Rejected:** EmbeddingGemma 300M, on memory cost.
**Not selected:** `Snowflake/snowflake-arctic-embed-s`, on a measured margin difference only.

Rationale from the owner:

1. The quality gap against EmbeddingGemma does not justify the memory cost. A 300 MB to 700 MB process is not possible on a 2 GB machine.
2. The users write English. The multilingual argument for EmbeddingGemma does not apply.
3. The BGE family is preferred. The larger siblings share the tokenizer and the input format, so an upgrade is a model swap and a reindex.

### A.5.1 Artifact provenance

This point is a supply chain risk, not a detail.

| Repository | File | Size |
|---|---|---|
| `BAAI/bge-small-en-v1.5` | `onnx/model.onnx` (fp32 only) | 133.09 MB |
| `Xenova/bge-small-en-v1.5` | `onnx/model_int8.onnx` | **33.76 MB** |
| `Xenova/bge-small-en-v1.5` | `onnx/model_uint8.onnx` | 33.76 MB |
| `Xenova/bge-small-en-v1.5` | `onnx/model_quantized.onnx` | 34.01 MB |

The official repository ships fp32 only. The 34 MB artifact is a community quantization.
Pin the mirror by revision and record the checksum in the repository.

```
repo      : Xenova/bge-small-en-v1.5
revision  : ea104dacec62c0de699686887e3f920caeb4f3e3
file      : onnx/model_int8.onnx
size      : 33.76 MB
sha256    : bf64d05457cb391fa88d045faf5927a15ea36d96228ddf23ea970087afdc1197
```

Add a startup check. The loader compares the file checksum against the pinned value. A mismatch stops the worker with a clear error.
A silent model swap is worse than a crash.

### A.5.2 Configuration

BGE uses CLS pooling and a query-only instruction. Both are mandatory.

```python
# src/config/settings.py (excerpt)
class EmbeddingSettings(BaseModel):
    model_id: str = "Xenova/bge-small-en-v1.5"
    revision: str = "ea104dacec62c0de699686887e3f920caeb4f3e3"
    artifact: str = "onnx/model_int8.onnx"
    artifact_sha256: str = "bf64d05457cb391fa88d045faf5927a15ea36d96228ddf23ea970087afdc1197"
    dimensions: int = 384
    max_tokens: int = 512
    pooling: Literal["cls"] = "cls"
    normalize: bool = True
    query_prefix: str = "Represent this sentence for searching relevant passages: "
    document_prefix: str = ""
    batch_size: int = 32
    threads: int = 2
    idle_unload_seconds: int = 300
```

Three contract tests, all cheap, all guarding a silent quality loss:

```python
def test_pooling_is_cls(embedder):
    # CLS pooling is required. Mean pooling degrades this model without an error.
    assert embedder.settings.pooling == "cls"

def test_document_has_no_prefix(embedder):
    assert embedder.settings.document_prefix == ""

def test_query_has_instruction(embedder):
    assert embedder.settings.query_prefix.startswith("Represent this sentence")
```

**Fallback path.** Keep the `EmbeddingModel` Protocol. `arctic-embed-s` works with a mean pooling adapter and a `passage: ` document prefix.
The reindex job in A.6 handles the swap. No code change is necessary.

## A.6 The dimension change problem

These three rules prevent a painful migration.

**Rule 1. Dimensions are configuration, never a literal.**
`Vector(1536)` in `src/schemas/memory.py` is a defect. Read the value from settings at migration time. Write the value into the migration file. Never import settings into a model.

**Rule 2. Store the vector in a column that matches one model version.**
Add `embedding_model` and `embedding_dim` to `memory_chunks`. The specification has `embedding_model` only. Add the dimension.

**Rule 3. Reindex is a job, not a downtime event.**
Add a `reindex_memory` job type. The job reads chunks where `embedding_model` differs from the active model. It recomputes in batches and updates the row. Retrieval filters on the active `embedding_model`, so half-migrated data never mixes vector spaces.

```sql
ALTER TABLE memory_chunks
  ADD COLUMN embedding_dim int NOT NULL DEFAULT 384;
CREATE INDEX ix_memory_chunks_model_dim ON memory_chunks (embedding_model, embedding_dim);
```

## A.7 Prefixes are not optional

Every candidate model uses instruction prefixes. Skip them and retrieval quality drops without any error.

| Model | Query prefix | Document prefix |
|---|---|---|
| arctic-embed-s / e5 family | `query: ` | `passage: ` |
| bge-small-en-v1.5 | `Represent this sentence for searching relevant passages: ` | none |
| EmbeddingGemma | task prompt, per model card | task prompt, per model card |

Add a test that asserts the prefix is applied. A silent regression here is invisible in normal use.

## A.8 Benchmark the host, do not guess

Run this on the target VPS before you commit to a model. It prints chunks per second and peak RSS.
Appendix C has the full script.

Expected order of magnitude: the 33M model is several times faster than the 308M model on CPU. Measure, do not assume.
The indexer is a background job, so a slow model costs latency on card generation, not on the chat path.

## A.9 Measured footprint

I ran both candidates on this host, on the same 9 document retrieval set with 5 queries.
The scripts are in Appendix C.

| Measurement | bge-small-en-v1.5 int8 | arctic-embed-s int8 |
|---|---|---|
| Artifact size | 33.76 MB | 34.02 MB |
| Pooling | CLS | mean |
| Top-1 accuracy | **5/5** | 5/5 |
| Mean score margin | **+0.163** | +0.109 |
| Correct answer scores | 0.679 to 0.801 | 0.855 to 0.895 |
| Best wrong scores | 0.561 to 0.615 | 0.732 to 0.797 |
| Throughput, batch 32 | 82 to 105 chunks/s | 91 chunks/s |
| RSS after load | 144 MB | 141 MB |
| RSS after 150 chunks | 270 MB | 233 MB |

Four conclusions.

**1. Both models answer every query correctly.** Quality is a tie on this small set. bge is not a downgrade.

**2. bge separates the right answer further.** The margin is 50 percent larger. Separation matters more than raw score, because the retrieval gate and the decay override both depend on a gap.

**3. The file size misleads you.** The artifact is 34 MB. The process is 144 MB after load and 270 MB during a batch. The ONNX runtime arena and the buffers dominate.
Plan for 300 MB peak, not 34 MB. This is the number that matters on a 2 GB machine.

Throughput varies between runs on a shared host. Treat the figure as a range, not a constant.

**4. The score band is low.** Correct answers score 0.679 to 0.801. A threshold of 0.85 never fires for this model.
For arctic the band sits higher, at 0.855 to 0.895, so the same threshold fires differently on each model. This settles the calibration question in D.4.

**Lazy load removes the standing cost.** The embedder belongs in the worker process only, and only while an index job runs.

```python
# src/integrations/embedding/local.py (excerpt)
class LocalEmbedder:
    def __init__(self, settings: EmbeddingSettings) -> None:
        self._settings = settings
        self._session: ort.InferenceSession | None = None
        self._last_used = 0.0

    def _ensure(self) -> ort.InferenceSession:
        if self._session is None:
            self._session = ort.InferenceSession(self._settings.model_path)
        self._last_used = time.monotonic()
        return self._session

    def release_if_idle(self) -> None:
        if self._session and time.monotonic() - self._last_used > self._settings.idle_unload_seconds:
            self._session = None
```

Set `intra_op_num_threads = 2`. A 2 core host must not give every thread to the embedder.
After the idle unload the worker returns to its baseline of about 90 MB. Vigil then costs nothing between indexing runs.

**The 2 GB budget is the real constraint.** A rough allocation for a developer laptop:

| Component | Budget |
|---|---|
| Node and the editor | 600 MB |
| Postgres, `shared_buffers` 128 MB | 300 MB |
| API process | 120 MB |
| Worker process, idle | 90 MB |
| Worker process, indexing | 300 MB for under 30 seconds |
| Docker daemon for the sandbox | 400 MB and up |
| **Total** | about 1.8 GB, before the sandbox |

The sandbox is what breaks a 2 GB machine, not the embedder. This decision is deferred to Phase 4 by agreement.
Record it as an open item in Part F. Do not let it block Phase 1.

---

# Part B - Blocking defects

Each item gives the defect, the failure mode, the fix, and the acceptance test.

## B1. The socket proxy grants host root

**Defect.** Section 11.2 routes Docker calls through `tecnativa/docker-socket-proxy` and allows `POST /containers/create`.
That proxy filters HTTP routes. It does not filter the request body.

**Failure mode.** The request body is the full container specification. The agent can set `Privileged: true`, bind mount `/`, or set `PidMode: host`.
A read-only filesystem flag on the intended container does not matter. The agent controls the next container.
This converts a sandbox escape into full host compromise.

**Fix.** Do not proxy the socket. Write a small runner service with a fixed request schema.

```python
# src/integrations/sandbox/contract.py
class SandboxSpec(BaseModel):
    image: str                      # must match SANDBOX_IMAGE_ALLOWLIST
    command: list[str]              # argv, never a shell string
    timeout_seconds: int = 300
    memory_mb: int = 1024
    cpu_quota: float = 1.0
    network: Literal["none"] = "none"
```

The runner builds the container config on the server side. The caller cannot express a bind mount, a capability, or a host namespace.
The runner never accepts a raw Docker payload.

**Acceptance test.** A request that contains `"HostConfig": {"Privileged": true}` is rejected with a validation error before any Docker call.

## B2. The reaper steals live jobs

**Defect.** Section 8.2 resets jobs where `locked_at < NOW() - INTERVAL '5 minutes'`.
The worker never updates `locked_at` after the claim.

**Failure mode.** A sandbox build that takes 15 minutes is reclaimed at minute 5. A second worker claims it. Both workers run the same job.
Two sandbox containers write to the same workspace. Two emails can be sent. The `attempts` counter increments on a job that never failed.

**Fix.** Add `heartbeat_at`. The worker updates it every 30 seconds while the job runs. The reaper uses `heartbeat_at`.

```sql
ALTER TABLE jobs ADD COLUMN heartbeat_at timestamptz;
CREATE INDEX ix_jobs_reap ON jobs (status, heartbeat_at) WHERE status = 'locked';
```

```sql
UPDATE jobs
SET status = 'pending', locked_by = NULL, locked_at = NULL, heartbeat_at = NULL
WHERE status = 'locked'
  AND COALESCE(heartbeat_at, locked_at) < NOW() - INTERVAL '90 seconds';
```

The limit is 3x the heartbeat interval. A container that dies without a clean shutdown recovers in 90 seconds.
Set `attempts` on claim, and treat a reaped job as a failed attempt only after the third reap.

**Acceptance test.** Start a 6 minute job. Run the reaper every 60 seconds. Assert exactly one execution completes and `attempts` equals 1.

## B3. The queue has no priority

**Defect.** Section 8.1 orders by `run_at ASC` only.

**Failure mode.** A user approves an email. The resume job enters the queue behind 500 connector poll jobs.
The user sees a spinner for minutes after an action that should take one second. This is the most visible failure in the product.

**Fix.** Add a priority column. Every job type gets a fixed priority.

| Job type | Priority |
|---|---|
| `resume_run` | 100 |
| `execute_run` | 90 |
| `index_memory` | 50 |
| `triage_item` | 60 |
| `poll_connector` | 20 |
| `cleanup_sandbox` | 10 |
| `reindex_memory` | 5 |

```sql
ALTER TABLE jobs ADD COLUMN priority int NOT NULL DEFAULT 0;
DROP INDEX IF EXISTS ix_jobs_status_run_at;
CREATE INDEX ix_jobs_claim ON jobs (priority DESC, run_at ASC) WHERE status = 'pending';
```

Add aging. Add `(NOW() - run_at)` in seconds to the sort key, capped at 3600. A low priority job cannot starve forever.

**Acceptance test.** Enqueue 1000 poll jobs, then one resume job. Assert the resume job is claimed first.

## B4. Tool execution runs in the API process

**Defect.** The Section 6 sequence diagram shows the SSE gateway calling MCP after approval.
The API process holds the OAuth token and the sandbox client.

**Failure mode.** A slow Gmail call blocks a FastAPI worker. The API container needs Docker access and connector credentials.
You now have two execution paths with different failure handling. The stateless claim breaks: the approval is only durable if the API process survives the call.

**Fix.** The decision endpoint writes the approval row and enqueues a job. It returns 202. Only the worker calls MCP.

```python
# src/modules/approvals/service.py (excerpt)
async def decide(self, approval_id: UUID, decision: str, note: str | None) -> None:
    approval = await self.repo.get_for_update(approval_id)
    if approval.status != "pending":
        raise ConflictError("APPROVAL_ALREADY_DECIDED")
    approval.status = "approved" if decision == "approve" else "rejected"
    approval.decided_at = utcnow()
    approval.decision_note = note
    await self.jobs.enqueue("resume_run", {"run_id": approval.run_id}, priority=100)
```

**Acceptance test.** Kill the API container immediately after the 202 response. The approval still executes after restart.

## B5. The background placeholder breaks the barrier

**Defect.** Section 6 writes `{"status": "enqueued"}` as the tool result for `C2`.
The barrier check then passes, because a row exists with the right `tool_call_id`.

**Failure mode.** The barrier claims to guarantee that every tool call ID has a real result. The placeholder violates that guarantee.
The model proceeds and reasons over a lie. It may plan a document compile and then reference a file that does not exist yet.
Worse, the run continues while a background job still writes to the same workspace. The snapshot on pause captures a half-written state.

**Fix.** Use a real state. Do not fabricate a result.

```
status = 'awaiting_background_job'
```

The job completion writes the true tool result and enqueues `resume_run`.
The barrier runs on resume, not on enqueue.

If a background job must not block the turn, the correct design is a notification step, not a fake tool result.
Give the model an explicit tool that returns immediately, such as `start_background_task(task_id)`, and a second tool `check_task(task_id)`.
The model decides when to check. The barrier is never lied to.

**Acceptance test.** A run with one immediate tool, one background tool, and one approval resumes exactly once, after all three results exist.

## B6. The SSE transport is undefined

**Defect.** The diagram shows `DB-->>SSE: Stream Event`. No section defines how the API process learns about a worker event.

**Failure mode.** Two implementers read this and build different systems. One polls. One adds Redis, which contradicts tenet 3.
The resumable stream also needs a cursor. `run_steps` has the index for it, but nothing uses it.

**Fix.** Define it. Two options.

**Option 1, cursor polling (simplest).** The endpoint reads `run_steps` with `step_number > last_event_id` every 500 ms. It sets `Last-Event-ID` support so a reconnect resumes. It sends a keepalive comment every 15 seconds.

**Option 2, LISTEN/NOTIFY (lower latency).** The worker sends `NOTIFY run_events, '<run_id>'`. The API holds one connection per process and fans out to local subscribers. Keep the polling path as a fallback.

Start with option 1. It survives restarts and needs no extra process. The 500 ms delay is invisible against a 5 second model call.

```
GET /api/v1/runs/{run_id}/events
Last-Event-ID: 42
```

```sql
SELECT step_number, event_type, payload, created_at
FROM run_steps
WHERE run_id = :run_id AND step_number > :last_event_id
ORDER BY step_number ASC
LIMIT 200;
```

**Acceptance test.** Reconnect mid-run with `Last-Event-ID`. No event is duplicated and no event is lost.

## B7. The table name mixin produces the wrong names

**Defect.** Section 5.1 derives `__tablename__` as `f"{cls.__name__.lower()}s"`.

**Verified.** I ran the derivation:

```
MemoryChunk    -> memorychunks
Cards          -> cardss
AgentConfig    -> agentconfigs
TriageResult   -> triageresults
```

**Failure mode.** The migration creates `memorychunks`. Every native query, index name, and Alembic autogenerate diff in the specification uses `memory_chunks`.
Alembic then wants to drop and recreate every table on the next autogenerate.

**Fix.** Declare the name on each model. Do not derive it.

```python
class MemoryChunk(Base):
    __tablename__ = "memory_chunks"
```

Enforce it with a test.

```python
def test_all_tables_have_explicit_names():
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        assert cls.__dict__.get("__tablename__"), cls.__name__
        assert cls.__tablename__.islower()
        assert "_" in cls.__tablename__ or cls.__tablename__.isalpha()
```

**Acceptance test.** `Base.metadata.tables.keys()` equals the 21 expected names, exactly.

## B8. HNSW recall collapses under the tenant filter

**Defect.** Section 5.6 creates one HNSW index on `embedding`. Section 7.3 filters on `user_id` and `scope`, then takes `LIMIT 50`.

**Failure mode.** pgvector applies the filter after the approximate scan. With many users, the scan returns 50 rows across all tenants, and the filter keeps 3.
Recall drops without any error. Results look plausible and are wrong. The time decay formula then ranks a poor candidate set precisely.

**Fix.** Raise the search effort and enable iterative scans. pgvector 0.8.0 added iterative index scans. Current release is 0.8.7.

```sql
SET LOCAL hnsw.ef_search = 200;
SET LOCAL hnsw.iterative_scan = relaxed_order;
SET LOCAL hnsw.max_scan_tuples = 40000;
```

Also consider a partial index per active user when the user count is small. For a self-hosted single-user deployment, the filter admits most rows and the fix is unnecessary. Add the settings anyway. They cost nothing.

**Acceptance test.** With 100k rows across 100 users, recall@50 against a brute force scan is at least 0.95.

---

# Part C - Corrected schema

## C.1 Table count

The document says 16 tables. It defines 17. The corrected count is 21 after the additions below.

| Group | Tables |
|---|---|
| Identity | users, devices |
| Connectors | connector_accounts, sync_state, items |
| Triage | triage_results, cards |
| Runtime | agent_configs, runs, messages, approvals, artifacts, run_steps |
| Memory | memory_chunks, skills |
| System | jobs, audit_log |
| **New** | chat_sessions, idempotency_keys, run_workspaces, connector_tools |

## C.2 Missing and wrong columns

| Table | Problem | Correction |
|---|---|---|
| `messages` | Section 7.1 marks rows with `indexed_at`. No column exists. Add `indexed_at timestamptz` | Blocking for memory indexing |
| `runs` | No snapshot path. Resume cannot find the tarball | Add `workspace_ref text` |
| `runs` | `cost_usd float` breaks the integer money rule | Rename to `cost_micros bigint` |
| `runs` | `stale_thread_paused` appears in the enum and is defined nowhere | Define it or remove it. See C.4 |
| `memory_chunks` | `scope = 'card'` but no `card_id` | Add `card_id uuid` |
| `memory_chunks` | `scope = 'agent'` but no agent reference | Add `agent_config_id uuid` |
| `memory_chunks` | No dimension column, so reindex cannot verify the vector | Add `embedding_dim int` |
| `jobs` | No priority, no heartbeat | See B2 and B3 |
| `triage_results` | `item_id` is UNIQUE, so a re-triage fails | Make it UNIQUE(item_id, model_version) |
| `connector_accounts` | `enabled_tools` is a list of strings with no policy | Move to `connector_tools` |
| `items` | Section 10 says dedupe "via unique index" but the index is on `external_id` | Correct the text. Thread bundling belongs to `cards` |

## C.3 New tables

```sql
CREATE TABLE chat_sessions (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users(id),
    title text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_chat_sessions_user ON chat_sessions (user_id, updated_at DESC);

CREATE TABLE idempotency_keys (
    key text PRIMARY KEY,
    endpoint text NOT NULL,
    request_hash text NOT NULL,
    response_status int,
    response_body jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_idempotency_created ON idempotency_keys (created_at);

CREATE TABLE run_workspaces (
    run_id uuid PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    archive_path text NOT NULL,
    size_bytes bigint NOT NULL,
    checksum text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE connector_tools (
    id uuid PRIMARY KEY,
    connector_account_id uuid NOT NULL REFERENCES connector_accounts(id) ON DELETE CASCADE,
    tool_name text NOT NULL,
    level text NOT NULL CHECK (level IN ('read', 'local', 'external')),
    enabled boolean NOT NULL DEFAULT true,
    UNIQUE (connector_account_id, tool_name)
);
```

## C.4 The undefined run state

`stale_thread_paused` appears once, in the enum list. Nothing explains it.
Two readings are possible. The first is a run that paused because the source email thread received a new reply. The second is a run whose approval was superseded.

Write the rule, or delete the state. A state without a transition is a bug in waiting.
If you keep it, define three things: the trigger, the resume condition, and the card that the user sees.

## C.5 Money as integers

`cost_usd: float` contradicts the house rule. Floats accumulate error and compare badly.

```sql
ALTER TABLE runs RENAME COLUMN cost_usd TO cost_micros;
ALTER TABLE runs ALTER COLUMN cost_micros TYPE bigint USING (cost_usd * 1000000)::bigint;
```

Rules:

1. Every provider price is stored in micro-USD per 1000 tokens.
2. Every cost computation uses integer arithmetic.
3. Budget checks compare integers.
4. The API formats to USD at the edge, in one function.

```python
def to_usd(micros: int) -> str:
    return f"${micros / 1_000_000:.6f}"
```

## C.6 The shared JSONB instance

`JSONBType = JSONB()` is a module level singleton. One type object bound to many columns is a known source of duplication warnings and confusing DDL.

Use the class in each column.

```python
metadata_: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
```

---

# Part D - Queue, security, retrieval

## D.1 Claim query, corrected

```sql
WITH claimable AS (
    SELECT id
    FROM jobs
    WHERE status = 'pending' AND run_at <= NOW()
    ORDER BY priority DESC, (run_at + LEAST(3600, EXTRACT(EPOCH FROM (NOW() - run_at))) * INTERVAL '1 second') ASC
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
UPDATE jobs
SET status = 'locked',
    locked_by = :worker_id,
    locked_at = NOW(),
    heartbeat_at = NOW(),
    attempts = attempts + 1
FROM claimable
WHERE jobs.id = claimable.id
RETURNING jobs.*;
```

## D.2 Retry policy

The Section 8.2 formula is sound. Add three constraints.

1. Cap the delay at 3600 seconds. The formula already does this.
2. Add full jitter, not a fixed offset. `delay = random(0, min(cap, base * 2 ** attempts))`.
3. Classify the error. A validation error is not retryable. A connection reset is. Retry only the second class.

| Error class | Attempts | Action |
|---|---|---|
| `SandboxUnavailable` | 3 | Retry |
| `ProviderRateLimited` | 5 | Retry with backoff |
| `ConnectorAuthExpired` | 1 | Mark account `needs_reauth`, notify user |
| `ValidationError` | 1 | Fail immediately |
| `BudgetExceeded` | 1 | Fail, emit card |

## D.3 Sandbox hardening, corrected

Section 11 is close. These points need a decision.

| Control | Current text | Correction |
|---|---|---|
| Socket | Proxy with `POST /containers/create` | Fixed schema runner (B1) |
| User | UID 1000 | Also set `--user 1000:1000` explicitly. Do not rely on the image |
| Root fs | `--read-only` | Keep. Add `--tmpfs /tmp:rw,noexec,nosuid,size=512m` |
| Capabilities | `--cap-drop=ALL` | Keep. Add `--security-opt no-new-privileges` |
| Seccomp | "Default profile" | Record the profile hash in `audit_log` on each run |
| Network | `--network=none` | Keep. An allowlist proxy defeats the purpose if the agent can set `HTTP_PROXY` |
| Memory | Not specified | Set `--memory 1g --memory-swap 1g` |
| CPU | Not specified | Set `--cpus 1.0` |
| PIDs | Not specified | Set `--pids-limit 256`. Prevents a fork bomb |
| Disk | Not specified | Quota the workspace volume. 2 GB per run |

Workspace archives need a lifecycle policy. Section 11 has no cap.
Add: delete archives older than 7 days, delete archives for `completed` runs after 24 hours, alert at 80 percent of the volume.

## D.4 Retrieval, corrected

The formula in Section 7.2 is correct and well chosen. Three changes.

**Change 1. Calibrate the override threshold.**
0.85 is a guess. Cosine similarity depends on the model. A 384 dimension model has a different score distribution from a 768 dimension model.
Run this once per model on 50 labeled pairs. Set the threshold at the 95th percentile of the negative pairs.
The measured correct-answer band for bge-small-en-v1.5 is 0.679 to 0.801. The best wrong answers score 0.561 to 0.615.
A threshold of 0.85 never fires. A threshold near 0.65 sits inside the measured gap and is a defensible start.
If you cannot label, use a percentile gate at query time: bypass decay only for the top 2 candidates when their score exceeds the 95th percentile of that user's recent matches.

**Change 2. Separate the score from the presentation.**
Return `similarity`, `final_score`, and `age_days` in the API. Debugging a memory system without the components is guesswork.

**Change 3. Add a minimum similarity gate.**
The floor of 0.35 keeps old items alive. It also means a weak match from two years ago outranks nothing. Add `similarity >= 0.25` as a hard cut. Otherwise the model receives irrelevant memory and treats it as fact.

## D.5 Compaction, corrected

The 4-tier flow in Section 9 is sound. Four gaps.

1. **No token counter.** The trigger is "70 percent of window". Nothing counts tokens. Add a counter per model. Use the provider tokenizer, not a character estimate.
2. **Tier 1 cost is unbounded.** Three questions per unit, batched. Units can number in the hundreds. Cap the batch at 50 units per call and the total at 3 calls per compaction.
3. **Summary drift.** Each compaction summarizes the previous summary. Quality decays. Keep the original user messages in full (Tier 0 already does). Also store the summary chain in `run_steps` so you can trace it.
4. **No verification.** Add a check after compaction. The compacted context must still contain every unresolved approval and every tool call ID. Fail the run rather than send a broken history.

## D.6 The barrier, formalized

Your invariant is correct. Make it executable.

```python
async def assert_barrier(self, run_id: UUID) -> None:
    assistant = await self.repo.last_assistant_message(run_id)
    if not assistant or not assistant.tool_calls:
        return
    expected = {call["id"] for call in assistant.tool_calls}
    actual = await self.repo.tool_call_ids_after(run_id, assistant.id)
    missing = expected - actual
    if missing:
        raise InvariantViolation(f"BARRIER_INCOMPLETE: {sorted(missing)}")
```

Call it before every model invocation. A violation is a hard failure, not a retry. It means the state machine lost a tool call.

Also enforce ordering for Anthropic. Anthropic requires tool results in the turn immediately after the assistant message, in order. Group the tool messages by assistant turn at assembly time. Do not rely on insertion order across hours.

---

# Part E - Revised phase gates

The seven phases stay. These gates change.

| Phase | Added gate |
|---|---|
| 1 | Table name test passes. Pairing token is hashed, never stored plain |
| 2 | Concurrent claim test: 8 workers, 1000 jobs, zero duplicates, zero deadlocks |
| 3 | Replay test: a fixed mailbox produces a fixed card set, byte for byte |
| 4 | Sandbox escape test: privileged payload rejected. Barrier invariant test passes |
| 5 | Recall test at 100k rows. Reindex test migrates 10k chunks without downtime |
| 6 | Skill install from Git is pinned to a commit and verified |
| 7 | Offline demo runs with zero provider calls |

Add one phase 0 task. Build the benchmark in Appendix C and run it on the target host. The embedding decision depends on that number.

---

# Part F - Open questions

Answer these before Phase 3.

1. **Naming.** The document uses Vigil and Virgil. The repo is `virgil-agent`. Pick one.
2. **Single user or multi user?** Tenet 3 and the tenant filter in B8 assume one household. If this is multi-tenant SaaS, several decisions change.
3. **`stale_thread_paused`.** Define it or delete it.
4. **Approval expiry.** What happens to an approval older than 30 days? The spec has a `stale` status but no rule.
5. **Budget enforcement point.** `max_cost_usd` exists in `agent_configs`. Nothing checks it. Decide where the check runs.
6. **Connector failure UX.** A `needs_reauth` account is invisible to the user today. Which card tells them?
7. **Local sandbox on a 2 GB host.** Deferred to Phase 4 by agreement. Options are a remote sandbox, a 4 GB minimum, or no Docker on the client.
8. **Quantized artifact trust.** The int8 export comes from a community mirror. Decide whether the project vendors the file or downloads and verifies it at first run.

---

# Appendix A - Embedding comparison, one page

If you read nothing else:

- A 33M model at 384 dimensions matches `text-embedding-3-small` at 1536.
- `all-MiniLM-L6-v2` is 10 points worse. Do not use it.
- Static embeddings lose 19 percent of retrieval quality. Reject.
- EmbeddingGemma wins on context length and languages. It costs 9x the parameters and about 4x the RSS.
- Decision: bge-small-en-v1.5, int8, 384 dimensions. Measured at 82.2 chunks per second, 270 MB peak RSS, and a 5/5 top-1 result.
- 1536 dimensions is an OpenAI artifact. Delete it from the schema.
- Store the embedding model and the dimension on every row.
- Apply the prefixes. A missing prefix fails silently.

# Appendix B - Defect checklist

- [ ] B1 Sandbox runner uses a fixed schema, no raw Docker payload
- [ ] B2 heartbeat_at plus reaper at 90 seconds
- [ ] B3 priority column plus aging
- [ ] B4 approval decision enqueues a job, returns 202
- [ ] B5 awaiting_background_job replaces the fake tool result
- [ ] B6 SSE cursor over run_steps with Last-Event-ID
- [ ] B7 explicit __tablename__ on every model
- [ ] B8 hnsw.iterative_scan and ef_search set per query
- [ ] C1 messages.indexed_at
- [ ] C2 runs.workspace_ref
- [ ] C3 runs.cost_micros
- [ ] C4 memory_chunks.card_id and agent_config_id
- [ ] C5 triage_results unique on (item_id, model_version)
- [ ] C6 idempotency_keys table wired to the middleware
- [ ] C7 chat_sessions table
- [ ] C8 embedding artifact pinned by revision and checksum
- [ ] C9 CLS pooling and query-only prefix asserted in tests
- [ ] D1 token counter before compaction
- [ ] D2 barrier assertion before every model call
- [ ] D3 workspace quotas and cleanup job

# Appendix C - Host benchmark script

This is the script that produced A.9. It verifies the artifact, measures speed and RSS, and runs a small retrieval check.
Run it before you change the model or the pooling.

```python
import hashlib, os, sys, time, urllib.request
import numpy as np
import onnxruntime as ort
import psutil
from tokenizers import Tokenizer

REPO = "Xenova/bge-small-en-v1.5"
REV = "ea104dacec62c0de699686887e3f920caeb4f3e3"
FILE = "onnx/model_int8.onnx"
SHA256 = "bf64d05457cb391fa88d045faf5927a15ea36d96228ddf23ea970087afdc1197"
QPREFIX = "Represent this sentence for searching relevant passages: "
CACHE = "/tmp/bge"
os.makedirs(CACHE, exist_ok=True)

paths = {}
for key, remote in (("model", FILE), ("tok", "tokenizer.json")):
    p = os.path.join(CACHE, os.path.basename(remote))
    if not os.path.exists(p):
        urllib.request.urlretrieve(
            f"https://huggingface.co/{REPO}/resolve/{REV}/{remote}", p)
    paths[key] = p

digest = hashlib.sha256(open(paths["model"], "rb").read()).hexdigest()
assert digest == SHA256, f"artifact mismatch: {digest}"

tok = Tokenizer.from_file(paths["tok"])
tok.enable_truncation(max_length=512)
tok.enable_padding(pad_id=0, pad_token="[PAD]")
sess = ort.InferenceSession(paths["model"], providers=["CPUExecutionProvider"])
graph_in = [i.name for i in sess.get_inputs()]

def encode(batch, prefix=""):
    encs = tok.encode_batch([prefix + t for t in batch])
    ids = np.array([e.ids for e in encs], dtype=np.int64)
    mask = np.array([e.attention_mask for e in encs], dtype=np.int64)
    feed = {"input_ids": ids, "attention_mask": mask}
    if "token_type_ids" in graph_in:
        feed["token_type_ids"] = np.zeros_like(ids)
    out = sess.run(None, feed)[0]
    emb = out[:, 0, :]                      # CLS pooling. Required for BGE.
    return emb / np.linalg.norm(emb, axis=1, keepdims=True)

DOCS = [
    "Invoice 8921 from the cloud provider is overdue. Service suspension starts on November 2.",
    "The landlord meeting moved to Thursday at 10:00. The agent confirmed the new slot.",
    "Server X registration key is stored on the deployment card created in March.",
    "The Q3 report shows AWS spend dropped 14 percent quarter over quarter.",
    "My sister birthday is on the 12th of December. She prefers no gifts.",
    "Draft a reply to the recruiter asking about the backend role at the Lagos office.",
    "The D1 database migration must run before the API deploy on Friday.",
    "Buy a new HDMI cable and a replacement laptop charger.",
    "The electricity bill for September is 18,400 naira and it is already paid.",
]
QUERIES = [
    ("When does the cloud service get suspended?", 0),
    ("What is the AWS cost change for Q3?", 3),
    ("Where is the server registration key?", 2),
    ("When is the landlord meeting?", 1),
    ("What is the deadline for the D1 migration?", 6),
]

rss_load = psutil.Process().memory_info().rss / 1e6
doc_vecs = encode(DOCS)

hits, margins = 0, []
for q, target in QUERIES:
    s = doc_vecs @ encode([q], QPREFIX)[0]
    order = np.argsort(-s)
    hits += order[0] == target
    margins.append(s[target] - s[next(i for i in order if i != target)])

big = (DOCS * 17)[:150]
t0 = time.perf_counter()
encode(big)
dt = time.perf_counter() - t0
rss_peak = psutil.Process().memory_info().rss / 1e6

print(f"artifact   : {os.path.getsize(paths['model'])/1e6:.2f} MB")
print(f"top1       : {hits}/{len(QUERIES)}")
print(f"margin     : {np.mean(margins):+.3f} (min {min(margins):+.3f})")
print(f"throughput : {len(big)/dt:.1f} chunks/s")
print(f"rss        : {rss_load:.0f} MB load, {rss_peak:.0f} MB peak")
```

Expected result: 5/5 top-1, a margin near +0.17, about 100 chunks per second, and about 240 MB peak.
The exact figures vary with the host and the core count.

Install with `uv run --with onnxruntime --with tokenizers --with numpy --with psutil python bench.py`.

# Appendix D - References

- EmbeddingGemma model card, Google. https://ai.google.dev/gemma/docs/embeddinggemma/model_card
- EmbeddingGemma release post, Hugging Face. https://huggingface.co/blog/embeddinggemma
- EmbeddingGemma paper, arXiv 2509.20354. https://arxiv.org/abs/2509.20354
- bge-small-en-v1.5 model card. https://huggingface.co/BAAI/bge-small-en-v1.5
- bge-small-en-v1.5 int8 export (community mirror). https://huggingface.co/Xenova/bge-small-en-v1.5
- Arctic Embed model card and comparison tables. https://huggingface.co/Snowflake/snowflake-arctic-embed-s
- Arctic Embed paper, arXiv 2405.05374. https://arxiv.org/abs/2405.05374
- Model2Vec results. https://minish.ai/packages/model2vec/results/
- nomic-embed-text-v1.5 MRL. https://www.nomic.ai/news/nomic-embed-matryoshka
- pgvector changelog, 0.8.0 iterative scans and 0.4 index limits. https://github.com/pgvector/pgvector/blob/master/CHANGELOG.md
- MTEB leaderboard, static English copy. https://ai-northstar-tech.github.io/mteb-info/
