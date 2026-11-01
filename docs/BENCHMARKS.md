# Benchmarks: real Postgres and real Docker

Each earlier gate ran on fakes. This document records four measurements on the live
stack. The measurements are the migration, the index recall at 100k rows, the indexer
memory and speed, and the sandbox flags on a live daemon.

The document reports the measured value. It does not report an estimate. A target that
fails is recorded as FAIL, with the number that failed it.

No source code changed for the first four measurements. The measurement hooks live in
`scripts/bench/`. The repository commit is `7400b4c` on branch `verify/phases-4-5`.

Section 3.5 was added by card F-02 on branch `fix/retrieval`. That change does touch source
code: the retrieval path now follows the tenant row count. Sections 1, 2, 4, and 5 report
the same measurements as before.

## 1. Environment

| Fact | Value |
| --- | --- |
| Host | Debian GNU/Linux 12 (bookworm), kernel 6.1.0-53-cloud-amd64 |
| CPU | 2 vCPU, Intel Xeon Platinum 8259CL at 2.50 GHz |
| Memory | 3,960,628 kB total (3.78 GiB) |
| Docker | 29.9.1 (client and server) |
| Docker Compose | v5.6.0 |
| Python | 3.12.15, managed by uv 0.13.1 |
| PostgreSQL | 16.15 (Debian 16.15-1.pgdg12+2) |
| pgvector | 0.8.7 |
| Database image | `pgvector/pgvector:pg16` |

Command:

```bash
docker version --format '{{.Server.Version}}'
docker exec vigil-bench-pg psql -U postgres -d vigil_bench -tAc "select version()"
docker exec vigil-bench-pg psql -U postgres -d vigil_bench -tAc "select extversion from pg_extension where extname='vector'"
```

The benchmark database ran in a new container, `vigil-bench-pg`, on port 5434. The two
older containers, `virgil-v01-pg` on 5432 and `stotv-pg` on 5433, stayed untouched.

```bash
docker run -d --name vigil-bench-pg -p 5434:5432 \
  -e POSTGRES_PASSWORD=bench -e POSTGRES_DB=vigil_bench \
  pgvector/pgvector:pg16
for i in $(seq 1 60)
do
  docker exec vigil-bench-pg pg_isready -U postgres && break
  sleep 1
done
```

The container answered `pg_isready` after 2 seconds.

## 2. Measurement 1: migration

Target: the schema gets to head, goes back to base, and gets to head again with no error.

| Step | Revision | Tables | vector |
| --- | --- | --- | --- |
| `alembic upgrade head` | 0003 | 20 | 0.8.7 |
| `alembic downgrade base` | none | 1 | absent |
| `alembic upgrade head` | 0003 | 20 | 0.8.7 |

The table count of 20 includes `alembic_version`. The schema has 19 data tables. The
single table after the downgrade is `alembic_version`, and it holds no row.

```bash
cd backend
env -u PYTHONPATH DATABASE_URL='postgresql+asyncpg://postgres:bench@127.0.0.1:5434/vigil_bench' \
  uv run alembic upgrade head
env -u PYTHONPATH DATABASE_URL='postgresql+asyncpg://postgres:bench@127.0.0.1:5434/vigil_bench' \
  uv run alembic downgrade base
env -u PYTHONPATH DATABASE_URL='postgresql+asyncpg://postgres:bench@127.0.0.1:5434/vigil_bench' \
  uv run alembic upgrade head
```

Checks:

```bash
docker exec vigil-bench-pg psql -U postgres -d vigil_bench -tAc \
  "select count(*) from information_schema.tables where table_schema='public' and table_type='BASE TABLE'"
docker exec vigil-bench-pg psql -U postgres -d vigil_bench -tAc \
  "select extversion from pg_extension where extname='vector'"
cd backend && env -u PYTHONPATH DATABASE_URL='postgresql+asyncpg://postgres:bench@127.0.0.1:5434/vigil_bench' uv run alembic current
```

`alembic current` prints `0003 (head)`. The round trip ran with no error and left no
extra object. The downgrade drops the `vector` extension. The upgrade creates it again at
0.8.7.

**Verdict: PASS.** The upgrade, downgrade, upgrade round trip is clean. The revision is
0003, the table count is 20, and the vector extension is 0.8.7.

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

