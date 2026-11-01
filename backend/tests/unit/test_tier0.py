"""Tests for the Tier 0 deterministic safety lock.

Tier 0 must keep every user message, the last six turns, every approval and
decision, and the final result of every tool chain, whatever Tier 1 says.
"""
from __future__ import annotations

from typing import Any

import pytest

from src.config.settings import Settings
from src.integrations.repositories import JevEvaluationResultDTO
from src.modules.compaction.service import CompactionService
from src.modules.compaction.tiers import (
    RECENT_TURN_COUNT,
    HistoryUnit,
    UnitKind,
    Verdict,
    lock_tier0_units,
    recent_turn_ids,
    tool_chain_final_ids,
)


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


def build_history(turns: int = 9) -> list[HistoryUnit]:
    """Build a history with user messages, approvals, a decision, and tool chains."""
    units: list[HistoryUnit] = []
    for turn in range(1, turns + 1):
        units.append(
            HistoryUnit(
                unit_id=f"t{turn}-user",
                role="user",
                content=f"Task step {turn}: change module {turn}.",
                kind=UnitKind.USER,
            )
        )
        units.append(
            HistoryUnit(
                unit_id=f"t{turn}-assistant",
                role="assistant",
                content=f"Plan for step {turn}: read, patch, test.",
                kind=UnitKind.ASSISTANT,
            )
        )
        units.append(
            HistoryUnit(
                unit_id=f"t{turn}-tool-a",
                role="tool",
                content=f"pytest output for step {turn}. " * 40,
                kind=UnitKind.TOOL_RESULT,
                tool_name="bash",
                command="pytest tests/",
                exit_code=0,
                artifact_id=f"art-{turn}-a",
                size_bytes=1500,
            )
        )
        units.append(
            HistoryUnit(
                unit_id=f"t{turn}-tool-b",
                role="tool",
                content=f"final lint output for step {turn}. " * 20,
                kind=UnitKind.TOOL_RESULT,
                tool_name="bash",
                command="ruff check",
                exit_code=0,
                artifact_id=f"art-{turn}-b",
                size_bytes=900,
            )
        )
        if turn == 2:
            units.append(
                HistoryUnit(
                    unit_id="approval-1",
                    role="approval",
                    content="User approved the deploy to staging.",
                    kind=UnitKind.APPROVAL,
                )
            )
        if turn == 4:
            units.append(
                HistoryUnit(
                    unit_id="decision-1",
                    role="decision",
                    content="Decision: use Postgres row locks for the job queue.",
                    kind=UnitKind.DECISION,
                )
            )
    return units


@pytest.mark.parametrize(
    "probabilities",
    [
        {"needed": 0.0, "user_fact": 0.0, "bulk": 0.0},
        {"needed": 0.01, "user_fact": 0.02, "bulk": 0.99},
    ],
)
@pytest.mark.asyncio
async def test_tier0_never_drops(probabilities: dict[str, float]) -> None:
    """A hostile Tier 1 cannot remove a single Tier 0 unit."""
    units = build_history()
    jev = ScriptedJev(probabilities)
    service = CompactionService(
        session=None,
        settings=Settings(),
        jev_client=jev,
        token_model="gpt-4o-mini",
    )

    plan = await service.plan_compaction(units)

    locked = plan.tier0.locked_ids
    assert plan.tier0.user_ids == frozenset(u.unit_id for u in units if u.kind is UnitKind.USER)
    assert plan.tier0.approval_ids == frozenset({"approval-1"})
    assert plan.tier0.decision_ids == frozenset({"decision-1"})
    assert plan.tier0.tool_chain_final_ids == frozenset(
        f"t{turn}-tool-b" for turn in range(1, 10)
    )

    kept_ids = {item.unit.unit_id for item in plan.kept}
    stubbed_ids = {item.unit.unit_id for item in plan.stubbed}
    dropped_ids = {item.unit.unit_id for item in plan.dropped}

    assert locked <= kept_ids
    assert not locked & stubbed_ids
    assert not locked & dropped_ids

    for item in plan.kept:
        if item.unit.unit_id in locked:
            assert item.verdict is Verdict.RETAIN
            assert item.content == item.unit.content

    # The lock must not weaken Tier 1 on the unlocked units.
    assert plan.units_dropped + plan.units_stubbed > 0
    assert jev.batch_sizes, "Tier 1 must be asked before any unit leaves the context"


@pytest.mark.asyncio
async def test_tier0_keeps_locked_content_verbatim() -> None:
    """A locked unit keeps its full text even when p(bulk) is 0.99."""
    units = build_history()
    service = CompactionService(
        session=None,
        settings=Settings(),
        jev_client=ScriptedJev({"needed": 0.05, "user_fact": 0.05, "bulk": 0.99}),
        token_model="gpt-4o-mini",
    )

    plan = await service.plan_compaction(units)

    by_id = {item.unit.unit_id: item for item in plan.kept}
    for unit_id in ("approval-1", "decision-1", "t1-user", "t3-tool-b"):
        assert unit_id in by_id
        assert by_id[unit_id].content == next(u.content for u in units if u.unit_id == unit_id)
        assert "Truncated Artifact" not in by_id[unit_id].content


def test_recent_six_turns_are_locked() -> None:
    """Only the last six turns enter the lock through the recency rule."""
    units = build_history()
    recent = recent_turn_ids(units)

    for turn in range(4, 10):
        assert f"t{turn}-assistant" in recent
    for turn in range(1, 4):
        assert f"t{turn}-assistant" not in recent
    assert RECENT_TURN_COUNT == 6


def test_tool_chain_final_only_last_result() -> None:
    """Only the last tool result of a chain is locked as the chain result."""
    units = build_history()
    finals = tool_chain_final_ids(units)

    for turn in range(1, 10):
        assert f"t{turn}-tool-b" in finals
        assert f"t{turn}-tool-a" not in finals


def test_lock_set_is_the_union_of_the_rules() -> None:
    """The lock set is the union of users, approvals, decisions, finals, recent turns."""
    units = build_history()
    selection = lock_tier0_units(units)

    expected = (
        selection.user_ids
        | selection.approval_ids
        | selection.decision_ids
        | selection.tool_chain_final_ids
        | selection.recent_turn_ids
    )
    assert selection.locked_ids == expected
    assert selection.is_locked("t1-user") is True
    assert selection.is_locked("t1-tool-a") is False


def test_units_from_messages_maps_roles_and_locks() -> None:
    """Message dictionaries map to the right unit kinds for the lock."""
    from src.modules.compaction.tiers import units_from_messages

    units = units_from_messages(
        [
            {"role": "user", "content": "Add the compaction module.", "id": "m1"},
            {"role": "assistant", "content": "I will write tiers.py."},
            {
                "role": "tool",
                "content": "collected 12 items",
                "tool_name": "bash",
                "command": "pytest",
                "exit_code": 0,
                "artifact_id": "art-1",
            },
            {"role": "approval", "content": "approved", "kind": "approval"},
        ]
    )

    assert units[0].kind is UnitKind.USER
    assert units[0].unit_id == "m1"
    assert units[1].kind is UnitKind.ASSISTANT
    assert units[2].kind is UnitKind.TOOL_RESULT
    assert units[2].artifact_id == "art-1"
    assert units[2].exit_code == 0
    assert units[2].size_bytes == len("collected 12 items")
    assert units[3].kind is UnitKind.APPROVAL

    selection = lock_tier0_units(units)
    assert selection.is_locked("m1") is True
    assert selection.is_locked("unit-0003") is True
    assert selection.is_locked("unit-0001") is True
