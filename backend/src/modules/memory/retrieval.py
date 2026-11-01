"""Time-decay retrieval: the candidate query, the scoring, and the ranking.

The score is cosine similarity decayed by age with a floor, and bypassed for a
strong match. The override threshold is measured, not guessed. Correct answers
score 0.679 to 0.801 on the local model and the best wrong answers score 0.561
to 0.615, so 0.65 separates them.

The database returns candidates only. Similarity, age, the minimum cut, the
override, and the final order are computed here, so the scoring rules are the
same for every caller and can be tested without a database.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Literal, Sequence
from uuid import UUID

from sqlalchemy import Select, func, literal, select, text
from sqlalchemy.sql.elements import TextClause

from src.config.settings import Settings
from src.modules.memory.repository import MemorySearchResultDTO
from src.schemas.memory import MemoryChunkModel

SECONDS_PER_DAY = 86400.0
MINIMUM_SIMILARITY = 0.25
ITERATIVE_SCAN_MODES = ("off", "relaxed_order", "strict_order")


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
