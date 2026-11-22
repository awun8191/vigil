"""Tests for JEV-free deterministic compaction, artifact stubs, and tokenizer counts."""
from __future__ import annotations

import pytest
import tiktoken

from src.config.settings import Settings
from src.modules.compaction.service import CompactionService
from src.modules.compaction.stub import (
    PREVIEW_CHARS,
    STUB_TAIL,
    ArtifactStub,
    build_preview,
    format_size,
)
from src.modules.compaction.tiers import (
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


def bulk_unit(unit_id: str = "tool-bulk", artifact_id: str | None = "art-e4b2a1") -> HistoryUnit:
    """Return one large tool log unit with optional stub provenance."""
    return HistoryUnit(
        unit_id=unit_id,
        role="tool",
        content=BULK_LOG,
        kind=UnitKind.TOOL_RESULT,
        tool_name="bash",
        command="pytest tests/ -q",
        exit_code=0,
        artifact_id=artifact_id,
        size_bytes=45000,
    )


def history_with(target: HistoryUnit, trailing_turns: int = 6) -> list[HistoryUnit]:
    """Put one unit in the oldest turn and add six later turns after it."""
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


@pytest.mark.asyncio
async def test_bulk_becomes_stub_without_jev() -> None:
    """An unlocked tool result with an artifact id becomes a stub with zero Jev calls."""
    service = CompactionService(session=None, settings=Settings(), token_model="gpt-4o-mini")

    plan = await service.plan_compaction(history_with(bulk_unit()))

    assert plan.tier1.jev_calls == 0
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
async def test_unlocked_tool_without_artifact_is_dropped() -> None:
    """An unlocked non-final tool unit without an artifact id is dropped deterministically."""
    service = CompactionService(session=None, settings=Settings(), token_model="gpt-4o-mini")

    plan = await service.plan_compaction(history_with(bulk_unit(artifact_id=None)))

    assert plan.tier1.jev_calls == 0
    assert [item.unit.unit_id for item in plan.dropped] == ["tool-bulk"]
    assert plan.dropped[0].verdict is Verdict.DROP
    assert plan.dropped[0].content == ""
    assert all(item.unit.unit_id != "tool-bulk" for item in plan.kept)
    assert all(item.unit.unit_id != "tool-bulk" for item in plan.stubbed)


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
    service = CompactionService(session=None, settings=Settings(), token_model="gpt-4o-mini")

    plan = await service.plan_compaction(history_with(bulk_unit()))

    counter = service.token_counter
    assert plan.original_tokens == counter.count_many([unit.content for unit in history_with(bulk_unit())])
    assert plan.original_tokens > counter.count(BULK_LOG)
    assert plan.compacted_tokens == plan.original_tokens - counter.count(BULK_LOG) + counter.count(
        plan.stubbed[0].content
    )


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
