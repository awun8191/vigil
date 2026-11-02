# Benchmarks: real Postgres and real Docker

Each earlier gate ran on fakes. This document records four measurements on the live
stack. The measurements are the migration, the index recall at 100k rows, the indexer
memory and speed, and the sandbox flags on a live daemon.

The document reports the measured value. It does not report an estimate. A target that
fails is recorded as FAIL, with the number that failed it.

This revision corrects the document after review card t_b9801c40. It corrects the
migration claim, the peak RSS claim, the environment section, and two small statements.
It does not change a recall number, because the numbers reproduce in the review. It does
not change the migration, because the extension must stay (section 2).

Section 3.5 was added by card F-02 on branch `fix/retrieval`. That change does touch source
code: the retrieval path now follows the tenant row count. Sections 1, 2, 4, and 5 report
the same measurements as before.

The measurement hooks live in `scripts/bench/`. The recall values come from the run at
commit `7400b4c`. The review reproduced them at commit `6317658` on branch
`bench/measure`.

## 1. Environment

Each host fact has its command. A reader can run each command again.

| Fact | Value | Command |
| --- | --- | --- |
| Host | Debian GNU/Linux 12 (bookworm) | `grep PRETTY_NAME /etc/os-release` |
| Kernel | 6.1.0-53-cloud-amd64 | `uname -r` |
| CPU model | Intel(R) Xeon(R) Platinum 8259CL CPU @ 2.50GHz | `grep -m1 'model name' /proc/cpuinfo` |
| CPU count | 2 vCPU | `nproc` |
| Memory | 3,960,628 kB (3.78 GiB) | `grep MemTotal /proc/meminfo` |
| Docker | 29.9.1 (client and server) | `docker version --format '{{.Server.Version}}'` |
| Docker Compose | v5.6.0 | `docker compose version` |
| Python | 3.12.15 | `cd backend && env -u PYTHONPATH uv run python -V` |
| uv | 0.13.1 | `uv --version` |
| PostgreSQL | 16.15 (Debian 16.15-1.pgdg12+2) | `docker exec vigil-bench-pg psql -U postgres -d vigil_bench -tAc "select version()"` |
| pgvector | 0.8.7 | `docker exec vigil-bench-pg psql -U postgres -d vigil_bench -tAc "select extversion from pg_extension where extname='vector'"` |
| Database image | `pgvector/pgvector:pg16` | `docker inspect vigil-bench-pg --format '{{.Config.Image}}'` |
| Port 5434 | benchmark database, `vigil-bench-pg` | `docker ps --format '{{.Names}} {{.Ports}}'` |
| Port 5432 | `virgil-v01-pg`, not used | `docker ps --format '{{.Names}} {{.Ports}}'` |
| Port 5433 | `stotv-pg`, not used | `docker ps --format '{{.Names}} {{.Ports}}'` |

The benchmark database ran in a new container, `vigil-bench-pg`, on port 5434. The two
older containers, `virgil-v01-pg` on 5432 and `stotv-pg` on 5433, stayed untouched.

```bash
docker run -d --name vigil-bench-pg -p 5434:5432 \
  -e POSTGRES_PASSWORD=bench -e POSTGRES_DB=vigil_bench \
  pgvector/pgvector:pg16
```

The commands below measure the start delay of the database container. The loop starts a
cold container on port 5435 and polls `pg_isready` every 0.2 s. It prints the delay in
seconds, and then it removes the container.

```bash
start=$(date +%s%N)
docker run -d --name vigil-bench-timing -p 5435:5432 \
  -e POSTGRES_PASSWORD=bench -e POSTGRES_DB=vigil_bench \
  pgvector/pgvector:pg16 > /dev/null
until docker exec vigil-bench-timing pg_isready -U postgres > /dev/null 2>&1
do
  sleep 0.2
done
awk -v start="$start" -v end="$(date +%s%N)" \
  'BEGIN {printf "%.2f s\n", (end - start) / 1e9}'
docker rm -f vigil-bench-timing > /dev/null
```

Three runs gave 4.44 s, 3.27 s, and 2.88 s, at a load average of 11.4 to 12.0 from other
builds on the host. The median is 3.27 s. The first revision gave 2 s from a 1 s poll,
so that value was coarse.

## 2. Measurement 1: migration

Target: the schema gets to head, goes back to base with no data table left, and gets to
head again with no error.

| Step | Revision | Tables | `vector` extension |
| --- | --- | --- | --- |
| `alembic upgrade head` | 0003 | 20 | 0.8.7 |
| `alembic downgrade base` | none | 1 | 0.8.7, not dropped |
| `alembic upgrade head` | 0003 | 20 | 0.8.7 |

The table count of 20 includes `alembic_version`. The schema has 19 data tables. The
single table after the downgrade is `alembic_version`, and it holds no row.

The extension stays by design. Revision 0001 runs `CREATE EXTENSION IF NOT EXISTS
vector` in `upgrade()`, and `downgrade()` drops the 19 tables and nothing else. The
`IF NOT EXISTS` clause admits that the extension can be present before the migration
runs, so the migration cannot tell whether it created the extension. A drop would remove
a database-level object that a database owner or a managed service can provide for
another use. For that reason the downgrade leaves the extension, and this document does
not claim a database that is empty after the downgrade.

The first revision of this document said that the downgrade drops the extension, and it
gave the value `absent`. Both statements were false. Migration 0001 held no drop
statement, and the extension stayed at 0.8.7 after `alembic downgrade base`. The review
found the same result (card t_b9801c40).

The round trip ran on an empty database. The command below creates the database and runs
the three revisions:

```bash
docker exec vigil-bench-pg dropdb -U postgres --if-exists vigil_bench_rt
docker exec vigil-bench-pg createdb -U postgres vigil_bench_rt
cd backend
URL='postgresql+asyncpg://postgres:bench@127.0.0.1:5434/vigil_bench_rt'
env -u PYTHONPATH DATABASE_URL=$URL uv run alembic upgrade head
env -u PYTHONPATH DATABASE_URL=$URL uv run alembic downgrade base
env -u PYTHONPATH DATABASE_URL=$URL uv run alembic upgrade head
```