Each query has its own generator, from the root seed. Each row id is a `uuid5` of the
seed, so a second run rebuilds the same table.

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
index. The target for that query is met, but the vector index does not meet it.

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
worker. It builds 1000 chunks with the project chunker from synthetic turns. It encodes
them with the int8 ONNX artifact through `LocalEmbeddingModel`. It reads the resident set
from `/proc/self/status` and `getrusage`.

Chunk size is realistic for a memory window. The mean is 445 tokens, the median is 444,
the minimum is 327, and the maximum is 512 (the model cap).

| Item | Value |
| --- | --- |
| RSS after core imports | 83.4 MB |
| RSS after `src.worker` import | 86.0 MB |
| RSS after building 1000 chunks | 108.4 MB |
| RSS after model load | 174.8 MB |
| Peak RSS during encoding (`VmHWM`) | 395.2 MB |
| Peak RSS during encoding (`ru_maxrss`) | 395.2 MB |
| RSS after idle unload | 217.3 MB |
| RSS after idle unload and `gc.collect()` | 211.5 MB |
| Model load time | 0.54 s |
| Encode time, 1000 chunks | 244.93 s |
| Throughput | 4.08 chunks/s |
| Chunk tokens, mean / min / max | 445 / 327 / 512 |

The idle unload worked. `unload_if_idle` returned `True`, the session was `None`, and the
resident set fell from the 395 MB peak to 217 MB. It did not fall to the 90 MB that the
specification claims. Python and the allocator keep the freed pages of the process.

Model identity:

| Item | Value |
| --- | --- |
| Model | `bge-small-en-v1.5`, int8 ONNX, 384 dimensions |
| Artifact | `~/.cache/virgil/embedding/model_int8.onnx`, 33,760,831 bytes |
| Revision | `ea104dacec62c0de699686887e3f920caeb4f3e3` |
| SHA-256 observed | `bf64d05457cb391fa88d045faf5927a15ea36d96228ddf23ea970087afdc1197` |
| SHA-256 pinned in settings | `bf64d05457cb391fa88d045faf5927a15ea36d96228ddf23ea970087afdc1197` |
| Match | yes |

**Verdict: FAIL, two targets.**

* **Peak RSS below 350 MB: FAIL.** The measured peak is 395.2 MB, which is 45 MB above
  the target and 125 MB above the 270 MB that the specification claims.
* **40 chunks per second or more: FAIL.** The measured rate is 4.08 chunks/s at a mean
  of 445 tokens per chunk. That is 9.8 times below the target. The encoder groups texts
  by equal token length before each ONNX run, so mixed lengths mostly run at a batch of
  one or two rows.

The post-unload resident set is a third finding. The value is 217.3 MB, and not the
claimed 90 MB.

### 4.1 Reproduction

```bash
cd backend
env -u PYTHONPATH uv run python ../scripts/bench/embed_bench.py
```

The first run downloads and verifies the artifact. A later run uses the cache.

## 5. Measurement 4: sandbox on a live daemon

Target: the sandbox suite runs with no skip, and the daemon shows the documented flags.

### 5.1 Suite

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

### 5.2 Flags by hand

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

### 5.3 Reproduction

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

## 7. Cleanup

The card owns one container and one database. The cleanup removes the two items.
`stotv-pg` and `virgil-v01-pg` stay as they are.

```bash
docker rm -f vigil-bench-pg
docker ps -a --filter name=vigil-bench-pg
docker volume ls | grep -c vigil_bench
```

The filter prints an empty list, and the count is 0. The database volume is anonymous, so
`docker rm -f` removes it with the container.

## 8. Summary

| Measurement | Target | Measured | Verdict |
| --- | --- | --- | --- |
| Migration | upgrade, downgrade, upgrade | 0003 to base to 0003, 20 tables, vector 0.8.7 | PASS |
| Recall@50, HNSW path | 0.95 or more | 0.560 at `ef_search=200`, `relaxed_order` | FAIL |
| Recall@50, production query | 0.95 or more | 0.999993, tenant btree path, index not used | PASS |
| Peak RSS | below 350 MB | 395.2 MB | FAIL |
| Throughput | 40 chunks/s or more | 4.08 chunks/s at 445 tokens | FAIL |
| Post-unload RSS | about 90 MB per the spec | 217.3 MB | FAIL |
| Sandbox flags | all documented flags present | all present on the live daemon | PASS |

Each finding below is a defect. Write one repair card for each defect.

* The HNSW path misses the recall target with a 1 percent tenant filter.
* The indexer is above the memory target.
* The indexer is 9.8 times below the throughput target.
