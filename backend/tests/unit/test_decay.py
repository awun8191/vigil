"""Unit tests for the time decay retrieval rules (V-11).

The database returns candidates only. These tests exercise the rules that score
them, so they need no Postgres.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from src.config.settings import Settings
from src.modules.memory.retrieval import (
    MINIMUM_SIMILARITY,
    RetrievalCandidate,
    RetrievalPolicy,
    candidate_statement,
    final_score,
    hnsw_statements,
    rank_candidates,
    recency_multiplier,
)

ACTIVE_MODEL = "bge-small-en-v1.5"


def _candidate(
    similarity: float,
    age_days: float,
    *,
    model: str = ACTIVE_MODEL,
    content: str = "chunk",
) -> RetrievalCandidate:
    """Build one scanned row with a stable identity and a chosen score."""
    return RetrievalCandidate(
        id=uuid4(),
        content=content,
        summary=None,
        similarity=similarity,
        age_days=age_days,
        embedding_model=model,
    )


def _rank(
    candidates: list[RetrievalCandidate], policy: RetrievalPolicy | None = None
) -> list:
    return rank_candidates(candidates, policy or RetrievalPolicy(), ACTIVE_MODEL)


def test_policy_defaults_are_the_measured_values() -> None:
    """The shipped settings carry the measured parameters, not provisional ones."""
    fields = Settings.model_fields
    assert fields["MEMORY_RETRIEVAL_HALF_LIFE_DAYS"].default == 30.0
    assert fields["MEMORY_RETRIEVAL_FLOOR"].default == 0.35
    assert fields["MEMORY_RETRIEVAL_OVERRIDE_THRESHOLD"].default == 0.65
    assert fields["MEMORY_RETRIEVAL_MIN_SIMILARITY"].default == 0.25
    assert fields["MEMORY_RETRIEVAL_CANDIDATE_K"].default == 50
    assert fields["MEMORY_RETRIEVAL_TOP_N"].default == 8

    policy = RetrievalPolicy.from_settings(Settings())
    assert policy.override_threshold == fields["MEMORY_RETRIEVAL_OVERRIDE_THRESHOLD"].default


def test_decay_curve() -> None:
    """Today keeps the similarity, 30 days halves the decay, 10 years keeps the floor."""
    similarity = 0.60
    policy = RetrievalPolicy()
    today = _candidate(similarity, 0.0, content="today")
    month_old = _candidate(similarity, 30.0, content="30 days")
    decade_old = _candidate(similarity, 3650.0, content="3650 days")

    results = _rank([today, month_old, decade_old], policy)
    scored = {result.content: result for result in results}

    # Today: the multiplier is 1.0, so the score is the similarity itself.
    assert scored["today"].final_score == pytest.approx(similarity, abs=1e-12)
    # One half life: decay is 0.5, so the multiplier is floor + (1 - floor) * 0.5.
    assert scored["30 days"].final_score == pytest.approx(
        similarity * (0.35 + 0.65 * 0.5), abs=1e-12
    )
    # Ten years: the decay term is negligible and the floor holds.
    assert scored["3650 days"].final_score == pytest.approx(
        similarity * 0.35, abs=1e-9
    )
    # The parts travel with the score, so a bad ranking can be debugged.
    assert scored["30 days"].similarity == pytest.approx(similarity)
    assert scored["30 days"].age_days == pytest.approx(30.0)
    # The freshest chunk ranks first at equal similarity.
    assert [result.content for result in results] == [
        "today",
        "30 days",
        "3650 days",
    ]


def test_override() -> None:
    """A strong match at any age returns the undecayed similarity."""
    policy = RetrievalPolicy()
    strong = _candidate(0.70, 400.0, content="strong")
    results = _rank([strong], policy)

    assert len(results) == 1
    assert results[0].final_score == pytest.approx(0.70, abs=1e-12)
    # It must not be the decayed value, which would be below 0.25 here.
    decayed = 0.70 * recency_multiplier(400.0, policy)
    assert results[0].final_score > decayed * 2

    # The gate is inclusive: exactly 0.65 bypasses the decay.
    at_gate = _candidate(0.65, 400.0)
    assert final_score(at_gate.similarity, at_gate.age_days, policy) == pytest.approx(
        0.65, abs=1e-12
    )
    # One thousandth below it, the decay applies.
    below_gate = _candidate(0.649, 400.0)
    assert final_score(
        below_gate.similarity, below_gate.age_days, policy
    ) == pytest.approx(0.649 * recency_multiplier(400.0, policy), abs=1e-12)


def test_minimum_cut() -> None:
    """A chunk below similarity 0.25 never returns."""
    at_cut = _candidate(MINIMUM_SIMILARITY, 0.0, content="at the cut")
    below_cut = _candidate(0.20, 0.0, content="below the cut")

    contents = [result.content for result in _rank([below_cut, at_cut])]

    assert "below the cut" not in contents
    assert "at the cut" in contents
    assert all(
        result.similarity >= MINIMUM_SIMILARITY for result in _rank([below_cut, at_cut])
    )


def test_model_filter() -> None:
    """A chunk from another embedding model never appears, however close it scores."""
    fresh_model = _candidate(0.90, 0.0, content="active model")
    stale_model = _candidate(
        0.99, 0.0, model="text-embedding-3-small", content="other model"
    )

    results = _rank([stale_model, fresh_model])

    assert [result.content for result in results] == ["active model"]
    assert _rank([stale_model]) == []


def test_hnsw_settings_are_issued_per_query() -> None:
    """The search width and the iterative scan come from settings."""
    policy = RetrievalPolicy(ef_search=321, iterative_scan="relaxed_order")
    statements = [str(statement) for statement in hnsw_statements(policy)]

    assert any("hnsw.ef_search = 321" in text for text in statements)
    assert any("hnsw.iterative_scan = 'relaxed_order'" in text for text in statements)

    with pytest.raises(ValueError):
        hnsw_statements(RetrievalPolicy(iterative_scan="bogus"))  # type: ignore[arg-type]


def test_candidate_statement_has_no_scoring_constants() -> None:
    """The query carries the filters only. The cut and the threshold are not in it."""
    policy = RetrievalPolicy()
    statement = candidate_statement(
        uuid4(), [0.0] * 384, ["global"], policy, ACTIVE_MODEL
    )
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "memory_chunks.user_id" in sql
    assert "memory_chunks.scope IN" in sql
    assert "memory_chunks.excluded IS false" in sql
    assert "memory_chunks.embedding_model" in sql
    assert "<=" in sql or "<=>" in sql
    # The cut and the override live in the policy, not in the SQL text.
    assert "0.25" not in sql
    assert "0.65" not in sql
