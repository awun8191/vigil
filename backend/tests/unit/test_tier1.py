"""Tests for the Tier 1 Jev gate, the artifact stub, and the batch cap."""
from __future__ import annotations

from typing import Any

import pytest
import tiktoken

from src.config.settings import Settings
from src.integrations.repositories import JevEvaluationResultDTO
from src.modules.compaction.service import CompactionService
from src.modules.compaction.stub import (
    PREVIEW_CHARS,
    STUB_TAIL,
    ArtifactStub,
    build_preview,
    format_size,
)
from src.modules.compaction.tiers import (
    DROP_BELOW_THRESHOLD,
    JEV_BATCH_SIZE,
    MAX_JEV_CALLS,
    HistoryUnit,
    TokenCounter,
    UnitKind,
    Verdict,
)

BULK_LOG = "\n".join(
    f"tests/unit/test_case_{index:03d}.py PASSED                              [ {index}%]"
    for index in range(1, 121)
)

FIXTURE_TEXT = (
    "============================= test session starts ==============================\n"
    "platform linux -- Python 3.12.15, pytest-8.4.2, pluggy-1.6.0\n"
    "rootdir: /srv/virgil/backend\n"
    "collected 312 items\n\n"
    "tests/unit/test_tier0.py ........                                        [  2%]\n"
    "tests/unit/test_tier1.py ........                                        [  5%]\n"
    "============================= 312 passed in 6.42s ==============================\n"
)

# Count of FIXTURE_TEXT by the provider tokenizer for o200k_base models,
# recorded from tiktoken 0.14.0 with encoding_for_model("gpt-4o-mini").
PROVIDER_REFERENCE_TOKENS = 96


class ScriptedJev:
    """Tier 1 test double that returns one fixed probability set for all units."""

    def __init__(self, probabilities: dict[str, float]) -> None:
        self.probabilities = probabilities
        self.batch_sizes: list[int] = []

    async def evaluate(self, state: dict[str, Any], questions: dict[str, str], model: str | None = None):
        raise AssertionError("Tier 1 must score units with evaluate_batch, not one call per unit.")

    async def evaluate_batch(
        self,
        states: list[dict[str, Any]],
        questions: dict[str, str],
        model: str | None = None,
    ) -> list[JevEvaluationResultDTO]:
        self.batch_sizes.append(len(states))
        return [
            JevEvaluationResultDTO(
                answers={key: "no" for key in questions},
                probabilities=dict(self.probabilities),
            )
            for _ in states
        ]

    async def is_healthy(self) -> bool:
        return True


def bulk_unit(unit_id: str = "tool-bulk") -> HistoryUnit:
    """Return one large tool log unit with a full stub provenance set."""
    return HistoryUnit(
        unit_id=unit_id,
        role="tool",
        content=BULK_LOG,
        kind=UnitKind.TOOL_RESULT,
        tool_name="bash",
        command="pytest tests/ -q",
        exit_code=0,
        artifact_id="art-e4b2a1",
        size_bytes=45000,
    )


def history_with(target: HistoryUnit, trailing_turns: int = 6) -> list[HistoryUnit]:
    """Put one unit in the oldest turn and add six later turns after it.

    The target sits before the chain-closing tool result and before the last
    six turns, so Tier 0 leaves it to Tier 1.
    """
    units: list[HistoryUnit] = [
        HistoryUnit(
            unit_id="t1-user",
            role="user",
            content="Start the task.",
            kind=UnitKind.USER,
        ),
        target,
        HistoryUnit(
            unit_id="t1-tool-final",
            role="tool",
            content="final chain output: 6 passed",
            kind=UnitKind.TOOL_RESULT,
            tool_name="bash",
            command="pytest tests/ -q",
            exit_code=0,
            artifact_id="art-final",
            size_bytes=1200,
        ),
    ]
    for turn in range(2, trailing_turns + 2):
        units.append(
            HistoryUnit(
                unit_id=f"t{turn}-user",
                role="user",
                content=f"Follow up {turn}.",
                kind=UnitKind.USER,
            )
        )
        units.append(
            HistoryUnit(
                unit_id=f"t{turn}-assistant",
                role="assistant",
                content=f"Answer {turn}.",
                kind=UnitKind.ASSISTANT,
            )
        )
    return units


