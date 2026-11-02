"""Integration tests for the time decay retrieval path against Postgres.

Each test builds its own scratch database, so the suite never depends on the state
of the development database and never leaves rows behind. When Postgres is absent
the tests skip with a reason.
"""
from __future__ import annotations

import asyncio
import math
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Sequence

import asyncpg
import numpy as np
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config.settings import Settings, get_settings
from src.modules.memory.repository import MemoryChunkCreateDTO
from src.modules.memory.retrieval import (
    RetrievalPolicy,
    create_tenant_graph_index,
)
from src.modules.memory.service import MemoryService
from src.schemas.base import Base

DIMENSIONS = 384
ACTIVE_MODEL = "bge-small-en-v1.5"
OTHER_MODEL = "text-embedding-3-small"

RECALL_USERS = 100
RECALL_CHUNKS_PER_USER = 1000
RECALL_TOTAL = RECALL_USERS * RECALL_CHUNKS_PER_USER
RECALL_QUERIES = 5
RECALL_FLOOR = 0.95
HEAVY_USER_CHUNKS = 50000
HEAVY_USER_QUERIES = 20
BULK_INSERT_BATCH = 1000

INSERT_CHUNK = (
    "INSERT INTO memory_chunks "
    "(id, user_id, scope, content, embedding, embedding_model, created_at) "
    "VALUES ($1, $2, 'global', $3, $4::vector, $5, $6)"
)


@dataclass
class ScratchDatabase:
    """A database that belongs to one test and is dropped afterwards."""

    name: str
    dsn: str
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]


def _base_dsn() -> str:
    """Return the configured database URL in plain asyncpg form."""
    return get_settings().DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)


def _vector_text(values: Sequence[float], precision: int = 4) -> str:
    """Format a vector for a pgvector cast. Four decimals keep the payload small."""
    return "[" + ",".join(f"{float(value):.{precision}f}" for value in values) + "]"


@asynccontextmanager
async def _scratch_database() -> AsyncIterator[ScratchDatabase]:
    """Create an empty database with the schema, then drop it."""
    base_dsn = _base_dsn()
    try:
        admin = await asyncpg.connect(base_dsn)
    except Exception as exc:  # pragma: no cover - depends on the host
        pytest.skip(f"PostgreSQL is not available: {exc}")
    name = f"virgil_recall_{uuid.uuid4().hex[:10]}"
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    except Exception as exc:  # pragma: no cover - depends on the host
        await admin.close()
        pytest.skip(f"Cannot create a scratch database: {exc}")

    dsn = f"{base_dsn.rsplit('/', 1)[0]}/{name}"
    engine = create_async_engine(
        dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await connection.run_sync(Base.metadata.create_all)
        yield ScratchDatabase(
            name=name,
            dsn=dsn,
            engine=engine,
            sessionmaker=async_sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False
            ),
        )
    finally:
        await engine.dispose()
        admin = await asyncpg.connect(base_dsn)
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        finally:
            await admin.close()


async def _insert_user(dsn: str, user_id: uuid.UUID) -> None:
    """Create the owner row that the chunk foreign key requires."""
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute(
            "INSERT INTO users (id, display_name) VALUES ($1, $2)",
            user_id,
            f"recall-user-{user_id.hex[:8]}",
        )
    finally:
        await connection.close()


async def _insert_chunks(
    dsn: str, chunks: Sequence[tuple[uuid.UUID, uuid.UUID, str, str, str, datetime]]
) -> None:
    connection = await asyncpg.connect(dsn)
    try:
        await connection.executemany(
            INSERT_CHUNK,
            [
                (chunk_id, user_id, content, embedding, model, created_at)
                for chunk_id, user_id, content, embedding, model, created_at in chunks
            ],
        )
    finally:
        await connection.close()


def _near_query(rng: np.random.Generator, noise: float = 1.2) -> np.ndarray:
    """Return a unit vector near the shared direction, with controlled cosine spread."""
    base = np.zeros(DIMENSIONS, dtype=np.float64)
    base[0] = 1.0
    return base + noise * rng.standard_normal(DIMENSIONS) / math.sqrt(DIMENSIONS)


def _unit(values: np.ndarray) -> np.ndarray:
    return values / np.linalg.norm(values)


