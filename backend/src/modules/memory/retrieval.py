"""Time-decay retrieval: the candidate query, the scoring, and the ranking.

The score is cosine similarity decayed by age with a floor, and bypassed for a
strong match. The override threshold is measured, not guessed. Correct answers
score 0.679 to 0.801 on the local model and the best wrong answers score 0.561
to 0.615, so 0.65 separates them.

The database returns candidates only. Similarity, age, the minimum cut, the
override, and the final order are computed here, so the scoring rules are the
same for every caller and can be tested without a database.

The candidate query takes one of two paths. A tenant that holds few rows gets
an exact scan, because an exact scan of a small slice is both correct and fast.
A tenant above the measured crossover gets its own graph, built for that tenant.
A large tenant without a graph gets the exact scan too, so a query never takes a
path that misses the recall floor. The exact scan is the default at low row
counts, and the vector index is not the default path there.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Literal, Sequence
from uuid import UUID

from sqlalchemy import Select, func, literal, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import TextClause

from src.config.settings import Settings
from src.modules.memory.repository import MemorySearchResultDTO
from src.schemas.memory import MemoryChunkModel

SECONDS_PER_DAY = 86400.0
MINIMUM_SIMILARITY = 0.25
ITERATIVE_SCAN_MODES = ("off", "relaxed_order", "strict_order")

# The exact scan refuses the graph walk and keeps the btree scans. A bitmap scan
# of the tenant index still reduces the heap, and the sort that follows is
# exact. The vector index cannot serve a bitmap scan, so it cannot be chosen.
# Each path states its own settings, including the ones the other path changes,
# because a SET LOCAL stays in force until the transaction ends.
EXACT_SCAN_SETTINGS = (
    "SET LOCAL enable_indexscan = off",
    "SET LOCAL enable_indexonlyscan = off",
    "SET LOCAL enable_sort = on",
    "SET LOCAL plan_cache_mode = force_custom_plan",
)

# The graph path refuses the exact sort. Without a sort the btree scan cannot
# answer an order by distance query, so the vector index answers it. A disabled
# node is a cost penalty, not a prohibition, so the statement always plans.
# The custom plan is required: a generic plan cannot prove that a parameter
# matches the WHERE clause of a per-tenant partial index.
GRAPH_PATH_SETTINGS = (
    "SET LOCAL enable_indexscan = on",
    "SET LOCAL enable_indexonlyscan = on",
    "SET LOCAL enable_sort = off",
    "SET LOCAL plan_cache_mode = force_custom_plan",
)

# The measured build settings of a per-tenant graph. The shipped global graph
# uses m=16 and ef_construction=64. That graph reaches recall@50 of 0.848 at
# ef_search 1000 on the benchmark distribution, even when the tenant owns every
# row. A partial index for one tenant, built with m=32 and ef_construction=200,
# reaches 0.9796 at ef_search 1000 on the same rows. The measurement is in
# section 3.5 of docs/BENCHMARKS.md.
TENANT_GRAPH_M = 32
TENANT_GRAPH_EF_CONSTRUCTION = 200
TENANT_GRAPH_EF_SEARCH = 1000
TENANT_INDEX_PREFIX = "ix_memory_chunks_hnsw_"
GLOBAL_GRAPH_INDEX = "ix_memory_chunks_embedding_hnsw"


@dataclass(frozen=True)
class RetrievalPolicy:
    """The measured retrieval parameters. Settings supply every value."""

    half_life_days: float = 30.0
    floor: float = 0.35
    override_threshold: float = 0.65
    candidate_k: int = 50
    top_n: int = 8
    minimum_similarity: float = MINIMUM_SIMILARITY
    ef_search: int = 200
    iterative_scan: Literal["off", "relaxed_order", "strict_order"] = "relaxed_order"
    # Measured crossover. At or below this tenant row count the exact scan is
    # the shipped path, because it meets the recall floor and it is exact. The
    # measurement is in section 3 of docs/BENCHMARKS.md.
    exact_scan_max_rows: int = 20000
    # The measured width of the per-tenant graph. The global graph cannot reach
    # the floor at any width, and a tenant graph at 200 reaches 0.8048 at 50,000
    # rows. At 1000 it reaches 0.9796. The measurement is in section 3.5.
    tenant_graph_ef_search: int = TENANT_GRAPH_EF_SEARCH

    @classmethod
    def from_settings(cls, settings: Settings) -> "RetrievalPolicy":
        """Build the policy from application settings. No value is hardcoded twice."""
        return cls(
            half_life_days=settings.MEMORY_RETRIEVAL_HALF_LIFE_DAYS,
            floor=settings.MEMORY_RETRIEVAL_FLOOR,
            override_threshold=settings.MEMORY_RETRIEVAL_OVERRIDE_THRESHOLD,
            candidate_k=settings.MEMORY_RETRIEVAL_CANDIDATE_K,
            top_n=settings.MEMORY_RETRIEVAL_TOP_N,
            minimum_similarity=settings.MEMORY_RETRIEVAL_MIN_SIMILARITY,
            ef_search=settings.MEMORY_RETRIEVAL_EF_SEARCH,
            iterative_scan=settings.MEMORY_RETRIEVAL_ITERATIVE_SCAN,
            exact_scan_max_rows=settings.MEMORY_RETRIEVAL_EXACT_SCAN_MAX_ROWS,
            tenant_graph_ef_search=settings.MEMORY_RETRIEVAL_TENANT_GRAPH_EF_SEARCH,
        )

    def with_top_n(self, top_n: int | None) -> "RetrievalPolicy":
        """Return a copy with a caller chosen result count, bounded by the pool."""
        if top_n is None:
            return self
        if top_n < 1:
            raise ValueError("top_n must be at least 1")
        return replace(self, top_n=top_n)


@dataclass(frozen=True)
class RetrievalCandidate:
    """One row of the approximate scan, before the decay rules are applied."""

    id: UUID
    content: str
    summary: str | None
    similarity: float
    age_days: float
    embedding_model: str


def decay_factor(age_days: float, half_life_days: float) -> float:
    """Return 0.5 ** (age / half life). A future timestamp counts as age zero."""
    if half_life_days <= 0:
        raise ValueError("half_life_days must be greater than zero")
    age = max(0.0, float(age_days))
    return 0.5 ** (age / float(half_life_days))


def recency_multiplier(age_days: float, policy: RetrievalPolicy) -> float:
    """Return floor + (1 - floor) * decay. It never drops below the floor."""
    return policy.floor + (1.0 - policy.floor) * decay_factor(
        age_days, policy.half_life_days
    )


def final_score(similarity: float, age_days: float, policy: RetrievalPolicy) -> float:
    """Return the similarity, undecayed at or above the override, decayed below it."""
    if similarity >= policy.override_threshold:
        return similarity
    return similarity * recency_multiplier(age_days, policy)


def rank_candidates(
    candidates: Iterable[RetrievalCandidate],
    policy: RetrievalPolicy,
    active_model: str,
) -> list[MemorySearchResultDTO]:
    """Apply the model filter, the minimum cut, the decay rules, and the top cut.

    A chunk from an embedding model that is not the active model never returns.
    A chunk below the minimum similarity never returns. The rest are ordered by
    the final score, then by similarity, then by age, so the order is stable.
    """
    scored: list[MemorySearchResultDTO] = []
    for candidate in candidates:
        if candidate.embedding_model != active_model:
            continue
        if candidate.similarity < policy.minimum_similarity:
            continue
        scored.append(
            MemorySearchResultDTO(
                id=candidate.id,
                content=candidate.content,
                summary=candidate.summary,
                similarity=candidate.similarity,
                age_days=candidate.age_days,
                final_score=final_score(
                    candidate.similarity, candidate.age_days, policy
                ),
            )
        )
    scored.sort(key=lambda item: (-item.final_score, -item.similarity, str(item.id)))
    return scored[: policy.top_n]


def hnsw_statements(policy: RetrievalPolicy) -> list[TextClause]:
    """Return the per query HNSW settings.

    The tenant, scope, and model predicates run after the approximate scan. Without
    a wider search width and an iterative scan, a filtered query collapses to a
    handful of rows. The values are typed integers and a checked mode, so the
    statement text is safe.
    """
    if policy.iterative_scan not in ITERATIVE_SCAN_MODES:
        raise ValueError(f"unsupported hnsw.iterative_scan mode: {policy.iterative_scan}")
    if policy.ef_search < 1:
        raise ValueError("ef_search must be at least 1")
    return [
        text(f"SET LOCAL hnsw.ef_search = {int(policy.ef_search)}"),
        text(f"SET LOCAL hnsw.iterative_scan = '{policy.iterative_scan}'"),
    ]


def candidate_statement(
    user_id: UUID,
    query_embedding: Sequence[float],
    scopes: Sequence[str],
    policy: RetrievalPolicy,
    active_model: str,
) -> Select:
    """Build the approximate scan. It returns candidates, not a final ranking."""
    distance = MemoryChunkModel.embedding.cosine_distance(list(query_embedding))
    age_days = (
        func.extract("epoch", func.now() - MemoryChunkModel.created_at)
        / literal(SECONDS_PER_DAY)
    )
    return (
        select(
            MemoryChunkModel.id,
            MemoryChunkModel.content,
            MemoryChunkModel.summary,
            (literal(1.0) - distance).label("similarity"),
            age_days.label("age_days"),
            MemoryChunkModel.embedding_model,
        )
        .where(
            MemoryChunkModel.user_id == user_id,
            MemoryChunkModel.scope.in_(list(scopes)),
            MemoryChunkModel.excluded.is_(False),
            MemoryChunkModel.embedding_model == active_model,
        )
        .order_by(distance.asc())
        .limit(policy.candidate_k)
    )


def tenant_row_count_statement(user_id: UUID) -> Select:
    """Count every chunk that one tenant holds.

    The count sizes the tenant, not one scope. The scope, the excluded flag, and
    the model predicate narrow one query. The tenant row count decides the path
    for every query of that tenant.
    """
    return (
        select(func.count())
        .select_from(MemoryChunkModel)
        .where(MemoryChunkModel.user_id == user_id)
    )


def exact_scan_statements() -> tuple[TextClause, ...]:
    """Return the session settings of the exact scan.

    The graph walk is refused. The btree scans stay available, so the tenant
    index still reduces the heap and the sort that follows is exact.
    """
    return tuple(text(statement) for statement in EXACT_SCAN_SETTINGS)


def graph_path_statements(policy: RetrievalPolicy) -> tuple[TextClause, ...]:
    """Return the session settings of the graph path.

    The width is the measured width of a tenant graph, not the width of the
    global graph. The exact sort is refused, so a tenant scan cannot answer the
    query in a different order.
    """
    tenant_policy = replace(policy, ef_search=policy.tenant_graph_ef_search)
    return tuple(hnsw_statements(tenant_policy)) + tuple(
        text(statement) for statement in GRAPH_PATH_SETTINGS
    )


def tenant_index_name(user_id: UUID) -> str:
    """Return the name of the graph index that belongs to one tenant.

    The name is derived from the tenant id, so the maintenance step and the
    query agree without a lookup table.
    """
    return f"{TENANT_INDEX_PREFIX}{user_id.hex}"


def tenant_graph_ddl(user_id: UUID) -> str:
    """Return the statement that builds the graph of one tenant.

    The graph covers one tenant, so a walk never spends its budget on rows that
    the tenant filter rejects. The name comes from a UUID and the build values
    are integers, so the statement text is safe. The build blocks writes for its
    duration, so run it at maintenance time, not in a request.
    """
    name = tenant_index_name(user_id)
    return (
        f"CREATE INDEX IF NOT EXISTS {name} ON memory_chunks USING hnsw "
        f"(embedding vector_cosine_ops) WITH (m = {TENANT_GRAPH_M}, "
        f"ef_construction = {TENANT_GRAPH_EF_CONSTRUCTION}) "
        f"WHERE user_id = '{user_id}'"
    )


def tenant_index_exists_statement(user_id: UUID) -> Select:
    """Return a statement that tests for the graph index of one tenant."""
    return select(func.to_regclass(literal(tenant_index_name(user_id))).is_not(None))


async def create_tenant_graph_index(session: AsyncSession, user_id: UUID) -> None:
    """Build the graph of one tenant, and retire the global graph.

    The global graph covers every tenant, so a filtered walk spends its budget
    on rows that the filter rejects. It also stays a valid plan for the tenant
    query, and the planner can choose it over the tenant graph. After this
    statement the tenant graphs are the only graph indexes, and each query has
    one usable graph.

    The build blocks writes for its duration, so run it at maintenance time. A
    tenant above the crossover that has no graph yet gets the exact scan: that
    query is correct, and it is slower.
    """
    # The build needs memory, and a parallel build needs shared memory that a
    # small container does not have. One worker keeps the build in the backend.
    await session.execute(text("SET LOCAL maintenance_work_mem = '512MB'"))
    await session.execute(text("SET LOCAL max_parallel_maintenance_workers = 0"))
    await session.execute(text(tenant_graph_ddl(user_id)))
    await session.execute(text(f"DROP INDEX IF EXISTS {GLOBAL_GRAPH_INDEX}"))


@dataclass(frozen=True)
class RetrievalPlan:
    """The chosen path for one query, and the statements that run it.

    ``path`` is the name the caller reports. ``tenant_rows`` is the count that
    decided the path. The caller runs ``statements`` first, then ``statement``,
    in one transaction, so the per query settings apply to the query.
    """

    path: Literal["exact", "hnsw"]
    tenant_rows: int
    statements: tuple[TextClause, ...]
    statement: Select


async def plan_retrieval(
    session: AsyncSession,
    user_id: UUID,
    query_embedding: Sequence[float],
    scopes: Sequence[str],
    policy: RetrievalPolicy,
    active_model: str,
) -> RetrievalPlan:
    """Route one query by the row count of its tenant.

    A tenant at or below ``exact_scan_max_rows`` gets the exact scan. A larger
    tenant gets its own graph, when that graph exists. A large tenant without a
    graph gets the exact scan: the query is then correct but slower, and the
    service never ships a path that misses the recall floor.
    """
    counted = await session.execute(tenant_row_count_statement(user_id))
    tenant_rows = int(counted.scalar_one())
    statement = candidate_statement(
        user_id, query_embedding, scopes, policy, active_model
    )
    if tenant_rows <= policy.exact_scan_max_rows:
        return RetrievalPlan(
            path="exact",
            tenant_rows=tenant_rows,
            statements=exact_scan_statements(),
            statement=statement,
        )
    present = await session.execute(tenant_index_exists_statement(user_id))
    if bool(present.scalar()):
        return RetrievalPlan(
            path="hnsw",
            tenant_rows=tenant_rows,
            statements=graph_path_statements(policy),
            statement=statement,
        )
    return RetrievalPlan(
        path="exact",
        tenant_rows=tenant_rows,
        statements=exact_scan_statements(),
        statement=statement,
    )

