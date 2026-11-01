"""Tests for the post-compaction check and its wiring in the service."""
from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

import src.modules.compaction.service as service_module
from src.config.settings import Settings
from src.integrations.repositories import JevEvaluationResultDTO, LLMResponseDTO
from src.modules.compaction.service import (
    SUMMARY_MESSAGE_ID,
    CompactionService,
)
from src.modules.compaction.verify import (
    CompactionVerificationError,
    approval_status,
    message_kind,
    unresolved_approval_ids,
    verify_compacted_history,
)

CANNED_ANSWER = (
    "What happened:\n"
    "The agent applied the migration.\n"
    "Decisions:\n"
    "Continue with the deploy.\n"
    "Open items:\n"
    "The backup is still open.\n"
)

BULK_LOG = "\n".join(
    f"step {index:03d}: applied change set and verified the checksum" for index in range(200)
)

DROP_ALL = {"needed": 0.05, "user_fact": 0.05, "bulk": 0.1}


class ScriptedFastLLM:
    """Tier 2 test double that returns one fixed answer."""

    def __init__(self, answer: str = CANNED_ANSWER) -> None:
        self.answer = answer
        self.calls = 0

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        tier: str = "reasoning",
        max_tokens: int | None = None,
    ) -> LLMResponseDTO:
        self.calls += 1
        return LLMResponseDTO(content=self.answer, model="gpt-4o-mini")


class ScriptedJev:
    """Tier 1 test double with one fixed probability set for all units."""

    def __init__(self, probabilities: dict[str, float]) -> None:
        self.probabilities = probabilities

    async def evaluate_batch(
        self,
        states: list[dict[str, Any]],
        questions: dict[str, str],
        model: str | None = None,
    ) -> list[JevEvaluationResultDTO]:
        return [
            JevEvaluationResultDTO(
                answers={key: "no" for key in questions},
                probabilities=dict(self.probabilities),
            )
            for _ in states
        ]

    async def is_healthy(self) -> bool:
        return True


def source_messages() -> list[dict[str, Any]]:
    """A legal source history with one approval and one satisfied tool call."""
    return [
        {"id": "u-1", "role": "user", "content": "Deploy the migration."},
        {
            "id": "a-1",
            "role": "assistant",
            "content": "I will run the migration.",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "bash", "arguments": "{}"},
                }
            ],
        },
        {
            "id": "t-1",
            "role": "tool",
            "content": "migration applied",
            "tool_call_id": "call-1",
            "kind": "tool_result",
        },
        {
            "id": "ap-1",
            "role": "approval",
            "content": "Approve the migration.",
            "kind": "approval",
            "status": "pending",
        },
    ]