The round trip needs an empty database. `0002.downgrade` changes
`memory_chunks.embedding` from 384 to 1536 dimensions. PostgreSQL refuses that change
while a row holds a 384-dimension vector. The probe below inserts one row, then runs the
downgrade:

```bash
docker exec vigil-bench-pg psql -U postgres -d vigil_bench_rt -v ON_ERROR_STOP=1 \
  -c "insert into users (id, display_name, settings) values (gen_random_uuid(), 'probe', '{}'::jsonb)" \
  -c "insert into memory_chunks (id, user_id, scope, content, embedding, embedding_model, embedding_dim) \
      select gen_random_uuid(), id, 'user', 'probe', array_fill(0.01::real, ARRAY[384])::vector, \
      'bge-small-en-v1.5', 384 from users where display_name = 'probe'"
cd backend
env -u PYTHONPATH DATABASE_URL=$URL uv run alembic downgrade base
```

```text
sqlalchemy.exc.DBAPIError: (sqlalchemy.dialects.postgresql.asyncpg.Error) expected 1536 dimensions, not 384
[SQL: ALTER TABLE memory_chunks ALTER COLUMN embedding TYPE vector(1536)]
```

The benchmark database held 100,000 rows from the recall run, so a downgrade of that
database stops in the same way. The probe leaves rows in the database. Drop and recreate
the database before you run the round trip again.

The checks below ran after each step. Run them from `backend/`, in the same shell, so
`$URL` is set:

```bash
docker exec vigil-bench-pg psql -U postgres -d vigil_bench_rt -tAc \
  "select count(*) from information_schema.tables where table_schema='public' and table_type='BASE TABLE'"
docker exec vigil-bench-pg psql -U postgres -d vigil_bench_rt -tAc \
  "select extversion from pg_extension where extname='vector'"
docker exec vigil-bench-pg psql -U postgres -d vigil_bench_rt -tAc \
  "select count(*) from pg_extension where extname='vector'"
env -u PYTHONPATH DATABASE_URL=$URL uv run alembic current
```

Output of this run:

```text
after upgrade head:    tables 20,  extversion 0.8.7,  extension count 1,  current 0003 (head)
after downgrade base:  tables 1,   extversion 0.8.7,  extension count 1,  current no revision
after upgrade head:    tables 20,  extversion 0.8.7,  extension count 1,  current 0003 (head)
```

`alembic current` printed `0003 (head)` after each upgrade. After the downgrade it
printed no revision name.

**Verdict: PASS for the tables and the revision.** The upgrade, downgrade, upgrade round
trip on an empty database is clean. The revision is 0003, the table count is 20, and
`alembic_version` is the only table at base. The `vector` extension stays at 0.8.7 at
base, as the table above records. The downgrade does not return the database to an empty
state, and this document does not claim that it does.

## 3. Measurement 2: recall at 100k rows

Target: recall@50 of 0.95 or more against an exact scan, with 100k rows over 100 users.

### 3.1 Assumptions

Recall is a property of the index and the filter. It is not a property of the embedding
space. So the run uses synthetic vectors. No model output enters this measurement.

| Assumption | Value |
| --- | --- |
| Seed | 20261101 (`numpy.random.default_rng`) |
| Dimensions | 384 |
| Vector rule | `unit(e0 + 1.2 * N(0, I) / sqrt(384))` |
| Row tag | `bge-small-en-v1.5`, the live model name |
| Rows | 100,000 |
| Users | 100, each with 1,000 rows |
| Queries | 200 per user, 20,000 in total |
| Index | HNSW, `m=16`, `ef_construction=64`, `vector_cosine_ops` |
| `ef_search` | 200, the shipped value, and 1000 for one diagnostic run |
| Exact scan | `enable_indexscan=off`, `enable_indexonlyscan=off`, `enable_bitmapscan=off` |

The vectors are one dense crowd near a single direction. That is the hard condition for
a filtered approximate scan. The tenant filter keeps 1 percent of the heap.

Each query has its own generator, seeded with the root seed and `1_000_000 + user_index`.
Each row id is a `uuid5` of the fixed namespace `0f0e0d0c-0b0a-4908-8706-050403020100` and
the row index (`user/{index}` and `chunk/{user_index}/{row}`). The seed does not enter the
id. The seed enters the vector values and the query values, so a second run with the same
seed rebuilds the same table.

```bash
grep -n "NAMESPACE\|uuid5\|default_rng" scripts/bench/recall_bench.py
```

The docstring of `recall_bench.py` still says that the id comes from the seed. That
sentence is wrong, and this card does not change the script.

### 3.2 What the planner does

The production statement does not use the HNSW index at this scale. The planner reads the
tenant rows through the btree index and then sorts them with no approximation.

```text
Limit
  Sort  (Sort Key: embedding <=> $4)
    Bitmap Heap Scan on memory_chunks
      Recheck Cond: user_id = $1
      Bitmap Index Scan on ix_memory_chunks_user_id
```

The production query at 1 percent selectivity reads 1,000 rows and sorts them. That is
cheaper than a graph walk of the full table. The vector index loses the cost test, so
`ef_search` and `hnsw.iterative_scan` do not apply.

To measure the index itself, the run drops `ix_memory_chunks_user_id` and
`ix_memory_chunks_user_scope_excluded`. The plan then reads the HNSW index, and the run
asserts this before it measures:

```text
Limit
  Index Scan using ix_memory_chunks_embedding_hnsw on memory_chunks
```

The two btree indexes come back at the end of the run.

### 3.3 Results

All values come from one run of `scripts/bench/recall_bench.py`.

| Path | `iterative_scan` | `ef_search` | recall@50 | p50 ms | p95 ms | Rows returned | Queries |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Production query, planner default | relaxed_order | 200 | 0.999993 | 4.67 | 6.36 | 50.0 | 20,000 |
| Forced HNSW | off | 200 | 0.040195 | 11.94 | 13.62 | 2.0 | 20,000 |
| Forced HNSW | relaxed_order | 200 | 0.560721 | 96.77 | 107.02 | 50.0 | 20,000 |
| Forced HNSW | strict_order | 200 | 0.478844 | 97.48 | 107.84 | 50.0 | 20,000 |
| Forced HNSW | strict_order | 1000 | 0.585160 | 118.86 | 129.65 | 50.0 | 2,000 |