@pytest.mark.asyncio
async def test_recall_at_100k() -> None:
    """Recall@50 of the filtered approximate scan against a brute force scan.

    100k chunks across 100 users means the tenant filter keeps 1 percent of the
    heap. Without the per query HNSW settings the scan returns almost nothing.
    """
    rng = np.random.default_rng(20261101)
    created_at = datetime.now(timezone.utc)
    user_ids = [uuid.uuid4() for _ in range(RECALL_USERS)]

    async with _scratch_database() as scratch:
        connection = await asyncpg.connect(scratch.dsn)
        try:
            await connection.executemany(
                "INSERT INTO users (id, display_name) VALUES ($1, $2)",
                [(user_id, f"recall-user-{index}") for index, user_id in enumerate(user_ids)],
            )
            # Build the graph once, from the finished table.
            await connection.execute(
                "DROP INDEX IF EXISTS ix_memory_chunks_embedding_hnsw"
            )
            await connection.execute("SET maintenance_work_mem = '512MB'")
            await connection.execute("SET max_parallel_maintenance_workers = 0")
            await connection.execute("SET synchronous_commit = off")

            batch: list[tuple] = []
            for index in range(RECALL_TOTAL):
                vector = _unit(_near_query(rng))
                batch.append(
                    (
                        uuid.uuid4(),
                        user_ids[index % RECALL_USERS],
                        f"recall chunk {index}",
                        _vector_text(vector),
                        ACTIVE_MODEL,
                        created_at,
                    )
                )
                if len(batch) == 1000:
                    await connection.executemany(INSERT_CHUNK, batch)
                    batch.clear()
            if batch:
                await connection.executemany(INSERT_CHUNK, batch)

            await connection.execute(
                "CREATE INDEX ix_memory_chunks_embedding_hnsw ON memory_chunks "
                "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
            )
        finally:
            await connection.close()

        # Compare the production query with an exact scan on the same user.
        probe = await asyncpg.connect(scratch.dsn)
        try:
            await probe.execute("SET enable_indexscan = off")
            await probe.execute("SET enable_indexonlyscan = off")
            await probe.execute("SET enable_bitmapscan = off")
            user_id = user_ids[0]
            recalls: list[float] = []
            async with scratch.sessionmaker() as session:
                service = MemoryService(session=session, settings=Settings())
                policy = RetrievalPolicy.from_settings(Settings()).with_top_n(50)
                for query_index in range(RECALL_QUERIES):
                    query = _unit(_near_query(rng))
                    exact_rows = await probe.fetch(
                        "SELECT id FROM memory_chunks "
                        "WHERE user_id = $1 AND scope = 'global' AND excluded = false "
                        "AND embedding_model = $2 "
                        "ORDER BY embedding <=> $3::vector LIMIT 50",
                        user_id,
                        ACTIVE_MODEL,
                        _vector_text(query, precision=6),
                    )
                    approximate = await service.search_embedding(
                        user_id,
                        [float(value) for value in query],
                        scopes=["global"],
                        policy=policy,
                    )
                    exact_ids = {row["id"] for row in exact_rows}
                    approximate_ids = {result.id for result in approximate}
                    assert len(exact_ids) == 50
                    recall = len(exact_ids & approximate_ids) / len(exact_ids)
                    recalls.append(recall)

                # The tenant holds 1000 rows, so the exact scan answers. It is
                # exact by construction, and the graph settings do not apply.
                assert service.last_retrieval_path == "exact"
                index_scan = await session.execute(text("SHOW enable_indexscan"))
                assert index_scan.scalar() == "off"
        finally:
            await probe.close()

        mean_recall = sum(recalls) / len(recalls)
        print(
            "recall@50 per query "
            f"{[round(value, 3) for value in recalls]}, mean {mean_recall:.3f}"
        )
        assert mean_recall >= RECALL_FLOOR, (
            f"recall@50 {mean_recall:.3f} below {RECALL_FLOOR}: {recalls}"
        )