def compacted_copy(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a legal compacted copy of a history."""
    return [dict(message) for message in messages]


def history_with_last_turn_tool_chain() -> list[dict[str, Any]]:
    """A history with an old bulk turn, an approval, and a live tool chain."""
    messages: list[dict[str, Any]] = [
        {"id": "u-0", "role": "user", "content": "Start the migration task."},
        {"id": "bulk-0", "role": "assistant", "content": BULK_LOG},
        {
            "id": "ap-1",
            "role": "approval",
            "content": "Approve the migration of the primary database.",
            "kind": "approval",
            "status": "pending",
        },
    ]
    for turn in range(1, 7):
        messages.append({"id": f"u-{turn}", "role": "user", "content": f"Follow up {turn}."})
        messages.append({"id": f"a-{turn}", "role": "assistant", "content": f"Answer {turn}."})
    messages.append({"id": "u-7", "role": "user", "content": "Run the checksum."})
    messages.append(
        {
            "id": "a-7",
            "role": "assistant",
            "content": "Running the checksum.",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "bash", "arguments": "{}"},
                }
            ],
        }
    )
    messages.append(
        {
            "id": "t-7",
            "role": "tool",
            "content": "checksum ok",
            "tool_call_id": "call-1",
            "kind": "tool_result",
        }
    )
    return messages


def service_with(run_steps: Any = None) -> tuple[CompactionService, ScriptedFastLLM]:
    """Build a compaction service with scripted Tier 1 and Tier 2."""
    settings = Settings()
    llm = ScriptedFastLLM()
    service = CompactionService(
        session=None,
        settings=settings,
        jev_client=ScriptedJev(DROP_ALL),
        fast_llm=llm,
        token_model=settings.REASONING_LLM_MODEL,
        run_steps=run_steps,
    )
    return service, llm


# =============================================================================
# Criterion 3: the check passes on a legal history
# =============================================================================

def test_verify_ok() -> None:
    """A history with all approvals and all tool results passes."""
    original = source_messages()

    report = verify_compacted_history(compacted_copy(original), original)

    assert report.approval_ids == ("ap-1",)
    assert report.tool_call_ids == ("call-1",)
    assert report.message_count == 4
    assert report.summary_present is False


def test_verify_ok_with_summary_and_stub() -> None:
    """A summary message and a stubbed tool result still pass."""
    original = source_messages()
    assembled = [
        {"id": SUMMARY_MESSAGE_ID, "role": "system", "content": "summary", "kind": "summary"},
        original[0],
        original[1],
        {
            "id": "t-1",
            "role": "tool",
            "content": "[Truncated Artifact #art-1: tool=bash, size=12B, exit_code=0]",
            "tool_call_id": "call-1",
            "kind": "tool_result",
        },
        original[3],
    ]

    report = verify_compacted_history(assembled, original)

    assert report.tool_call_ids == ("call-1",)
    assert report.summary_present is True


# =============================================================================
# Criterion 4: the check fails when an approval is gone
# =============================================================================

def test_verify_missing_approval() -> None:
    """A compacted history without one approval raises."""
    original = source_messages()
    assembled = [message for message in compacted_copy(original) if message["id"] != "ap-1"]
    assert len(assembled) == len(original) - 1

    with pytest.raises(CompactionVerificationError) as error:
        verify_compacted_history(assembled, original)

    assert error.value.missing_approval_ids == ("ap-1",)
    assert error.value.unsatisfied_tool_call_ids == ()
    assert "ap-1" in str(error.value)


def test_verify_missing_tool_result() -> None:
    """A tool call of the last assistant turn without a result raises."""
    original = source_messages()
    assembled = [message for message in compacted_copy(original) if message["id"] != "t-1"]

    with pytest.raises(CompactionVerificationError) as error:
        verify_compacted_history(assembled, original)

    assert error.value.unsatisfied_tool_call_ids == ("call-1",)
    assert error.value.missing_approval_ids == ()


def test_verify_reports_both_problems() -> None:
    """The check collects every problem before it raises."""
    original = source_messages()
    assembled = [
        message
        for message in compacted_copy(original)
        if message["id"] not in {"ap-1", "t-1"}
    ]

    with pytest.raises(CompactionVerificationError) as error:
        verify_compacted_history(assembled, original)

    assert error.value.missing_approval_ids == ("ap-1",)
    assert error.value.unsatisfied_tool_call_ids == ("call-1",)


# =============================================================================
# The rules of the check
# =============================================================================

def test_resolved_approval_may_leave() -> None:
    """A decided approval is not an unresolved approval."""
    original = source_messages()
    original[3]["status"] = "approved"
    assembled = [message for message in compacted_copy(original) if message["id"] != "ap-1"]

    report = verify_compacted_history(assembled, original)

    assert report.approval_ids == ()


def test_approval_without_status_counts_as_open() -> None:
    """The check treats a missing status as open, not as decided."""
    message = {"id": "ap-9", "role": "approval", "content": "Approve."}

    assert approval_status(message) == "pending"
    assert unresolved_approval_ids([message]) == ("ap-9",)


def test_only_the_last_assistant_turn_is_checked() -> None:
    """An old tool call without a result does not fail the last turn check."""
    original = [
        {"id": "u-1", "role": "user", "content": "Start."},
        {
            "id": "a-1",
            "role": "assistant",
            "content": "Old call.",
            "tool_calls": [{"id": "call-old", "type": "function", "function": {"name": "bash"}}],
        },
        {"id": "u-2", "role": "user", "content": "Stop."},
        {"id": "a-2", "role": "assistant", "content": "Stopped."},
    ]

    report = verify_compacted_history(compacted_copy(original), original)

    assert report.tool_call_ids == ()


def test_history_without_approvals_or_calls_passes() -> None:
    """A plain history needs no approval and no tool result."""
    original = [
        {"id": "u-1", "role": "user", "content": "Hello."},
        {"id": "a-1", "role": "assistant", "content": "Hi."},
    ]

    report = verify_compacted_history(compacted_copy(original), original)

    assert report.approval_ids == ()
    assert report.tool_call_ids == ()
    assert report.message_count == 2


def test_message_kind_reads_kind_then_role() -> None:
    """The kind field wins, and the role is the fallback."""
    assert message_kind({"role": "user", "kind": "approval"}) == "approval"
    assert message_kind({"role": "approval"}) == "approval"
    assert message_kind({"role": "tool"}) == "tool_result"
    assert message_kind({"role": "assistant", "type": "decision"}) == "decision"


# =============================================================================
# The service wiring
# =============================================================================

@pytest.mark.asyncio
async def test_compaction_keeps_approval_and_tool_chain() -> None:
    """A real compaction keeps the approval and the live tool chain."""
    service, _ = service_with()
    messages = history_with_last_turn_tool_chain()

    context = await service.compact(uuid4(), messages)

    ids = [message["id"] for message in context.messages]
    assert ids[0] == SUMMARY_MESSAGE_ID
    assert "ap-1" in ids
    assert "t-7" in ids
    assert "bulk-0" not in ids

    tool_message = next(message for message in context.messages if message["id"] == "t-7")
    assert tool_message["tool_call_id"] == "call-1"

    approval = next(message for message in context.messages if message["id"] == "ap-1")
    assert approval["status"] == "pending"
    assert approval["kind"] == "approval"

    assert context.verification.approval_ids == ("ap-1",)
    assert context.verification.tool_call_ids == ("call-1",)
    assert context.verification.summary_present is True
    assert context.summary is not None


@pytest.mark.asyncio
async def test_failed_verification_persists_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed check stops the run before any save or step."""
    saved: list[tuple[Any, ...]] = []

    def refuse(assembled, original):
        raise CompactionVerificationError("forced failure")

    monkeypatch.setattr(service_module, "verify_compacted_history", refuse)
    service, _ = service_with()
    run_id = uuid4()

    async def fake_save(run_id_arg, compacted_messages, summary_block):
        saved.append((run_id_arg, compacted_messages, summary_block))

    service.save_compacted_history = fake_save  # type: ignore[method-assign]

    with pytest.raises(CompactionVerificationError):
        await service.compact(run_id, history_with_last_turn_tool_chain())

    assert saved == []
    assert CompactionService._compacted_history.get(run_id) is None


@pytest.mark.asyncio
async def test_check_and_compact_skips_a_small_history() -> None:
    """A history inside the window is not compacted."""
    service, llm = service_with()

    result = await service.check_and_compact(
        uuid4(),
        100_000,
        messages=history_with_last_turn_tool_chain(),
    )

    assert result is None
    assert llm.calls == 0


@pytest.mark.asyncio
async def test_check_and_compact_compacts_a_large_history() -> None:
    """A history past 70 percent of the window is compacted."""
    service, _ = service_with()
    messages = history_with_last_turn_tool_chain()
    original = service.token_counter.count_many(
        [str(message.get("content") or "") for message in messages]
    )
    assert original > 1000

    result = await service.check_and_compact(uuid4(), 1200, messages=messages)

    assert result is not None
    assert result.original_tokens == original
    assert result.compacted_tokens < original
    assert result.summary_block.startswith("== Compaction Summary ==")
    assert result.units_dropped >= 1