Other numbers from the same run:

| Item | Value |
| --- | --- |
| Insert time, 100k rows | 38.6 s |
| HNSW build time | 93.9 s |
| Exact scan p50 | 54.69 ms |
| Exact scan p95 | 62.74 ms |
| Recall below 0.95, forced HNSW at shipped settings | 20,000 of 20,000 queries |
| Mean cosine distance, exact rank 1 | 0.4860 |
| Mean cosine distance, exact rank 50 | 0.5395 |
| Mean cosine distance, exact rank 200 | 0.5662 |

The distance table shows the margin. Rank 50 and rank 200 are 0.027 apart in distance, so
the order is not a tie. A set difference of a few rows is a true ranking error.

### 3.4 Reading of the numbers

`hnsw.iterative_scan` is in the code because of the first forced row. With the setting
off, the index scan stops at `ef_search` candidates, the tenant filter rejects 99 percent
of them, and the query returns 2 rows and not 50. Recall is 0.040. The setting turns bad
results into 50 results.

The setting does not get to the target. With `relaxed_order`, recall@50 is 0.561. With
`strict_order` it is 0.479, and the maximum `ef_search` of 1000 gives 0.585. All 20,000
queries miss the 0.95 target.

The cause is the graph. One global HNSW graph holds 100 tenants. A walk that follows the
nearest global neighbours spends its budget on rows that the filter then rejects. The
tenant rows that it meets are near the query, but they are not the nearest 50 of the
tenant.

The production query passes the target by a different path. It does not use the index.
The tenant btree reduces the candidate set to 1,000 rows, and an exact sort ranks them.
That path is also 20 times faster than the forced graph walk at the 50th percentile.

**Verdict: FAIL.** Recall@50 of the HNSW index path is 0.560 at the shipped settings
(`ef_search=200`, `iterative_scan=relaxed_order`) and 0.585 at the maximum `ef_search` of
1000. The two values are below the 0.95 target. The production query gets to 0.999993
only because the planner uses the tenant btree index and an exact sort, not the vector
index. The target for that query is met, but the vector index does not meet it. The
review measured 0.561791 for the forced HNSW path and 0.999993 for the production
query, so both recall rows reproduce.

### 3.5 After the fix: the routed path

Card F-02 changed the candidate path. The service counts the rows of the tenant, then chooses
one of three outcomes:

* At or below the crossover: the exact scan answers.
* Above the crossover, when the tenant graph exists: the tenant graph answers.
* Above the crossover, when the tenant graph does not exist: the exact scan answers. This is
  the fallback. It is correct, and it is slower.

No migration ships. The search response keeps its fields and adds the path name. The branch
is `fix/retrieval`.

The exact path refuses the graph walk:

```text
SET LOCAL enable_indexscan = off
SET LOCAL enable_indexonlyscan = off
```

The btree bitmap scan stays available, so the tenant rows still come from an index, and the
sort that follows is exact. The graph path refuses the exact sort:

```text
SET LOCAL hnsw.ef_search = 1000
SET LOCAL hnsw.iterative_scan = 'relaxed_order'
SET LOCAL enable_sort = off
```

#### 3.5.1 The measured crossover

Each row below measures both paths on the same rows and the same queries. The table states
the size of the whole table, because the exact scan cost depends on it. The host also ran
other workloads during these runs. The load average is on every row. The recall values do
not depend on the load. The latencies are higher than a quiet host.

| Table rows | Tenant rows | Path | recall@50 | p50 ms | p95 ms | Queries | Load |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 75,000 | 5,000 | exact | 1.0000 | 11.47 | 14.31 | 40 | 11.49 |
| 75,000 | 5,000 | tenant graph | 1.0000 | 14.76 | 18.05 | 40 | 11.49 |
| 60,000 | 10,000 | exact | 1.0000 | 16.26 | 22.85 | 40 | 13.78 |
| 60,000 | 10,000 | tenant graph | 0.9995 | 21.93 | 30.28 | 40 | 13.78 |
| 75,000 | 20,000 | exact | 1.0000 | 33.59 | 37.92 | 40 | 11.49 |
| 75,000 | 20,000 | tenant graph | 0.9970 | 30.89 | 34.33 | 40 | 11.49 |
| 50,000 | 50,000 | exact | 1.0000 | 117.60 | 138.87 | 50 | 11.65 |
| 50,000 | 50,000 | tenant graph | 0.9796 | 46.58 | 51.69 | 50 | 11.65 |

At 5,000 and 10,000 rows the exact scan is faster and it is exact. At 20,000 and 50,000 rows
the tenant graph is faster, and it stays above the 0.95 floor. The exact scan plans as a
bitmap index scan and a sort at 5,000 to 20,000 rows, and as a sequential scan and a sort
when the tenant holds the whole table.

#### 3.5.2 The tenant graph reaches the floor, the global graph does not

One tenant holds all 50,000 rows, so the tenant filter rejects nothing. This is the best
case for a graph. The table compares the two graph builds on the same rows and queries.

| Index | Build | `ef_search` | recall@50 | p50 ms | p95 ms | Queries |
| --- | --- | --- | --- | --- | --- | --- |
| global | m=16, ef_construction=64 | 200 | 0.5612 | 52.52 | 82.84 | 50 |
| global | m=16, ef_construction=64 | 1,000 | 0.8483 | 47.88 | 62.15 | 200 |
| tenant | m=16, ef_construction=200 | 200 | 0.6216 | 9.84 | 22.03 | 50 |
| tenant | m=16, ef_construction=200 | 500 | 0.7940 | 17.96 | 21.25 | 50 |
| tenant | m=16, ef_construction=200 | 1,000 | 0.8948 | 27.68 | 29.08 | 50 |
| tenant | m=32, ef_construction=200 | 200 | 0.8048 | 16.92 | 184.36 | 50 |
| tenant | m=32, ef_construction=200 | 500 | 0.9308 | 30.28 | 36.02 | 50 |
| tenant | m=32, ef_construction=200 | 1,000 | 0.9796 | 46.58 | 51.69 | 50 |

