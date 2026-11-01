"""End-to-end compaction cycle: a long run keeps its approval.

The test scripts a long run with an approval at step 12. It compacts the run
history through the service that the composition root builds, then checks that
the approval is still in the compacted context, that the summary is present, and
that the run completes after the approval decision.

The Fast tier is replaced with a deterministic double, so the test needs no
network. The composition root wiring is asserted before the double is set.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest

from src.config.settings import Settings
from src.dependencies import get_compaction_service
from src.integrations.repositories import JevEvaluationResultDTO, LLMResponseDTO
from src.modules.approvals.repository import ApprovalCreateDTO
from src.modules.approvals.service import ApprovalsService
from src.modules.compaction.service import COMPACTION_TRIGGER_RATIO
from src.modules.runs.repository import RunCreateDTO, RunStepCreateDTO
from src.modules.runs.service import RunsService

# The approval sits in the middle of the run, outside the last six turns.
APPROVAL_STEP = 12
TOTAL_STEPS = 80
# A small window, so a run of this length passes the compaction trigger.
CONTEXT_WINDOW_TOKENS = 2000

SUMMARY_ANSWER = (
    "What happened:\n"
    "The run read the deploy log and checked the migration state.\n"
    "Decisions:\n"
    "The team agreed to hold the release until the check passes.\n"
    "Open items:\n"
    "The approval at step 12 is still pending."
)


class ScriptedFastLLM:
    """Deterministic Fast tier double. It records the tier of every call."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        tier: str = "reasoning",
        max_tokens: int | None = None,
    ) -> LLMResponseDTO:
        self.calls.append({"messages": messages, "tier": tier, "max_tokens": max_tokens})
        return LLMResponseDTO(
            content=SUMMARY_ANSWER,
            tool_calls=[],
            prompt_tokens=120,
            completion_tokens=40,
            total_tokens=160,
            finish_reason="stop",
            model="scripted-fast",
        )

    async def stream_complete(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Tier 2 must not stream.")

    async def embed_texts(
        self,
        texts: list[str],
        model: str | None = None,
    ) -> list[list[float]]:
        return [[0.0] * 384 for _ in texts]

    async def is_healthy(self) -> bool:
        return True


class ScriptedJev:
    """Deterministic Tier 1 double. It drops the old assistant turns.

    A tool result is bulk output, and the assistant turns that are not locked
    carry no needed fact. The user, approval, and decision units score high, so
    only the safety lock, not the classifier, keeps them.
    """

    async def evaluate(
        self,
        state: dict[str, Any],
        questions: dict[str, str],
        model: str | None = None,
    ) -> JevEvaluationResultDTO:
        return (await self.evaluate_batch([state], questions, model))[0]

    async def evaluate_batch(
        self,
        states: list[dict[str, Any]],
        questions: dict[str, str],
        model: str | None = None,
    ) -> list[JevEvaluationResultDTO]:
        results: list[JevEvaluationResultDTO] = []
        for state in states:
            kind = str(state.get("kind") or "")
            if kind == "tool_result":
                probabilities = {"needed": 0.1, "user_fact": 0.05, "bulk": 0.95}
            elif kind == "assistant":
                probabilities = {"needed": 0.1, "user_fact": 0.05, "bulk": 0.1}
            else:
                probabilities = {"needed": 0.95, "user_fact": 0.95, "bulk": 0.05}
            results.append(
                JevEvaluationResultDTO(
                    answers={},
                    probabilities=probabilities,
                    model_version="scripted-jev",
                )
            )
        return results

    async def is_healthy(self) -> bool:
        return True


def _scripted_history(approval_id: UUID) -> list[dict[str, Any]]:
    """Build one long run history. The approval is the unit at step 12."""
    history: list[dict[str, Any]] = []
    for step in range(1, TOTAL_STEPS + 1):
        if step == APPROVAL_STEP:
            history.append(
                {
                    "id": str(approval_id),
                    "role": "approval",
                    "kind": "approval_required",
                    "content": "Approval required: send the release note by email.",
                    "approval_id": str(approval_id),
                    "status": "pending",
                    "step": step,
                }
            )
        elif step % 5 == 1:
            history.append(
                {
                    "id": f"step-{step:02d}",
                    "role": "user",
                    "content": f"Step {step}: continue the release check.",
                    "step": step,
                }
            )
        elif step % 5 == 3:
            history.append(
                {
                    "id": f"step-{step:02d}",
                    "role": "tool",
                    "content": "log line " * 120,
                    "tool_name": "shell",
                    "tool_call_id": f"call-{step:02d}",
                    "artifact_id": f"artifact-{step:02d}",
                    "command": "make check",
                    "exit_code": 0,
                    "step": step,
                }
            )
        else:
            history.append(
                {
                    "id": f"step-{step:02d}",
                    "role": "assistant",
                    "content": f"Step {step}: the check is in progress. " + "detail " * 60,
                    "step": step,
                }
            )
    return history


@pytest.mark.asyncio
async def test_approval_survives_compaction() -> None:
    """The approval at step 12 stays in context, and the run still completes."""
    settings = Settings()
    approval_service = ApprovalsService(session=None, settings=settings)
    run_service = RunsService(session=None, settings=settings)

    run = await run_service.create_run(RunCreateDTO(config_id=uuid4(), status="running"))
    approval = await approval_service.create_approval(
        ApprovalCreateDTO(
            run_id=run.id,
            tool_call_id="call-12",
            tool_name="send_email",
            arguments={"to": "ops@example.com", "subject": "Release note"},
        )
    )
    history = _scripted_history(approval.id)
    assert history[APPROVAL_STEP - 1]["step"] == APPROVAL_STEP

    for message in history:
        await run_service.save_run_step(
            RunStepCreateDTO(
                run_id=run.id,
                step_number=int(message["step"]),
                event_type="approval_required" if message["role"] == "approval" else "thought",
                payload={"content": message["content"]},
            )
        )

    compaction = get_compaction_service(session=None, settings=settings)

    # The composition root must supply every tier client and the step recorder.
    assert compaction.jev_client is not None
    assert compaction.fast_llm is not None
    assert compaction.run_steps is not None

    # One journal holds both the run steps and the compaction trace. Without a
    # session each service builds its own in-memory journal, so share this one.
    compaction.run_steps = run_service

    scripted = ScriptedFastLLM()
    compaction.fast_llm = scripted
    compaction.jev_client = ScriptedJev()

    context = await compaction.compact(run.id, history)

    # The history is long enough to pass the trigger, and the run really compacted.
    assert context.original_tokens > CONTEXT_WINDOW_TOKENS * COMPACTION_TRIGGER_RATIO
    assert context.units_dropped > 0
    assert context.compacted_tokens < context.original_tokens

    # Tier 2 ran on the Fast tier and never saw the approval.
    assert scripted.calls
    assert all(call["tier"] == "fast" for call in scripted.calls)
    for call in scripted.calls:
        for message in call["messages"]:
            assert "Approval required" not in str(message.get("content") or "")

    # The summary block is present, and the post check passed.
    assert context.summary is not None
    assert context.summary_block.strip()
    assert context.verification.summary_present is True
    assert str(approval.id) in context.verification.approval_ids

    # The approval is still in the compacted context, with its identity and status.
    survivors = [
        message
        for message in context.messages
        if message.get("approval_id") == str(approval.id)
        or message.get("id") == str(approval.id)
    ]
    assert len(survivors) == 1
    assert survivors[0]["status"] == "pending"
    assert str(survivors[0]["approval_id"]) == str(approval.id)

    # The compaction trace is on the run.
    assert context.step is not None
    assert context.step.event_type == "compaction"
    assert context.step.payload["verified"] is True
    assert str(approval.id) in context.step.payload["approvals_checked"]

    # The approval row itself is unchanged.
    stored = await approval_service.get_approval_by_id(approval.id)
    assert stored is not None
    assert stored.status == "pending"

    # The run completes after the approval decision, and the trace stays.
    await approval_service.update_decision(
        approval.id,
        "approved",
        datetime.now(timezone.utc),
        decision_note="Release note approved.",
    )
    await run_service.save_run_step(
        RunStepCreateDTO(
            run_id=run.id,
            step_number=TOTAL_STEPS + 1,
            event_type="run_completed",
            payload={"approval_id": str(approval.id)},
        )
    )
    finished = await run_service.update_run_status(run.id, "completed")

    assert finished.status == "completed"
    assert finished.ended_at is not None
    steps = await run_service.get_steps_since(run.id, 0)
    assert any(step.event_type == "compaction" for step in steps)
    assert steps[-1].event_type == "run_completed"
    assert steps[-1].payload["approval_id"] == str(approval.id)