@pytest.mark.asyncio
async def test_model_filter() -> None:
    """A live row from another embedding model never appears in the result."""
    user_id = uuid.uuid4()
    vector = np.zeros(DIMENSIONS)
    vector[0] = 1.0
    embedding = _vector_text(vector)
    now = datetime.now(timezone.utc)

    async with _scratch_database() as scratch:
        await _insert_user(scratch.dsn, user_id)
        await _insert_chunks(
            scratch.dsn,
            [
                (uuid.uuid4(), user_id, "active model chunk", embedding, ACTIVE_MODEL, now),
                (uuid.uuid4(), user_id, "other model chunk", embedding, OTHER_MODEL, now),
            ],
        )
        async with scratch.sessionmaker() as session:
            service = MemoryService(session=session, settings=Settings())
            results = await service.search_embedding(
                user_id,
                [float(value) for value in vector],
                scopes=["global"],
                policy=RetrievalPolicy.from_settings(Settings()),
            )

    assert [result.content for result in results] == ["active model chunk"]


@pytest.mark.asyncio
async def test_decay_from_database() -> None:
    """The database age and the scoring rules produce the documented curve."""
    user_id = uuid.uuid4()
    chunk = np.zeros(DIMENSIONS)
    chunk[0] = 1.0
    # A query at 53 degrees: the cosine similarity is 0.6, below the override.
    query = np.zeros(DIMENSIONS)
    query[0] = 0.6
    query[1] = 0.8
    embedding = _vector_text(chunk)
    now = datetime.now(timezone.utc)

    async with _scratch_database() as scratch:
        await _insert_user(scratch.dsn, user_id)
        await _insert_chunks(
            scratch.dsn,
            [
                (uuid.uuid4(), user_id, "today", embedding, ACTIVE_MODEL, now),
                (
                    uuid.uuid4(),
                    user_id,
                    "30 days",
                    embedding,
                    ACTIVE_MODEL,
                    now - timedelta(days=30),
                ),
            ],
        )
        async with scratch.sessionmaker() as session:
            service = MemoryService(session=session, settings=Settings())
            results = await service.search_embedding(
                user_id,
                [float(value) for value in query],
                scopes=["global"],
                policy=RetrievalPolicy.from_settings(Settings()),
            )

    scored = {result.content: result for result in results}
    assert set(scored) == {"today", "30 days"}
    for result in results:
        assert result.similarity == pytest.approx(0.6, abs=1e-3)
    assert scored["today"].age_days == pytest.approx(0.0, abs=0.01)
    assert scored["30 days"].age_days == pytest.approx(30.0, abs=0.01)
    assert scored["today"].final_score == pytest.approx(0.6, abs=1e-3)
    assert scored["30 days"].final_score == pytest.approx(
        0.6 * (0.35 + 0.65 * 0.5), abs=1e-3
    )
    # The fresher chunk outranks the older one at equal similarity.
    assert [result.content for result in results] == ["today", "30 days"]


@pytest.mark.asyncio
async def test_delete_removes_chunk() -> None:
    """Delete removes the row, and a second delete reports that it is gone."""
    user_id = uuid.uuid4()
    vector = np.zeros(DIMENSIONS)
    vector[0] = 1.0
    now = datetime.now(timezone.utc)
    chunk_id = uuid.uuid4()

    async with _scratch_database() as scratch:
        await _insert_user(scratch.dsn, user_id)
        await _insert_chunks(
            scratch.dsn,
            [
                (
                    chunk_id,
                    user_id,
                    "forget me",
                    _vector_text(vector),
                    ACTIVE_MODEL,
                    now,
                )
            ],
        )
        async with scratch.sessionmaker() as session:
            service = MemoryService(session=session, settings=Settings())
            await service.save_chunk(
                MemoryChunkCreateDTO(
                    user_id=user_id,
                    scope="global",
                    content="a second chunk",
                    source_message_ids=[],
                    embedding=[float(value) for value in vector],
                    embedding_model=ACTIVE_MODEL,
                )
            )
            await session.commit()

            assert await service.delete_chunk(chunk_id) is True
            assert await service.get_chunk_by_id(chunk_id) is None
            await session.commit()
            assert await service.get_chunk_by_id(chunk_id) is None
            assert await service.delete_chunk(chunk_id) is False
            results = await service.search_embedding(
                user_id,
                [float(value) for value in vector],
                scopes=["global"],
                policy=RetrievalPolicy.from_settings(Settings()),
            )

    assert [result.content for result in results] == ["a second chunk"]