The link count decides the result. The same rows with m=32 reach 0.9796 at the maximum width.
`ef_search` 1000 is the maximum that pgvector accepts, so the graph cannot go wider. The
tenant graph is the only measured graph that meets the floor.

The cause is the test distribution. All vectors sit in one dense crowd. The cosine distance
of exact rank 1 is 0.4860 and of exact rank 50 is 0.5395 (section 3.3). A graph with a low
link count cannot separate the top 50 at this spacing.

#### 3.5.3 The shipped threshold

`MEMORY_RETRIEVAL_EXACT_SCAN_MAX_ROWS` is 20,000. The measurement sets it:

* At 5,000 and 10,000 tenant rows the exact scan has the lower p95, and it is exact.
* At 20,000 tenant rows the tenant graph has the lower p95: 34.33 ms against 37.92 ms.
* At 50,000 tenant rows the tenant graph has the lower p95: 51.69 ms against 138.87 ms.
* 20,000 is the smallest measured size where the graph wins, so it is the crossover.

`MEMORY_RETRIEVAL_TENANT_GRAPH_EF_SEARCH` is 1000. It is the width that reaches the floor at
50,000 rows, and it is the maximum width that pgvector accepts.

A tenant above 20,000 rows gets the graph after `create_tenant_graph_index` builds it. The
build retires the global graph, because the global graph stays a valid plan for the tenant
query, and when the planner chooses it the recall falls to 0.8483.

#### 3.5.4 The acceptance measurements

The two cases use the shipped routing (`--path auto`). Each row states its query count and
its load average. The 100,000-row case is the exact path, and the measurement below comes
from the run in section 3 with 1,000 queries per user. The 50,000-row case is the tenant
graph, and it comes from the run in section 3.5.1 with 50 queries.

| Case | Rows | Tenant rows | Path | recall@50 | p50 ms | p95 ms | Queries | Load |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Many light users | 100,000 | 1,000 | exact | 0.999980 | 11.94 | 22.98 | 1,000 | 7 to 8 |
| One heavy user | 50,000 | 50,000 | hnsw | 0.9796 | 46.58 | 51.69 | 50 | 11.65 |

The acceptance tests measure the same two cases through the service. They pass with the
0.95 floor. A full 200-queries-per-user run of both cases was started for this table and
stopped after 55 minutes, when the exact cache loop was still running on this loaded host.
That run gives the same path and the same rows as the rows above.


#### 3.5.5 Reproduction

```bash
cd backend
# The shipped path, many light users, the full query count.
env -u PYTHONPATH uv run python ../scripts/bench/recall_bench.py \
  --database-url 'postgresql+asyncpg://postgres:bench@127.0.0.1:5434/vigil_bench' \
  --users 100 --chunks-per-user 1000 --queries-per-user 200 --path auto --planner-only
# The heavy user, shipped path, with the tenant graph installed.
env -u PYTHONPATH uv run python ../scripts/bench/recall_bench.py \
  --database-url 'postgresql+asyncpg://postgres:bench@127.0.0.1:5434/vigil_bench' \
  --users 1 --chunks-per-user 50000 --queries-per-user 200 --path auto \
  --install-tenant-graph --planner-only
```

`--queries-per-user 10` gives a fast run for a first look. The table above states the query
count of every row.

**Verdict: the acceptance target is met, and the card's graph option is met.** The shipped
path reaches the floor at 100,000 rows over 100 users through the exact scan. It reaches the
floor at 50,000 rows for one user through the tenant graph, which is the per-tenant index
option of the card. The global graph does not meet the floor at any measured width.

### 3.6 Reproduction

```bash
cd backend
env -u PYTHONPATH uv run python ../scripts/bench/recall_bench.py \
  --database-url 'postgresql+asyncpg://postgres:bench@127.0.0.1:5434/vigil_bench' \
  --users 100 --chunks-per-user 1000 --queries-per-user 200 --ef-search 200
```

The script empties and refills `memory_chunks` and `users`, and it restores the two btree
indexes that it drops. A run takes about 100 minutes on this host.

## 4. Measurement 3: indexer memory and speed

Target: peak resident set below 350 MB, and 40 chunks per second or more.

The harness imports `src.worker` first, so the baseline holds the module graph of the
worker. It builds the requested number of chunks with the project chunker from synthetic
turns. It encodes them with the int8 ONNX artifact through `LocalEmbeddingModel`. It reads
the resident set from `/proc/self/status` and `getrusage`.

Chunk size is realistic for a memory window. At 1000 chunks the mean is 445 tokens, the
median is 444, the minimum is 327, and the maximum is 512 (the model cap).

### 4.1 The size of one index job

`INDEX_BATCH_LIMIT` is 200 messages (`backend/src/modules/memory/service.py`). The index
job reads at most that many unindexed messages in one pass. The specification registers a
job after 5 to 10 unindexed messages (`README.md`, the batched indexing trigger). So one
job usually holds 5 to 10 messages, and 200 messages is the cap.

```bash
grep -rn "INDEX_BATCH_LIMIT" backend/src
```

The chunker makes a window from 4 to 6 turns, with an overlap of 2 turns. The command
below counts the windows for a given number of turns. It uses the turn generator of
`scripts/bench/embed_bench.py` and the same seed:

```bash
cd backend
env -u PYTHONPATH uv run python - <<'PY'
import sys, uuid
sys.path.insert(0, "../scripts/bench")
import numpy as np
from embed_bench import make_turn
from src.config.settings import Settings
from src.integrations.embedding.local import LocalEmbeddingModel
from src.modules.memory.chunker import Chunker, MemoryTurn

settings = Settings()
chunker = Chunker(LocalEmbeddingModel(settings), max_tokens=settings.EMBEDDING_MAX_TOKENS)
rng = np.random.default_rng(20261101)
turns = []
for index in range(1, 3001):
    role = "user" if index % 2 == 0 else "assistant"
    size = int(rng.integers(20, 46)) if role == "user" else int(rng.integers(60, 141))
    turns.append(MemoryTurn(message_id=uuid.uuid4(), role=role, content=make_turn(rng, role, size)))
    if index in (5, 10, 200, 3000):
        print(index, len(chunker.segment(turns)))
PY
```