def service_with(probabilities: dict[str, float] | None) -> tuple[CompactionService, ScriptedJev | None]:
    """Build a service with a scripted classifier, or with no classifier."""
    if probabilities is None:
        return CompactionService(session=None, settings=Settings(), token_model="gpt-4o-mini"), None
    jev = ScriptedJev(probabilities)
    service = CompactionService(
        session=None,
        settings=Settings(),
        jev_client=jev,
        token_model="gpt-4o-mini",
    )
    return service, jev


@pytest.mark.asyncio
async def test_bulk_becomes_stub() -> None:
    """A unit with p(bulk) 0.9 becomes a stub that carries the artifact id."""
    service, _ = service_with({"needed": 0.1, "user_fact": 0.05, "bulk": 0.9})

    plan = await service.plan_compaction(history_with(bulk_unit()))

    assert [item.unit.unit_id for item in plan.stubbed] == ["tool-bulk"]
    assert plan.stubbed[0].verdict is Verdict.STUB

    stub = plan.stubbed[0].content
    assert plan.stubbed[0].unit.artifact_id == "art-e4b2a1"
    assert "art-e4b2a1" in stub
    assert "tool=bash" in stub
    assert 'command="pytest tests/ -q"' in stub
    assert f"size={format_size(45000)}" in stub
    assert "exit_code=0" in stub
    assert STUB_TAIL in stub

    preview = stub.split("Preview: ", 1)[1].split("\n", 1)[0]
    assert len(preview) == PREVIEW_CHARS
    assert preview == build_preview(BULK_LOG)

    assert "t1-user" in {item.unit.unit_id for item in plan.kept}
    assert plan.compacted_tokens < plan.original_tokens


@pytest.mark.asyncio
async def test_low_value_dropped() -> None:
    """A unit with p(needed) 0.1 leaves the active context."""
    service, _ = service_with({"needed": 0.1, "user_fact": 0.1, "bulk": 0.1})

    plan = await service.plan_compaction(history_with(bulk_unit()))

    assert [item.unit.unit_id for item in plan.dropped] == ["tool-bulk"]
    assert plan.dropped[0].verdict is Verdict.DROP
    assert plan.dropped[0].content == ""
    assert all(item.unit.unit_id != "tool-bulk" for item in plan.kept)
    assert all(item.unit.unit_id != "tool-bulk" for item in plan.stubbed)


@pytest.mark.asyncio
async def test_batch_cap() -> None:
    """200 units produce at most three Jev calls of at most 50 units."""
    units = [
        HistoryUnit(
            unit_id=f"u{index:03d}",
            role="assistant",
            content=f"Intermediate draft {index}.",
            kind=UnitKind.ASSISTANT,
        )
        for index in range(200)
    ]
    service, jev = service_with({"needed": 0.9, "user_fact": 0.0, "bulk": 0.0})
    assert jev is not None

    plan = await service.plan_compaction(units)

    assert plan.tier1.jev_calls <= MAX_JEV_CALLS == 3
    assert len(jev.batch_sizes) == 3
    assert max(jev.batch_sizes) <= JEV_BATCH_SIZE == 50
    assert sum(jev.batch_sizes) == 150
    assert len(plan.tier1.unscored_ids) == 50
    assert len(plan.kept) == 200


def test_token_count() -> None:
    """The counter uses the model tokenizer and matches the provider count."""
    model = Settings().FAST_LLM_MODEL
    counter = TokenCounter(model)

    assert counter.encoding_name == "o200k_base"

    provider_count = len(
        tiktoken.encoding_for_model(model).encode(FIXTURE_TEXT, disallowed_special=())
    )
    counted = counter.count(FIXTURE_TEXT)

    assert abs(counted - provider_count) <= 1
    assert counted == PROVIDER_REFERENCE_TOKENS
    assert counted != len(FIXTURE_TEXT) // 4

    service = CompactionService(session=None, settings=Settings(), token_model=model)
    assert service.token_counter.count(FIXTURE_TEXT) == counted