def _percentile(values: Sequence[float], fraction: float) -> float:
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


async def _fill_table(
    scratch: ScratchDatabase,
    user_ids: Sequence[uuid.UUID],
    chunks_per_user: int,
    build_global: bool = True,
) -> None:
    """Insert the rows, then build the graph once from the finished table.

    The graph is dropped before the insert and created after it, so the insert
    measures the table and not the graph maintenance. ``build_global`` builds
    the global graph, which is the schema default. A caller that installs a
    tenant graph passes false, because that graph is the one it measures.
    """
    rng = np.random.default_rng(20261101)
    created_at = datetime.now(timezone.utc)
    total = len(user_ids) * chunks_per_user
    connection = await asyncpg.connect(scratch.dsn)
    try:
        await connection.executemany(
            "INSERT INTO users (id, display_name) VALUES ($1, $2)",
            [
                (user_id, f"recall-user-{index}")
                for index, user_id in enumerate(user_ids)
            ],
        )
        await connection.execute(
            "DROP INDEX IF EXISTS ix_memory_chunks_embedding_hnsw"
        )
        await connection.execute("SET maintenance_work_mem = '512MB'")
        await connection.execute("SET max_parallel_maintenance_workers = 0")
        await connection.execute("SET synchronous_commit = off")

        batch: list[tuple] = []
        for index in range(total):
            batch.append(
                (
                    uuid.uuid4(),
                    user_ids[index % len(user_ids)],
                    f"recall chunk {index}",
                    _vector_text(_unit(_near_query(rng))),
                    ACTIVE_MODEL,
                    created_at,
                )
            )
            if len(batch) == BULK_INSERT_BATCH:
                await connection.executemany(INSERT_CHUNK, batch)
                batch.clear()
        if batch:
            await connection.executemany(INSERT_CHUNK, batch)

        if build_global:
            await connection.execute(
                "CREATE INDEX ix_memory_chunks_embedding_hnsw ON memory_chunks "
                "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
            )
        # A table that a bulk load filled has no statistics until this runs. The
        # planner needs them to choose the tenant index and the sort, so the
        # measured latency reflects the shipped path and not a stale plan.
        await connection.execute("ANALYZE memory_chunks")
    finally:
        await connection.close()


async def _exact_top_ids(
    probe: asyncpg.Connection,
    user_id: uuid.UUID,
    query: np.ndarray,
    top_n: int = 50,
) -> set[uuid.UUID]:
    """Return the exact top n ids for one query, with every index path off."""
    async with probe.transaction():
        await probe.execute("SET LOCAL enable_indexscan = off")
        await probe.execute("SET LOCAL enable_indexonlyscan = off")
        await probe.execute("SET LOCAL enable_bitmapscan = off")
        rows = await probe.fetch(
            "SELECT id FROM memory_chunks "
            "WHERE user_id = $1 AND scope = 'global' AND excluded = false "
            "AND embedding_model = $2 "
            "ORDER BY embedding <=> $3::vector LIMIT $4",
            user_id,
            ACTIVE_MODEL,
            _vector_text(query, precision=6),
            top_n,
        )
    return {row["id"] for row in rows}


async def _measure_shipped_path(
    scratch: ScratchDatabase,
    user_ids: Sequence[uuid.UUID],
    queries_per_user: int,
    top_n: int = 50,
) -> dict:
    """Measure recall@50 and the latency of the shipped path against an exact scan.

    The service chooses the path. The result records the path name, so a test
    can state which path answered the query and not only its recall.
    """
    rng = np.random.default_rng(20261102)
    recalls: list[float] = []
    latencies: list[float] = []
    paths: set[str] = set()
    probe = await asyncpg.connect(scratch.dsn)
    try:
        async with scratch.sessionmaker() as session:
            service = MemoryService(session=session, settings=Settings())
            policy = RetrievalPolicy.from_settings(Settings()).with_top_n(top_n)
            for user_id in user_ids:
                for _ in range(queries_per_user):
                    query = _unit(_near_query(rng))
                    expected = await _exact_top_ids(probe, user_id, query, top_n)
                    started = time.perf_counter()
                    results = await service.search_embedding(
                        user_id,
                        [float(value) for value in query],
                        scopes=["global"],
                        policy=policy,
                    )
                    latencies.append((time.perf_counter() - started) * 1000.0)
                    paths.add(service.last_retrieval_path or "unknown")
                    found = {result.id for result in results}
                    recalls.append(len(expected & found) / len(expected))
            await session.rollback()
    finally:
        await probe.close()
    return {"paths": paths, "recalls": recalls, "latencies": latencies}