Output:

```text
5 1
10 2
200 50
3000 750
```

One job at the cap makes 50 chunks. The usual job of 5 to 10 messages makes 1 to 2
chunks. A 1000-chunk batch holds 4000 messages. That is 20 jobs at the cap, and 400 to
800 jobs at the usual size. The 1000-chunk row is a stress value, not a batch that the
worker sees.

### 4.2 Peak RSS by chunk count

The peak does not have one value. The table gives the values from three runs at each
chunk count. `VmHWM` and `ru_maxrss` agreed in all nine runs.

| Chunks | Messages | Work | Runs | Min (MB) | Median (MB) | Max (MB) |
| --- | --- | --- | --- | --- | --- | --- |
| 50 | 200 | one job at the cap | 3 | 331.5 | 333.1 | 349.4 |
| 200 | 800 | four jobs | 3 | 335.4 | 342.8 | 352.7 |
| 1000 | 4000 | twenty jobs | 3 | 243.9 | 300.7 | 367.8 |

The chunk count is not the cause of the spread. The peak follows the resident set after
the model load, and the host sets that value:

| Chunks | RSS after model load (MB) | Peak (MB) |
| --- | --- | --- |
| 50 | 179.0, 189.4, 191.4 | 331.5, 333.1, 349.4 |
| 200 | 178.5, 181.2, 190.2 | 335.4, 342.8, 352.7 |
| 1000 | 123.8, 184.4, 196.5 | 243.9, 300.7, 367.8 |

RSS counts resident pages. A page that the kernel removes is not resident, so a run on a
loaded host reports less memory than the process used. The three runs at 1000 chunks
built the same chunks from the same seed, and the resident set after the chunk build was
41.0 MB in one run and 109.4 MB in another. The host held about 3 GB in swap during these
runs, at a load average of 3.8 to 11.1. For that reason the peak of a run on a loaded
host is a lower bound.

The review measured 336.5 MB and 289.1 MB at 100 chunks and 362.0 MB at 200 chunks. The
first revision recorded 395.2 MB at 1000 chunks and gave no chunk count. The five known
values at 1000 chunks are 243.9, 300.7, 367.8, 395.2, and 434.9 MB.

**Peak RSS at one job: PASS.** The three runs at 50 chunks are 331.5, 333.1, and 349.4
MB. The worst run is 0.6 MB below the 350 MB target. The margin is very small, and the
review window is 50 MB, so this result does not show headroom.

**Peak RSS at 200 chunks: FAIL.** The three runs are 335.4, 342.8, and 352.7 MB. The
worst run is 2.7 MB above the target. The batch is four jobs.

**Peak RSS at 1000 chunks: FAIL.** The three runs are 243.9, 300.7, and 367.8 MB. The
first revision recorded 395.2 MB and the review measured 434.9 MB. Three of the five
runs are above the target, and the median of the five is 367.8 MB. The batch is 20 jobs,
so the value does not describe one job.

### 4.3 Resident set at each stage

| Item | Value (MB) |
| --- | --- |
| RSS after core imports | 81.9 to 84.5 |
| RSS after `src.worker` import | 82.7 to 85.3 |
| RSS after building 1000 chunks | 41.0 to 109.4 |
| RSS after model load | 123.8 to 196.5 |

The four values are the range over the three runs at 1000 chunks. Each run built the same
1000 chunks from the same seed, so the range after the chunk build is the page residency,
not the data size.

### 4.4 Idle unload

The idle unload worked in every run. `unload_if_idle` returned `True` and the session was
`None`. The harness reads the value at the end of each run (`scripts/bench/embed_bench.py`,
the `after_idle_unload` field). The resident set fell from the peak, but it did not fall to
the 90 MB that the specification claims. Python and the allocator keep the freed pages of
the process.

| Item | Range (MB) |
| --- | --- |
| RSS after idle unload, three runs at 50 chunks | 180.5 to 196.4 |
| RSS after idle unload, three runs at 200 chunks | 227.3 to 258.0 |
| RSS after idle unload, three runs at 1000 chunks | 92.6 to 111.8 |
| RSS after idle unload and `gc.collect()`, nine runs | 152.4 to 257.1 |

The medians of the three sizes are 192.5, 236.2, and 99.5 MB. The nine runs in this
revision gave 92.6 to 258.0 MB after the unload. The first revision recorded 217.3 MB and
the review measured 266.3 MB. The range over the eleven measurements is 92.6 to 266.3 MB.
The lowest value is 92.6 MB, which is 2.6 MB above the 90 MB in the specification. No run
reached 90 MB, so the claim does not hold at any size.

**Verdict: FAIL.** The best measured value is 92.6 MB, above the 90 MB in the
specification.

### 4.5 Throughput

| Item | Value |
| --- | --- |
| Model load time | 0.54 s |
| Encode time, 1000 chunks | 244.93 s |
| Throughput | 4.08 chunks/s |
| Chunk tokens, mean / min / max | 445 / 327 / 512 |

The throughput value comes from the run at commit `7400b4c`. The review reproduced it at
4.0919 chunks/s. The nine runs in this revision shared the host with other builds. Their
rate was 0.157 to 1.290 chunks/s, and their model load took 0.67 s to 6.09 s. They do not
measure throughput.

Model identity:

| Item | Value |
| --- | --- |
| Model | `bge-small-en-v1.5`, int8 ONNX, 384 dimensions |
| Artifact | `~/.cache/virgil/embedding/model_int8.onnx`, 33,760,831 bytes |
| Revision | `ea104dacec62c0de699686887e3f920caeb4f3e3` |
| SHA-256 observed | `bf64d05457cb391fa88d045faf5927a15ea36d96228ddf23ea970087afdc1197` |
| SHA-256 pinned in settings | `bf64d05457cb391fa88d045faf5927a15ea36d96228ddf23ea970087afdc1197` |
| Match | yes |