@pytest.mark.asyncio
async def test_plan_tokens_match_tokenizer() -> None:
    """Plan token totals come from the tokenizer, not from a character estimate."""
    service, _ = service_with({"needed": 0.1, "user_fact": 0.05, "bulk": 0.9})

    plan = await service.plan_compaction(history_with(bulk_unit()))

    counter = service.token_counter
    assert plan.original_tokens == counter.count_many([unit.content for unit in history_with(bulk_unit())])
    assert plan.original_tokens > counter.count(BULK_LOG)
    assert plan.compacted_tokens == plan.original_tokens - counter.count(BULK_LOG) + counter.count(
        plan.stubbed[0].content
    )


@pytest.mark.asyncio
async def test_retain_at_needed_threshold() -> None:
    """p(needed) at the 0.7 threshold keeps the unit in full."""
    service, _ = service_with({"needed": 0.7, "user_fact": 0.0, "bulk": 0.9})

    plan = await service.plan_compaction(history_with(bulk_unit()))

    kept = {item.unit.unit_id: item for item in plan.kept}
    assert kept["tool-bulk"].verdict is Verdict.RETAIN
    assert kept["tool-bulk"].content == BULK_LOG
    assert plan.units_stubbed == 0


@pytest.mark.asyncio
async def test_retain_on_user_fact() -> None:
    """p(user_fact) at 0.7 keeps the unit even when p(needed) is low."""
    service, _ = service_with({"needed": 0.2, "user_fact": 0.7, "bulk": 0.7})

    plan = await service.plan_compaction(history_with(bulk_unit()))

    kept = {item.unit.unit_id: item for item in plan.kept}
    assert kept["tool-bulk"].verdict is Verdict.RETAIN
    assert plan.units_stubbed == 0


@pytest.mark.asyncio
async def test_mid_probability_retained() -> None:
    """A unit between the drop and retain bands stays."""
    service, _ = service_with({"needed": 0.4, "user_fact": 0.1, "bulk": 0.1})

    plan = await service.plan_compaction(history_with(bulk_unit()))

    kept = {item.unit.unit_id: item for item in plan.kept}
    assert kept["tool-bulk"].verdict is Verdict.RETAIN
    assert DROP_BELOW_THRESHOLD == 0.3


@pytest.mark.asyncio
async def test_unscored_units_retained_without_jev() -> None:
    """With no Jev client Tier 1 cannot act, so every unit stays."""
    units = history_with(bulk_unit())
    service, _ = service_with(None)

    plan = await service.plan_compaction(units)

    assert plan.tier1.jev_calls == 0
    assert len(plan.tier1.unscored_ids) == len(units)
    assert len(plan.kept) == len(units)
    assert plan.compacted_tokens == plan.original_tokens
    assert plan.summary_block == ""


@pytest.mark.asyncio
async def test_missing_needed_probability_retains() -> None:
    """A classifier answer without p(needed) keeps the unit."""
    service, _ = service_with({"bulk": 0.99})

    plan = await service.plan_compaction(history_with(bulk_unit()))

    kept = {item.unit.unit_id: item for item in plan.kept}
    assert kept["tool-bulk"].verdict is Verdict.RETAIN
    assert plan.units_stubbed == 0


def test_stub_requires_artifact_id() -> None:
    """A bulk unit without an artifact id cannot become a stub."""
    unit = HistoryUnit(
        unit_id="tool-no-artifact",
        role="tool",
        content=BULK_LOG,
        kind=UnitKind.TOOL_RESULT,
        tool_name="bash",
        command="pytest",
        exit_code=1,
    )

    with pytest.raises(ValueError):
        ArtifactStub.from_unit(unit)
