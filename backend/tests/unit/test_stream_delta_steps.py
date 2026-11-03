"""Unit tests for the step writes of the streaming infer stage.

A per-fragment step write is a journal commit and a run row lock per model
token, and it also rescans every step already written to number the next one.
These tests drive one loop iteration with a streaming model that emits many
small fragments and check the two properties that fix that cost: the deltas are
batched, and the reconstructed text is still complete and in order.

No database, no container and no provider key are involved.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

import pytest

from src.config.settings import Settings
from src.integrations.repositories import LLMStreamChunkDTO
from src.modules.runs.repository import RunCreateDTO
from src.modules.runs.service import RunsService
from src.modules.runs.state import InMemoryRunJournal
from tests.unit.loop_fakes import (
    FakeToolExecutor,
    RecordingObserver,
    ScriptedModel,
    agent_config,
)

FRAGMENT_COUNT = 200
"""Fragments in one streamed reply."""

FRAGMENT_SIZE = 3
"""Characters per fragment, so the whole reply spans several batches."""

MAX_DELTA_STEPS = 10
"""Upper bound on the ``message_delta`` steps for one streamed reply."""


class StreamingModel(ScriptedModel):
    """A model client that streams a fixed list of content fragments."""

    def __init__(self, fragments: Sequence[str]) -> None:
        super().__init__([])
        self.fragments = list(fragments)

    async def stream_complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        tier: str = "reasoning",
        max_tokens: int | None = None,
    ):
        """Yield one chunk per fragment, then a final usage chunk."""
        self.calls.append(
            {
                "messages": [dict(message) for message in messages],
                "tools": tools,
                "temperature": temperature,
                "tier": tier,
            }
        )
        for fragment in self.fragments:
            yield LLMStreamChunkDTO(
                delta_content=fragment,
                tool_call_chunks=None,
                done=False,
                finish_reason=None,
                usage=None,
            )
        yield LLMStreamChunkDTO(
            delta_content="",
            tool_call_chunks=None,
            done=True,
            finish_reason="stop",
            usage={"prompt_tokens": 12, "completion_tokens": 6},
        )


async def streamed_run(
    fragments: Sequence[str],
    *,
    journal: InMemoryRunJournal | None = None,
) -> tuple[UUID, InMemoryRunJournal]:
    """Run one loop iteration over a streaming model and return the run id."""
    model = StreamingModel(fragments)
    tools = FakeToolExecutor()
    journal = journal or InMemoryRunJournal()
    service = RunsService(
        session=None,
        settings=Settings(APP_ENV="test"),
        llm=model,
        tools=tools,
        journal=journal,
    )
    config = agent_config()
    await journal.save_config(config)
    run = await service.create_run(RunCreateDTO(config_id=config.id))
    finished = await service.process(run.id, observer=RecordingObserver())
    assert finished.status == "completed"
    return finished.id, journal


@pytest.mark.asyncio
async def test_streamed_deltas_are_batched_and_complete() -> None:
    """200 fragments become a few ordered steps that rebuild the whole reply."""
    fragments = [f"{index % 1000:03d}" for index in range(FRAGMENT_COUNT)]
    expected = "".join(fragments)
    assert len(expected) > 200

    run_id, journal = await streamed_run(fragments)

    steps = await journal.list_steps(run_id)
    deltas = [step for step in steps if step.event_type == "message_delta"]

    # Batched, but still fine grained enough for a live view.
    assert 1 <= len(deltas) <= MAX_DELTA_STEPS
    assert len(deltas) < FRAGMENT_COUNT

    # Every delta step carries real text, and the steps rebuild the reply.
    assert all(isinstance(step.payload.get("delta"), str) for step in deltas)
    assert all(step.payload["delta"] for step in deltas)
    assert "".join(step.payload["delta"] for step in deltas) == expected

    # Step numbers are strictly increasing and unique across every step.
    numbers = [step.step_number for step in steps]
    assert numbers == sorted(numbers)
    assert len(numbers) == len(set(numbers))
    assert numbers[0] >= 1

    # The assistant row holds the full reply, in order.
    messages = await journal.list_messages(run_id)
    assistant = [row for row in messages if row.role == "assistant"]
    assert len(assistant) == 1
    assert assistant[0].content == expected


@pytest.mark.asyncio
async def test_the_remainder_is_flushed_before_the_stage_ends() -> None:
    """A reply shorter than one batch is still written as one delta step."""
    fragments = ["a", "b", "c"]

    run_id, journal = await streamed_run(fragments)

    steps = await journal.list_steps(run_id)
    deltas = [step for step in steps if step.event_type == "message_delta"]
    assert len(deltas) == 1
    assert deltas[0].payload["delta"] == "abc"