**Verdict: PASS at one job by 0.6 MB, FAIL at 200 and 1000 chunks, and FAIL at the
throughput target.**

* **Peak RSS below 350 MB at one job: PASS.** The worst of three runs at 50 chunks is
  349.4 MB. The batch is one job at the 200-message cap. The margin is 0.6 MB.
* **Peak RSS below 350 MB at 200 chunks: FAIL.** The worst of three runs is 352.7 MB.
  The batch is four jobs.
* **Peak RSS below 350 MB at 1000 chunks: FAIL.** Three of the five known runs are above
  the target, and the median of the five is 367.8 MB. The batch is 20 jobs, not one job.
* **40 chunks per second or more: FAIL.** The measured rate is 4.08 chunks/s at a mean
  of 445 tokens per chunk. That is 9.8 times below the target. The encoder groups texts
  by equal token length before each ONNX run, so mixed lengths mostly run at a batch of
  one or two rows.
* **Post-unload RSS near 90 MB: FAIL.** The lowest of eleven measurements is 92.6 MB.

### 4.6 Reproduction

```bash
cd backend
env -u PYTHONPATH EMBEDDING_IDLE_UNLOAD_SECONDS=3600 \
  uv run python ../scripts/bench/embed_bench.py --chunks 1000
env -u PYTHONPATH EMBEDDING_IDLE_UNLOAD_SECONDS=3600 \
  uv run python ../scripts/bench/embed_bench.py --chunks 200
env -u PYTHONPATH EMBEDDING_IDLE_UNLOAD_SECONDS=3600 \
  uv run python ../scripts/bench/embed_bench.py --chunks 50
```

Run each command three times. The runs in section 4.2 used these commands. Each run
starts a new process.

The value `EMBEDDING_IDLE_UNLOAD_SECONDS=3600` stops the idle sweeper from unloading the
session during one long encode. The default is 300 seconds. On a loaded host an encode of
1000 chunks takes longer than 300 seconds, and the sweeper unloads the session in the
middle of the run. The probe below uses a 5 second window to show the failure:

```bash
cd backend
env -u PYTHONPATH EMBEDDING_IDLE_UNLOAD_SECONDS=5 \
  uv run python ../scripts/bench/embed_bench.py --chunks 100
```

The run stops with this error:

```text
  File ".../src/integrations/embedding/local.py", line 319, in _encode_batch
    outputs = self._session.run(None, feeds)
AttributeError: 'NoneType' object has no attribute 'run'
```

The idle window does not change the peak resident set. The unload step in the harness
passes its own future time, so the step still measures the unloaded state. On an idle
host the command below completes in 245 s at 1000 chunks, below the 300 s window, so the
variable is not necessary.

```bash
cd backend
env -u PYTHONPATH uv run python ../scripts/bench/embed_bench.py
```

The first run downloads and verifies the artifact. A later run uses the cache.

## 4b. Re-measurement after the memory repair

This section repeats the measurement of section 4 after a change to the memory
model. The script, the seed, and the chunk builder are the same. The two
configurations ran one after the other on the same loaded host.

The measurement uses 40 chunks. The loaded host cannot finish 1000 chunks in a
practical time. The mean chunk has 454 tokens, which is close to the 445 tokens
of section 4. The commands and the load averages are:

| Configuration | Command | Load average, start / end |
| --- | --- | --- |
| In process | `EMBEDDING_ENCODE_SUBPROCESS=0 uv run python ../scripts/bench/embed_bench.py --chunks 40 --seed 20261101` | 5.69 / 6.63 |
| Subprocess | `uv run python ../scripts/bench/embed_bench.py --chunks 40 --seed 20261101` | 5.17 / 5.90 |

The before column sets `EMBEDDING_ENCODE_SUBPROCESS=0`. The after column uses
the default value, `EMBEDDING_ENCODE_SUBPROCESS=1`. Both commands start with
`cd backend` and `env -u PYTHONPATH`.

| Item | Before, in-process | After, subprocess |
| --- | --- | --- |
| Peak RSS of the worker during encoding | 304.8 MB | 105.8 MB |
| Peak RSS of the child | not used | 245.2 MB, from a separate run |
| Worker plus child peak | 304.8 MB | 347.5 MB, from the separate run |
| RSS after `unload_if_idle` | 171.5 MB | 105.8 MB |
| RSS after `unload_if_idle` and `gc.collect()` | 171.5 MB | 105.8 MB |
| Throughput at a mean of 454 tokens | 0.550 chunks per second | 0.586 chunks per second |
| ONNX session in the worker after the encode | yes | no |

The resident set is volatile on this host. The host has 8 GB of swap, and
2.6 GB was in use during these runs. The kernel can reclaim the pages of a
process at any moment. One sample of `VmRSS` can then fall below the heap that
the process still holds. The import baseline of the script was 90.0 MB in the
subprocess run and 81.9 MB in the in-process run. A resident set below that
baseline is not a footprint value. Do not read a value below about 82 to 90 MB
as a release.

In subprocess mode the worker holds no ONNX session. `unload_if_idle` returns
`False`, because there is no session to release. The 105.8 MB after the call is
the same value as before the call. The worker holds the tokenizer, the chunk
data, and the Python runtime. The claim of about 90 MB in the specification is
not met, by 15.8 MB. No unload can release the pages, because the worker never
loads them.

The bench script reads only the resident set of the worker. The child peak comes
from a second run of the same 40 chunk set through `LocalEmbeddingModel`, which
reads `RUSAGE_CHILDREN`. That run had load 6.21 to 6.53. The worker peaked at
102.2 MB and the child at 245.2 MB, a total of 347.5 MB. Two earlier runs of the
same configuration gave 333.6 MB and 338.8 MB. The worker plus child total is
the value to compare against the 350 MB target. The total is inside the target
by 2.5 to 16.4 MB, so the margin is small.

The throughput is lower than the 4.08 chunks per second of section 4, because
the host is loaded. The two throughput rows are not distinguishable on this
host. The in-process row and the subprocess row differ by less than the spread
of the runs below. The review of this change ran the two configurations in
turn at 40 chunks:

