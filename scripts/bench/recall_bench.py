"""Recall@50 of the filtered HNSW path against an exact scan, at 100k rows.

The measurement is a property of the index and the tenant filter, not of the
embedding space, so the vectors are synthetic and drawn from a fixed seed. They
are not model output. Every vector is a unit vector of 384 dimensions.

Distribution. All chunks and queries lie near one shared direction with
isotropic noise. That is the hard case for a filtered approximate scan: the
global graph is one crowd, the tenant filter keeps 1 percent of the heap, so a
plain ef_search window of 200 candidates holds almost no rows of the asked user.

The shipped path is measured on the same rows. ``--path auto`` uses the measured
crossover from settings, ``--path exact`` forces the exact scan, and ``--path
hnsw`` forces the graph walk. ``--install-tenant-graph`` builds the graph of each
measured tenant and retires the global graph, which is the state that a tenant
above the crossover needs. The graph run asserts the plan before it measures.
The diagnostic runs drop the two btree tenant indexes, so the HNSW index is the
only cheap access path, and they measure every ``hnsw.iterative_scan`` mode.

Ids are derived from the seed with uuid5, so a second run with the same seed
rebuilds the same 100k rows.

Usage (from backend/):
    env -u PYTHONPATH uv run python ../scripts/bench/recall_bench.py \
        --database-url postgresql+asyncpg://postgres:bench@127.0.0.1:5434/vigil_bench \
        --exact-cache /tmp/recall-exact.jsonl

The script is a measurement hook. It changes no application code and it restores
every index it drops.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import asyncpg
import numpy as np

BACKEND_ROOT = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config.settings import Settings  # noqa: E402
from src.modules.memory.retrieval import (  # noqa: E402
    GLOBAL_GRAPH_INDEX,
    RetrievalPolicy,
    exact_scan_statements,
    graph_path_statements,
    plan_retrieval,
    tenant_graph_ddl,
)
from src.modules.memory.service import MemoryService  # noqa: E402

DIMENSIONS = 384
ACTIVE_MODEL = "bge-small-en-v1.5"
SEED = 20261101
NOISE = 1.2
INSERT_BATCH = 2000
NAMESPACE = uuid.UUID("0f0e0d0c-0b0a-4908-8706-050403020100")
TENANT_INDEXES = ("ix_memory_chunks_user_id", "ix_memory_chunks_user_scope_excluded")

INSERT_USER = "INSERT INTO users (id, display_name) VALUES ($1, $2)"
INSERT_CHUNK = (
    "INSERT INTO memory_chunks "
    "(id, user_id, scope, content, embedding, embedding_model, created_at) "
    "VALUES ($1, $2, 'global', $3, $4::vector, $5, $6)"
)
EXACT_SQL = (
    "SELECT id FROM memory_chunks "
    "WHERE user_id = $1 AND scope = 'global' AND excluded = false "
    "AND embedding_model = $2 "
    "ORDER BY embedding <=> $3::vector LIMIT $4"
)
EXPLAIN_SQL = "EXPLAIN (FORMAT JSON) " + EXACT_SQL


def log(message: str) -> None:
    """Write one progress line to stderr, so stdout stays valid JSON."""
    print(f"[recall] {message}", file=sys.stderr, flush=True)


def user_id_for(index: int) -> uuid.UUID:
    """Return the deterministic user id for one index."""
    return uuid.uuid5(NAMESPACE, f"user/{index}")


def chunk_id_for(user_index: int, row: int) -> uuid.UUID:
    """Return the deterministic chunk id for one row of one user."""
    return uuid.uuid5(NAMESPACE, f"chunk/{user_index}/{row}")


def unit(values: np.ndarray) -> np.ndarray:
    """Return the vector scaled to unit length."""
    return values / np.linalg.norm(values)


def vector_text(values: Sequence[float], precision: int = 6) -> str:
    """Format a vector for a pgvector cast."""
    return "[" + ",".join(f"{float(value):.{precision}f}" for value in list(values)) + "]"


def draw(rng: np.random.Generator) -> np.ndarray:
    """Draw one unit vector near the shared direction with isotropic noise."""
    base = np.zeros(DIMENSIONS, dtype=np.float64)
    base[0] = 1.0
    return unit(base + NOISE * rng.standard_normal(DIMENSIONS) / math.sqrt(DIMENSIONS))


def percentile(values: Sequence[float], fraction: float) -> float:
    """Return the linear interpolated percentile of a sample."""
    ordered = sorted(values)
    if not ordered:
        return float("nan")
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def dsn_plain(url: str) -> str:
    """Strip the SQLAlchemy driver suffix from a database URL."""
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def plan_summary(plan: list) -> list[str]:
    """Flatten a Postgres JSON plan into ``Node Type[Index Name]`` strings."""
    nodes: list[str] = []

    def walk(node: dict) -> None:
        label = node.get("Node Type", "?")
        if node.get("Index Name"):
            label = f"{label}[{node['Index Name']}]"
        extra = node.get("Sort Key")
        if extra:
            label = f"{label} sort={len(extra)}"
        nodes.append(label)
        for key in ("Plans",):
            for child in node.get(key, []) or []:
                walk(child)

    for node in plan:
        walk(node)
    return nodes


def uses_hnsw(nodes: Sequence[str]) -> bool:
    """Return True when a plan node reads an HNSW index, global or per tenant."""
    return any("hnsw" in node.lower() for node in nodes)


async def reset_tables(connection: asyncpg.Connection) -> None:
    """Empty the two tables the measurement uses, then reclaim the space.

    Every HNSW index on the table goes first, not only the global one. A tenant
    graph that an earlier run built stays valid for its tenant, and the planner
    reads it in a later run, so that run would not measure the path the
    arguments ask for.
    """
    graphs = await connection.fetch(
        "SELECT indexname FROM pg_indexes "
        "WHERE tablename = 'memory_chunks' AND indexdef ILIKE '%USING hnsw%'"
    )
    for row in graphs:
        # The name comes from the catalog, not from an argument.
        await connection.execute(f'DROP INDEX IF EXISTS "{row["indexname"]}"')
    await connection.execute("TRUNCATE memory_chunks, users CASCADE")
    await connection.execute("VACUUM (FULL, ANALYZE) memory_chunks")


async def insert_rows(
    connection: asyncpg.Connection, users: list[uuid.UUID], per_user: int
) -> float:
    """Insert the rows and return the elapsed seconds."""
    created_at = datetime.now(timezone.utc)
    started = time.perf_counter()
    await connection.executemany(
        INSERT_USER,
        [(user_id, f"recall-user-{index}") for index, user_id in enumerate(users)],
    )
    for user_index, user_id in enumerate(users):
        rng = np.random.default_rng((SEED, user_index))
        batch: list[tuple] = []
        for row in range(per_user):
            batch.append(
                (
                    chunk_id_for(user_index, row),
                    user_id,
                    f"recall chunk {user_index}-{row}",
                    vector_text(draw(rng), precision=4),
                    ACTIVE_MODEL,
                    created_at,
                )
            )
            if len(batch) == INSERT_BATCH:
                await connection.executemany(INSERT_CHUNK, batch)
                batch.clear()
        if batch:
            await connection.executemany(INSERT_CHUNK, batch)
        if (user_index + 1) % 20 == 0:
            log(f"inserted {user_index + 1}/{len(users)} users")
    return time.perf_counter() - started


async def build_index(connection: asyncpg.Connection) -> float:
    """Build the HNSW index exactly as the migration defines it."""
    started = time.perf_counter()
    await connection.execute("SET maintenance_work_mem = '512MB'")
    await connection.execute("SET max_parallel_maintenance_workers = 0")
    await connection.execute(
        "CREATE INDEX ix_memory_chunks_embedding_hnsw ON memory_chunks "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )
    await connection.execute("ANALYZE memory_chunks")
    return time.perf_counter() - started


async def install_tenant_graphs(
    connection: asyncpg.Connection, users: list[uuid.UUID]
) -> float:
    """Build one graph per measured tenant, then retire the global graph.

    This is the maintenance step that the retrieval path expects above the
    crossover. Use one tenant for the heavy case: a graph per light tenant costs
    time and gains nothing, because the exact scan answers that size.
    """
    started = time.perf_counter()
    await connection.execute("SET maintenance_work_mem = '512MB'")
    await connection.execute("SET max_parallel_maintenance_workers = 0")
    for user_id in users:
        await connection.execute(tenant_graph_ddl(user_id))
    await connection.execute(f"DROP INDEX IF EXISTS {GLOBAL_GRAPH_INDEX}")
    await connection.execute("ANALYZE memory_chunks")
    return time.perf_counter() - started


async def exact_results(
    probe: asyncpg.Connection,
    users: list[uuid.UUID],
    queries: list[list[np.ndarray]],
    top_n: int,
    cache_path: Path | None,
) -> tuple[dict[tuple[int, int], set[uuid.UUID]], dict]:
    """Read or compute the exact top n for every query with every index path off."""
    if cache_path is not None and cache_path.is_file():
        log(f"reading the exact cache from {cache_path}")
        cached = {}
        with cache_path.open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                cached[(row["user"], row["query"])] = {
                    uuid.UUID(value) for value in row["ids"]
                }
        if len(cached) == sum(len(item) for item in queries):
            return cached, {"source": "cache", "latency_p50_ms": None, "latency_p95_ms": None}

    exact_ids: dict[tuple[int, int], set[uuid.UUID]] = {}
    latencies: list[float] = []
    handle = cache_path.open("w", encoding="utf-8") if cache_path is not None else None
    try:
        for user_index, user_id in enumerate(users):
            for query_index, query in enumerate(queries[user_index]):
                started = time.perf_counter()
                async with probe.transaction():
                    await probe.execute("SET LOCAL enable_indexscan = off")
                    await probe.execute("SET LOCAL enable_indexonlyscan = off")
                    await probe.execute("SET LOCAL enable_bitmapscan = off")
                    rows = await probe.fetch(
                        EXACT_SQL, user_id, ACTIVE_MODEL, vector_text(query), top_n
                    )
                latencies.append((time.perf_counter() - started) * 1000.0)
                ids = {row["id"] for row in rows}
                if len(ids) != top_n:
                    raise SystemExit(
                        f"the exact scan returned {len(ids)} rows, not {top_n}"
                    )
                exact_ids[(user_index, query_index)] = ids
                if handle is not None:
                    handle.write(
                        json.dumps(
                            {
                                "user": user_index,
                                "query": query_index,
                                "ids": [str(value) for value in ids],
                            }
                        )
                        + "\n"
                    )
            if (user_index + 1) % 20 == 0:
                log(f"exact: user {user_index + 1}/{len(users)}")
    finally:
        if handle is not None:
            handle.close()
    return exact_ids, {
        "source": "measured",
        "latency_p50_ms": percentile(latencies, 0.50),
        "latency_p95_ms": percentile(latencies, 0.95),
    }


async def plan_for(
    probe: asyncpg.Connection,
    user_id: uuid.UUID,
    query: np.ndarray,
    top_n: int,
    presets: Sequence[str] = (),
) -> list[str]:
    """Explain the production statement for one user and one query.

    ``presets`` are the session settings of the measured path. They run in the
    same transaction as the explain, so the plan shows the path the query takes.
    """
    async with probe.transaction():
        for statement in presets:
            await probe.execute(statement)
        rows = await probe.fetch(
            EXPLAIN_SQL, user_id, ACTIVE_MODEL, vector_text(query), top_n
        )
    return plan_summary([json.loads(rows[0]["QUERY PLAN"])[0]["Plan"]])


async def neighbour_margin(
    probe: asyncpg.Connection,
    users: list[uuid.UUID],
    queries: list[list[np.ndarray]],
    sample_users: int = 10,
) -> dict:
    """Return the mean cosine distance at fixed ranks of the exact order.

    A flat curve means many rows sit at almost the same distance, so a set
    difference of a few rows is not a quality signal. A steep curve means recall
    measures a real ordering.
    """
    ranks = (0, 9, 49, 99, 199)
    totals = {rank: 0.0 for rank in ranks}
    counted = 0
    for user_index in range(min(sample_users, len(users))):
        async with probe.transaction():
            await probe.execute("SET LOCAL enable_indexscan = off")
            await probe.execute("SET LOCAL enable_indexonlyscan = off")
            await probe.execute("SET LOCAL enable_bitmapscan = off")
            rows = await probe.fetch(
                "SELECT embedding <=> $2::vector AS distance FROM memory_chunks "
                "WHERE user_id = $1 AND scope = 'global' AND excluded = false "
                "AND embedding_model = $3 ORDER BY distance LIMIT 200",
                users[user_index],
                vector_text(queries[user_index][0]),
                ACTIVE_MODEL,
            )
        if len(rows) < max(ranks) + 1:
            continue
        for rank in ranks:
            totals[rank] += float(rows[rank]["distance"])
        counted += 1
    if not counted:
        return {"sampled_queries": 0}
    return {
        "sampled_queries": counted,
        "mean_cosine_distance_by_rank": {
            str(rank + 1): totals[rank] / counted for rank in ranks
        },
    }


async def run_mode(
    sessionmaker: async_sessionmaker[AsyncSession],
    users: list[uuid.UUID],
    queries: list[list[np.ndarray]],
    policy: RetrievalPolicy,
    exact_ids: dict[tuple[int, int], set[uuid.UUID]],
    label: str,
    limit_per_user: int | None = None,
) -> dict:
    """Run every query through the production path and compare with the exact scan."""
    recalls: list[float] = []
    latencies: list[float] = []
    returned: list[int] = []
    paths: dict[str, int] = {}
    async with sessionmaker() as session:
        service = MemoryService(session=session, settings=Settings())
        for user_index, user_id in enumerate(users):
            window = queries[user_index][:limit_per_user] if limit_per_user else queries[user_index]
            for query_index, query in enumerate(window):
                payload = [float(value) for value in query]
                started = time.perf_counter()
                results = await service.search_embedding(
                    user_id, payload, scopes=["global"], policy=policy
                )
                latencies.append((time.perf_counter() - started) * 1000.0)
                path = service.last_retrieval_path or "unknown"
                paths[path] = paths.get(path, 0) + 1
                found = {result.id for result in results}
                expected = exact_ids[(user_index, query_index)]
                recalls.append(len(expected & found) / len(expected))
                returned.append(len(found))
            if (user_index + 1) % 25 == 0:
                log(f"{label}: user {user_index + 1}/{len(users)}")
        settings_now = {}
        for name in ("hnsw.ef_search", "hnsw.iterative_scan"):
            settings_now[name] = (await session.execute(text(f"SHOW {name}"))).scalar()
        await session.rollback()
    return {
        "label": label,
        "ef_search": settings_now["hnsw.ef_search"],
        "iterative_scan": settings_now["hnsw.iterative_scan"],
        "paths": paths,
        "queries": len(recalls),
        "recall_mean": sum(recalls) / len(recalls),
        "recall_min": min(recalls),
        "recall_p10": percentile(recalls, 0.10),
        "queries_below_095": sum(1 for value in recalls if value < 0.95),
        "mean_returned": sum(returned) / len(returned),
        "latency_p50_ms": percentile(latencies, 0.50),
        "latency_p95_ms": percentile(latencies, 0.95),
        "latency_max_ms": max(latencies),
    }


def policy_with(
    top_n: int, ef_search: int, mode: str, exact_scan_max_rows: int
) -> RetrievalPolicy:
    """Return the production policy with one scan setting overridden."""
    base = RetrievalPolicy.from_settings(Settings())
    return RetrievalPolicy(
        half_life_days=base.half_life_days,
        floor=base.floor,
        override_threshold=base.override_threshold,
        candidate_k=top_n,
        top_n=top_n,
        minimum_similarity=base.minimum_similarity,
        ef_search=ef_search,
        iterative_scan=mode,
        exact_scan_max_rows=exact_scan_max_rows,
    )


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--users", type=int, default=100)
    parser.add_argument("--chunks-per-user", type=int, default=1000)
    parser.add_argument("--queries-per-user", type=int, default=200)
    parser.add_argument("--ef-search", type=int, default=200)
    parser.add_argument("--top-n", type=int, default=50)
    parser.add_argument("--exact-cache", type=Path, default=None)
    parser.add_argument("--planner-only", action="store_true")
    parser.add_argument(
        "--path",
        choices=("auto", "exact", "hnsw"),
        default="auto",
        help=(
            "auto uses the measured crossover from settings, exact forces the "
            "exact scan, and hnsw forces the graph walk"
        ),
    )
    parser.add_argument(
        "--install-tenant-graph",
        action="store_true",
        help=(
            "build one graph per measured tenant and retire the global graph; "
            "use one tenant for the heavy case"
        ),
    )
    args = parser.parse_args()

    plain = dsn_plain(args.database_url)
    users = [user_id_for(index) for index in range(args.users)]
    queries: list[list[np.ndarray]] = []
    for user_index in range(args.users):
        rng = np.random.default_rng((SEED, 1_000_000 + user_index))
        queries.append([draw(rng) for _ in range(args.queries_per_user)])

    connection = await asyncpg.connect(plain)
    probe = await asyncpg.connect(plain)
    engine = create_async_engine(args.database_url)
    sessionmaker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        await reset_tables(connection)
        insert_seconds = await insert_rows(connection, users, args.chunks_per_user)
        total_rows = args.users * args.chunks_per_user
        log(f"inserted {total_rows} rows in {insert_seconds:.1f}s")
        index_seconds = 0.0
        tenant_graph_seconds = 0.0
        if args.install_tenant_graph:
            tenant_graph_seconds = await install_tenant_graphs(connection, users)
            log(f"built {len(users)} tenant graphs in {tenant_graph_seconds:.1f}s")
        else:
            index_seconds = await build_index(connection)
            log(f"built HNSW index in {index_seconds:.1f}s")

        exact_ids, exact_stats = await exact_results(
            probe, users, queries, args.top_n, args.exact_cache
        )
        margin = await neighbour_margin(probe, users, queries)

        threshold = {
            "auto": RetrievalPolicy.from_settings(Settings()).exact_scan_max_rows,
            "exact": 1_000_000_000,
            "hnsw": 0,
        }[args.path]
        shipped_policy = policy_with(
            args.top_n, args.ef_search, "relaxed_order", threshold
        )
        presets = (
            [str(item) for item in graph_path_statements(shipped_policy)]
            if args.path == "hnsw"
            else [str(item) for item in exact_scan_statements()]
            if args.path == "exact"
            else None
        )
        label = f"shipped {args.path}"
        chosen_path = args.path
        if presets is None:
            # The auto path must plan with the settings that the router uses for
            # this tenant. An empty preset list plans the bare query, and a
            # leftover graph index then answers it, so the plan line would not
            # describe the shipped path.
            async with sessionmaker() as plan_session:
                planned = await plan_retrieval(
                    plan_session,
                    users[0],
                    queries[0][0],
                    ["global"],
                    shipped_policy,
                    ACTIVE_MODEL,
                )
            chosen_path = planned.path
            presets = [str(item) for item in planned.statements]
        planner_plan = await plan_for(
            probe, users[0], queries[0][0], args.top_n, presets
        )
        log(f"{label} path ({chosen_path}): {planner_plan}")
        if chosen_path == "hnsw" and not uses_hnsw(planner_plan):
            raise SystemExit(
                "the graph path does not read the HNSW index: " f"{planner_plan}"
            )
        if chosen_path == "exact" and uses_hnsw(planner_plan):
            raise SystemExit(
                "the exact path reads the HNSW index: " f"{planner_plan}"
            )
        planner = await run_mode(
            sessionmaker,
            users,
            queries,
            shipped_policy,
            exact_ids,
            label,
        )
        planner["plan"] = planner_plan
        planner["used_hnsw"] = uses_hnsw(planner_plan)
        planner["path"] = args.path
        planner["exact_scan_max_rows"] = threshold
        modes = [planner]

        forced_plan: list[str] = []
        forced_results = []
        if not args.planner_only and not args.install_tenant_graph:
            # Remove the btree tenant paths, so the HNSW index is the only
            # cheap access path. The indexes come back in the finally block.
            for name in TENANT_INDEXES:
                await connection.execute(f"DROP INDEX IF EXISTS {name}")
            try:
                forced_plan = await plan_for(probe, users[0], queries[0][0], args.top_n)
                log(f"forced path: {forced_plan}")
                if not uses_hnsw(forced_plan):
                    raise SystemExit(
                        "the forced run does not read the HNSW index: " f"{forced_plan}"
                    )
                for label, mode, ef_search, limit in (
                    ("forced-hnsw off", "off", args.ef_search, None),
                    ("forced-hnsw relaxed_order", "relaxed_order", args.ef_search, None),
                    ("forced-hnsw strict_order", "strict_order", args.ef_search, None),
                    (
                        "forced-hnsw strict_order ef_search=1000",
                        "strict_order",
                        1000,
                        20,
                    ),
                ):
                    result = await run_mode(
                        sessionmaker,
                        users,
                        queries,
                        policy_with(args.top_n, ef_search, mode, 0),
                        exact_ids,
                        label,
                        limit_per_user=limit,
                    )
                    result["plan"] = forced_plan
                    result["used_hnsw"] = True
                    forced_results.append(result)
            finally:
                await connection.execute(
                    "CREATE INDEX IF NOT EXISTS ix_memory_chunks_user_id "
                    "ON memory_chunks (user_id)"
                )
                await connection.execute(
                    "CREATE INDEX IF NOT EXISTS ix_memory_chunks_user_scope_excluded "
                    "ON memory_chunks (user_id, scope, excluded)"
                )
            modes.extend(forced_results)
    finally:
        await engine.dispose()
        await probe.close()
        await connection.close()

    report = {
        "seed": SEED,
        "dimensions": DIMENSIONS,
        "distribution": f"unit(e0 + {NOISE} * N(0, I) / sqrt({DIMENSIONS}))",
        "model_tag": ACTIVE_MODEL,
        "rows": total_rows,
        "users": args.users,
        "chunks_per_user": args.chunks_per_user,
        "queries_per_user": args.queries_per_user,
        "index": {"m": 16, "ef_construction": 64},
        "index_build_seconds": index_seconds,
        "tenant_graph": {
            "installed": bool(args.install_tenant_graph),
            "tenants": len(users) if args.install_tenant_graph else 0,
            "m": 32,
            "ef_construction": 200,
            "build_seconds": tenant_graph_seconds,
        },
        "insert_seconds": insert_seconds,
        "measured_path": args.path,
        "exact_scan_max_rows": threshold,
        "exact_scan": {
            **exact_stats,
            "top_n": args.top_n,
            "index_flags": [
                "enable_indexscan=off",
                "enable_indexonlyscan=off",
                "enable_bitmapscan=off",
            ],
        },
        "neighbour_margin": margin,
        "modes": modes,
        "host_python": sys.version.split()[0],
    }
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
