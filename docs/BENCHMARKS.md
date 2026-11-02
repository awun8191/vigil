# Benchmarks: real Postgres and real Docker

Each earlier gate ran on fakes. This document records four measurements on the live
stack. The measurements are the migration, the index recall at 100k rows, the indexer
memory and speed, and the sandbox flags on a live daemon.

The document reports the measured value. It does not report an estimate. A target that
fails is recorded as FAIL, with the number that failed it.

No source code changed for this card. The measurement hooks live in `scripts/bench/`.
The repository commit is `7400b4c` on branch `verify/phases-4-5`.

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

### 3.5 Reproduction

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

## 4b. Re-measurement after the memory repair

This section repeats the measurement of section 4 after a change to the memory
model. The script, the seed, and the chunk builder are the same. The two rows
of the table below came one after the other. The two rows saw the same loaded
host. The load average was 8.15 for the first row and 8.46 for the second.

The measurement uses 100 chunks. The loaded host cannot finish 1000 chunks in a
practical time. The mean chunk has 447 tokens, which is the size of section 4.

| Item | Before, in-process | After, subprocess |
| --- | --- | --- |
| Peak RSS of the worker during encoding | 320.1 MB | 107.7 MB |
| Peak RSS of the child | not used | 233.9 MB |
| RSS after idle unload | 185.7 MB | 22.5 MB |
| RSS after unload and `gc.collect()` | 185.7 MB | 74.3 MB |
| Throughput at a mean of 447 tokens | 0.617 chunks per second | 0.506 chunks per second |
| ONNX session in the worker after the encode | yes | no |

The before row sets `EMBEDDING_ENCODE_SUBPROCESS=0`. The after row uses the
default value, `EMBEDDING_ENCODE_SUBPROCESS=1`. The commands are:

```bash
cd backend
env -u PYTHONPATH EMBEDDING_ENCODE_SUBPROCESS=0 uv run python ../scripts/bench/embed_bench.py --chunks 100
env -u PYTHONPATH uv run python ../scripts/bench/embed_bench.py --chunks 100
```

The throughput is lower than the 4.08 chunks per second of section 4, because
the host is loaded. Other agents on the host did benchmarks, test suites, and
container work at the same time. The two rows are comparable to each other. The
two rows are not comparable to section 4.

The bench script reads only the resident set of the worker. The child peak comes
from a second test of the same 100 chunk set through `LocalEmbeddingModel`,
which reads `RUSAGE_CHILDREN`. The value after the idle unload is volatile. The
kernel reclaimed pages at that moment, and the read after `gc.collect()` was
74.3 MB.

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
| Peak RSS below 350 MB | 320.1 MB at 100 chunks, 395.2 MB at 1000 chunks | 107.7 MB in the worker | PASS for the worker, with little margin. The worker and the child together hold about 335 MB at one time. |
| Idle RSS after unload | 185.7 MB | 22.5 MB | PASS. The subprocess option was used. |
| 40 chunks per second at a mean of 445 tokens | 4.08 at low load, 0.617 at load 8.15 | 0.506 at load 8.46 | FAIL. The target is 2.8 times the compute peak of this host. |
| A padded batch within 1e-5 of a solo encode | not asserted | 1.4e-02 | FAIL. The artifact does not hold that property. |

The memory target is met. The throughput target is not met, and no batching
strategy can meet it on this host. The batching strategy stays as it is until
the artifact or the host changes.

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