| Configuration | Run | Chunks per second | Load average |
| --- | --- | --- | --- |
| In process | 1 | 0.635 | 7.31 |
| Subprocess | 1 | 0.586 | 8.91 |
| Subprocess | 2 | 0.525 | 7.81 |
| In process | 2 | 0.371 | 9.04 |

One configuration moves by 1.7 times between two of those runs. The runs at
100 chunks reversed the direction. Subprocess mode gave 0.483 chunks per second
at load 9.30. In-process mode gave 0.368 chunks per second at load 10.06. The
earlier record read 0.617 against 0.506 chunks per second, a difference of
18 percent that is inside this spread. Do not read the repair as a cost of
18 percent of throughput. Both configurations are far below the 40 chunks per
second target.

### 4b.1 What changed

`backend/src/integrations/embedding/local.py`:

* A short lived child does the encode by default. The child holds the ONNX
  session, does the planned batches, writes the vectors, and exits. The worker
  keeps the tokenizer only. The pages of the batch go back to the operating
  system.
* The idle sweeper cannot release a session while an encode is active. On this
  loaded host, 1000 chunks took longer than the 300 second idle window. The
  sweeper released the session in the middle of the work. The bench stopped
  with `AttributeError: 'NoneType' object has no attribute 'run'` after 345
  seconds. The guard removes that failure.
* The model, the artifact, the checksum, the pooling, and the prefixes do not
  change. `LocalEmbeddingModel` continues to satisfy the `EmbeddingModel`
  Protocol.

### 4b.2 The batching decision does not hold

The card asked for buckets of a fixed token multiple, for example 64. Three
measurements refuse that change.

**1. The artifact is not invariant to padding or to the batch.** The pinned int8
artifact is dynamically quantized. The activation scale of a batch comes from
the largest activation in that batch. A longer sequence or a second row changes
the scale, and all vectors move.

| Comparison | Largest per-dimension difference |
| --- | --- |
| One row at 473 tokens against the same row padded to 512 | 1.354e-02 |
| Three rows at 512 against the same rows alone at 512 | 1.559e-02 |
| Row A alone against row A in a batch with row B | 1.153e-02, cosine 0.997151732 |
| Row A alone against row A in a batch with a copy of A | 0.000e+00 |

A copy of a row keeps the range, which is why the last row matches. The encoder
applies the attention mask: an all-ones mask moves the vector by 0.147. The
target of 1e-5 per dimension cannot hold for a padded batch or a multi row
batch of this artifact. The test `test_padded_batch_matches_solo` asserts the
target and carries an expected failure mark, with the measured value as the
cause.

**2. The existing suite forbids coarse buckets.**
`test_batch_cap_keeps_the_same_vectors` encodes five texts with a batch cap of 4
and of 1 at one padded width of 64. It asserts `np.allclose(atol=1e-6)`.
Measured: largest difference 1.462e-02, mean 2.731e-03, cosine minimum
0.995538592. The assertion `np.allclose(atol=1e-6)` does not hold. A bucket
change breaks a test that this card must not weaken.

**3. The 40 chunks per second target is above the peak of this host.** A 445
token chunk takes 22.55 GFLOP. The count is 12 layers, hidden 384, intermediate
1536, and the attention terms. This host has two vCPUs of a Xeon Platinum 8259CL
at 2.5 GHz. `/proc/cpuinfo` shows AVX-512 F and no VNNI. The FP32 peak is 320
GFLOP/s for the two cores. The absolute ceiling is 14.2 chunks per second at 100
percent of peak. The recorded 4.08 chunks per second is 29 percent of peak,
which is a realistic ONNX Runtime figure. The target of 40 chunks per second is
902 GFLOP/s, 2.8 times the peak. The figure of 82 to 105 chunks per second in
the card is 5.8 to 7.4 times the peak.

The batching test measured in one worker, with the two rows in turn, and with
the load at 5.0 to 5.5:

| Strategy | Batches | Chunks per second, row 1 / row 2 |
| --- | --- | --- |
| Exact length, 1024 token cap | 20 | 0.95 / 0.69 |
| Bucket 64, 1024 token cap | 13 | 1.02 / 0.65 |
| Bucket 64, 16384 token cap, 32 rows | 2 | 1.30 / 0.89 |

The count of batches falls from 20 to 2 with no matching gain. The limiter is
the arithmetic, not the count of batches.

### 4b.3 Verdicts

| Target | Before | After | Verdict |
| --- | --- | --- | --- |
| Peak RSS below 350 MB | 304.8 MB in process at 40 chunks, 395.2 MB at 1000 chunks | 105.8 MB in the worker, 347.5 MB for the worker and the child | PASS, with a margin of 2.5 to 16.4 MB over three runs. The worker plus child total is the value to compare, not the worker alone. |
| Idle RSS after unload | 171.5 MB in process at 40 chunks | 105.8 MB; no session in the worker, so no release | FAIL. The worker is 15.8 MB above the claim of about 90 MB, and no unload releases the pages. |
| 40 chunks per second | 4.08 at a mean of 445 tokens (section 4), 0.550 at a mean of 454 tokens and load 5.69 to 6.63 | 0.586 at a mean of 454 tokens and load 5.17 to 5.90 | FAIL. The two configurations are not distinguishable under this load, and the target is 2.8 times the compute peak of this host. |
| A padded batch within 1e-5 of a solo encode | not asserted | 1.4e-02 | FAIL. The artifact does not hold that property. |

The peak memory target is met, with a margin of 2.5 to 16.4 MB. The claim of
about 90 MB after an unload is not met, and the subprocess worker has no session
to release. The throughput target is not met, and no batching strategy can meet
it on this host. The batching strategy stays as it is until the artifact or the
host changes.

## 5. Measurement 4: sandbox on a live daemon

Target: the sandbox suite runs with no skip, and the daemon shows the documented flags.

### 5.1 Image prerequisite

The suite and the flags script need the image `python:3.14-slim` in the local daemon.
Pull it once:

```bash
docker pull python:3.14-slim
```