def _report(name: str, measured: dict) -> float:
    """Print one measurement line and return the mean recall."""
    recalls = measured["recalls"]
    mean_recall = sum(recalls) / len(recalls)
    print(
        f"{name}: paths={sorted(measured['paths'])} queries={len(recalls)} "
        f"recall@50 mean={mean_recall:.6f} min={min(recalls):.3f} "
        f"p50={_percentile(measured['latencies'], 0.50):.2f}ms "
        f"p95={_percentile(measured['latencies'], 0.95):.2f}ms"
    )
    return mean_recall


@pytest.mark.asyncio
async def test_recall_many_light_users() -> None:
    """100k rows over 100 users: the shipped path is the exact scan, and it holds.

    Every tenant holds 1000 rows. That is below the measured crossover, so the
    answer must come from the tenant rows and an exact sort. The vector index
    reaches recall@50 of 0.56 on the same rows, and the test would catch a
    return to that path.
    """
    user_ids = [uuid.uuid4() for _ in range(RECALL_USERS)]
    policy = RetrievalPolicy.from_settings(Settings())
    assert policy.exact_scan_max_rows >= RECALL_CHUNKS_PER_USER, (
        "the crossover is below one light tenant, so the exact path cannot serve "
        f"a tenant of {RECALL_CHUNKS_PER_USER} rows"
    )

    async with _scratch_database() as scratch:
        await _fill_table(scratch, user_ids, RECALL_CHUNKS_PER_USER)
        measured = await _measure_shipped_path(
            scratch, user_ids[:3], RECALL_QUERIES
        )

    mean_recall = _report("many light users", measured)
    assert measured["paths"] == {"exact"}
    assert mean_recall >= RECALL_FLOOR, (
        f"recall@50 {mean_recall:.6f} below {RECALL_FLOOR}: {measured['recalls']}"
    )


@pytest.mark.asyncio
async def test_recall_heavy_user() -> None:
    """50k rows in one tenant: the shipped path meets the floor.

    The measurement sets the crossover below 50,000 rows, so this tenant gets
    its own graph. On the same rows the global graph reaches recall@50 of 0.5612
    at ef_search 200 and 0.8483 at 1000, because its walk spends its budget on
    the rows of other tenants. A graph that holds this tenant alone, built with
    m=32 and ef_construction=200, reaches 0.9796 at ef_search 1000.

    The test first reads the fallback: a tenant above the crossover with no
    graph gets the exact scan, and that answer is exact. Then it installs the
    graph, and the shipped path must reach the floor on the graph.
    """
    user_ids = [uuid.uuid4()]
    policy = RetrievalPolicy.from_settings(Settings())
    assert policy.exact_scan_max_rows < HEAVY_USER_CHUNKS, (
        "the crossover is at or above the heavy tenant, so the exact scan would "
        f"answer a tenant of {HEAVY_USER_CHUNKS} rows"
    )

    async with _scratch_database() as scratch:
        await _fill_table(
            scratch, user_ids, HEAVY_USER_CHUNKS, build_global=False
        )

        # No tenant graph yet: the exact scan answers, and it is exact.
        fallback = await _measure_shipped_path(scratch, user_ids, 3)
        fallback_recall = _report("heavy user, no graph", fallback)
        assert fallback["paths"] == {"exact"}
        assert fallback_recall == 1.0

        # Install the graph: the maintenance step for a tenant above the
        # crossover. It also retires the global graph.
        async with scratch.sessionmaker() as session:
            await create_tenant_graph_index(session, user_ids[0])
            await session.commit()

        measured = await _measure_shipped_path(
            scratch, user_ids, HEAVY_USER_QUERIES
        )

    mean_recall = _report("heavy user", measured)
    assert measured["paths"] == {"hnsw"}
    assert mean_recall >= RECALL_FLOOR, (
        f"recall@50 {mean_recall:.6f} below {RECALL_FLOOR}: {measured['recalls']}"
    )