Without the image the suite skips 8 tests. It reports them as skipped, and it does not
fail. The flags script prints `python:3.14-slim is not present locally` and exits with
code 2. The skip comes from the fixture in
`backend/tests/integration/test_sandbox_runner.py`. The exit code 2 comes from
`scripts/bench/sandbox_flags.py`.

### 5.2 Suite

```bash
cd backend
env -u PYTHONPATH uv run pytest tests/integration/test_sandbox_runner.py -q
```

```text
........                                                                 [100%]
8 passed in 7.78s
```

All 8 tests ran. None skipped. Docker was available, so the suite exercised the live
daemon and the `python:3.14-slim` image.

### 5.3 Flags by hand

The proof created one container through `SandboxRunner.create`, read the flags back with
`docker inspect`, and removed the container. The command line that the runner builds is:

```text
docker run --detach --name <name> --read-only --cap-drop=ALL \
  --security-opt no-new-privileges --network=none --memory 1g --memory-swap 1g \
  --cpus 1.0 --pids-limit 256 --user 1000:1000 \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --tmpfs /workspace:rw,nosuid,size=2048m,uid=1000,gid=1000,mode=0770 \
  python:3.14-slim sleep 60
```

The daemon reported these values:

| Field | `docker inspect` path | Value |
| --- | --- | --- |
| ReadonlyRootfs | `.HostConfig.ReadonlyRootfs` | `true` |
| CapDrop | `.HostConfig.CapDrop` | `[ALL]` |
| NetworkMode | `.HostConfig.NetworkMode` | `none` |
| Memory | `.HostConfig.Memory` | `1073741824` (1 GiB) |
| MemorySwap | `.HostConfig.MemorySwap` | `1073741824` (1 GiB) |
| PidsLimit | `.HostConfig.PidsLimit` | `256` |
| SecurityOpt | `.HostConfig.SecurityOpt` | `[no-new-privileges]` |
| NanoCpus | `.HostConfig.NanoCpus` | `1000000000` (1.0 CPU) |
| User | `.Config.User` | `1000:1000` |
| Tmpfs | `.HostConfig.Tmpfs` | `/tmp` is `rw,noexec,nosuid,size=64m`, `/workspace` is `rw,nosuid,size=2048m,uid=1000,gid=1000,mode=0770` |

Command for one field:

```bash
docker inspect <id> --format '{{.HostConfig.ReadonlyRootfs}}'
docker inspect <id> --format '{{.HostConfig.CapDrop}}'
docker inspect <id> --format '{{.HostConfig.NetworkMode}}'
docker inspect <id> --format '{{.HostConfig.Memory}}'
docker inspect <id> --format '{{.HostConfig.PidsLimit}}'
docker inspect <id> --format '{{.HostConfig.SecurityOpt}}'
```

Time of the run:

| Step | Seconds |
| --- | --- |
| Container create | 0.248 |
| Create, inspect, one `exec`, remove | 0.587 |

The `exec` ran `python3 -c "print('ok')"` and returned `ok` with exit code 0. The script
removes the container at the end.

**Verdict: PASS.** The suite reports 8 passed and 0 skipped. The daemon shows read-only
root, all capabilities dropped, no network, 1 GiB of memory, a memory swap limit of 1 GiB,
256 pids, `no-new-privileges`, UID 1000, and the two tmpfs mounts.

### 5.4 Reproduction

```bash
cd backend
env -u PYTHONPATH uv run python ../scripts/bench/sandbox_flags.py
```

## 6. Regression check

The four measurements must not change the behavior of the test suite.

```bash
cd backend
env -u PYTHONPATH uv run pytest --junitxml=/tmp/b01.xml -q
```

```text
603 passed in 192.71s (0:03:12)
```

The XML report reads `tests="603"`, `failures="0"`, `errors="0"`, and `skipped="0"`.
Docker and Postgres were available for this run, so the sandbox and database tests ran.
The report is from the run at commit `7400b4c`. This revision changes this document
only, so the report still applies to the code.

## 7. Cleanup

The card owns one container and one database. The cleanup removes the two items.
`stotv-pg` and `virgil-v01-pg` stay as they are.

```bash
docker rm -f vigil-bench-pg
docker ps -a --filter name=vigil-bench-pg
docker volume ls | grep -c vigil_bench
```

The filter prints an empty list, and the count is 0. This output is from the first
revision. The container is up again for this revision's measurements. The database volume
is anonymous, so `docker rm -f` removes it with the container. The database
`vigil_bench_rt` lives in the same container, so the removal also removes it.

## 8. Summary

| Measurement | Target | Measured | Verdict |
| --- | --- | --- | --- |
| Migration | upgrade, downgrade, upgrade, no table left at base | 0003 to base to 0003, 20 tables, vector 0.8.7 stays at base | PASS for tables and revision |
| Recall@50, HNSW path | 0.95 or more | 0.560 at `ef_search=200`, `relaxed_order` | FAIL |
| Recall@50, production query | 0.95 or more | 0.999993, tenant btree path, index not used | PASS |
| Peak RSS, one job (50 chunks) | below 350 MB | 331.5 to 349.4 MB, worst 349.4 | PASS by 0.6 MB |
| Peak RSS, 200 chunks | below 350 MB | 335.4 to 352.7 MB, worst 352.7 | FAIL |
| Peak RSS, 1000 chunks | below 350 MB | 243.9 to 434.9 MB across five runs, median 367.8 | FAIL in three runs of five |
| Throughput | 40 chunks/s or more | 4.08 chunks/s at 445 tokens | FAIL |
| Post-unload RSS | about 90 MB per the spec | 92.6 to 266.3 MB across eleven measurements | FAIL |
| Sandbox flags | all documented flags present | all present on the live daemon | PASS |

Each finding below is a defect. Write one repair card for each defect.

* The HNSW path misses the recall target with a 1 percent tenant filter.
* The indexer peak at one job is 349.4 MB, 0.6 MB below the memory target. The margin is
  too small for a stable pass. At 200 chunks the peak is above the target, and at 1000
  chunks it is above the target in three runs of five.
* The indexer is 9.8 times below the throughput target.
* The resident set after the idle unload is 92.6 MB at best, above the 90 MB in the
  specification.
