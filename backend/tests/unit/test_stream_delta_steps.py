"""Unit tests for the step writes of the streaming infer stage.

A per-fragment step write is a journal commit and a run row lock per model
token, and it also rescans every step already written to number the next one.
These tests drive one loop iteration with a streaming model that emits many
small fragments and check the properties that fix that cost: the deltas are
batched under a real time bound, a failed stream still flushes what it wrote,
and the reconstructed text is still complete and in order.

No database, no container and no provider key are involved.
"""
from __future__ import annotations

import asyncio
import time
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
    journal = journal if journal is not None else InMemoryRunJournal()
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


class NoRescanJournal(InMemoryRunJournal):
    """A journal that fails the test when the loop rescans the step rows."""

    def __init__(self) -> None:
        super().__init__()
        self.guarded = False

    async def list_steps(self, run_id: UUID):
        """Refuse a read while the loop is running."""
        if self.guarded:
            raise AssertionError(
                "the loop reloaded every step row of the run to number the next one"
            )
        return await super().list_steps(run_id)


@pytest.mark.asyncio
async def test_recording_a_step_does_not_rescan_the_run() -> None:
    """The loop never calls ``list_steps`` while it records steps."""
    journal = NoRescanJournal()
    journal.guarded = True
    fragments = [f"{index % 1000:03d}" for index in range(FRAGMENT_COUNT)]

    run_id, journal = await streamed_run(fragments, journal=journal)

    journal.guarded = False
    steps = await journal.list_steps(run_id)
    assert [step.event_type for step in steps].count("message_delta") >= 1
    numbers = [step.step_number for step in steps]
    assert numbers == sorted(numbers)
    assert len(numbers) == len(set(numbers))


# ---------------------------------------------------------------------------
# The time bound and the failure path
# ---------------------------------------------------------------------------

PAUSE_SECONDS = 0.6
"""A stall longer than the flush window, but short enough for a unit test."""

FIRST_GROUP_CHARS = 150
"""Characters in the first group, below the 200 character threshold."""


def content_chunk(text: str) -> LLMStreamChunkDTO:
    """Return one content-only stream chunk."""
    return LLMStreamChunkDTO(
        delta_content=text,
        tool_call_chunks=None,
        done=False,
        finish_reason=None,
        usage=None,
    )


def final_chunk() -> LLMStreamChunkDTO:
    """Return the closing stream chunk with the usage counters."""
    return LLMStreamChunkDTO(
        delta_content="",
        tool_call_chunks=None,
        done=True,
        finish_reason="stop",
        usage={"prompt_tokens": 12, "completion_tokens": 6},
    )


class PausingModel(StreamingModel):
    """A model that sends one short group, stalls, then sends the rest.

    Before it emits the second group it reads the journal, so the test can prove
    that the first group was already a step while the provider was still silent.
    """

    def __init__(self, first: str, second: str, journal: InMemoryRunJournal) -> None:
        super().__init__([first, second])
        self.first = first
        self.second = second
        self.journal = journal
        self.run_id: UUID | None = None
        self.deltas_at_resume: list[str] | None = None
        self.pause_elapsed = 0.0

    async def stream_complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        tier: str = "reasoning",
        max_tokens: int | None = None,
    ):
        """Yield the first group, stall, then yield the second group."""
        self.calls.append(
            {
                "messages": [dict(message) for message in messages],
                "tools": tools,
                "temperature": temperature,
                "tier": tier,
            }
        )
        yield content_chunk(self.first)
        started = time.monotonic()
        await asyncio.sleep(PAUSE_SECONDS)
        self.pause_elapsed = time.monotonic() - started
        assert self.run_id is not None
        steps = await self.journal.list_steps(self.run_id)
        self.deltas_at_resume = [
            str(step.payload.get("delta"))
            for step in steps
            if step.event_type == "message_delta"
        ]
        yield content_chunk(self.second)
        yield final_chunk()


@pytest.mark.asyncio
async def test_a_provider_pause_flushes_the_open_batch_on_time() -> None:
    """150 characters then a 0.6 s silence still becomes a step inside 0.6 s.

    The character threshold cannot fire on 150 characters, so the only thing
    that can write them before the second group is the time bound. The model
    reads the journal after the pause, which is before it emits the second
    group, so the assertion cannot be satisfied by the end-of-stream flush.
    """
    first = "a" * FIRST_GROUP_CHARS
    second = "b" * 50
    journal = InMemoryRunJournal()
    model = PausingModel(first, second, journal)
    service = RunsService(
        session=None,
        settings=Settings(APP_ENV="test"),
        llm=model,
        tools=FakeToolExecutor(),
        journal=journal,
    )
    config = agent_config()
    await journal.save_config(config)
    run = await service.create_run(RunCreateDTO(config_id=config.id))
    model.run_id = run.id

    finished = await service.process(run.id, observer=RecordingObserver())

    assert finished.status == "completed"
    assert model.pause_elapsed >= PAUSE_SECONDS
    assert model.deltas_at_resume == [first], (
        "the first 150 characters were still buffered after "
        f"{PAUSE_SECONDS} s of provider silence"
    )

    steps = await journal.list_steps(run.id)
    deltas = [step for step in steps if step.event_type == "message_delta"]
    assert "".join(step.payload["delta"] for step in deltas) == first + second


class FailingModel(StreamingModel):
    """A model whose provider drops the stream after one group of text."""

    def __init__(self, first: str) -> None:
        super().__init__([first])
        self.first = first

    async def stream_complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        tier: str = "reasoning",
        max_tokens: int | None = None,
    ):
        """Yield one group, then raise the way a broken provider does."""
        self.calls.append(
            {
                "messages": [dict(message) for message in messages],
                "tools": tools,
                "temperature": temperature,
                "tier": tier,
            }
        )
        yield content_chunk(self.first)
        raise RuntimeError("provider dropped the stream")


@pytest.mark.asyncio
async def test_a_provider_failure_still_writes_the_prefix() -> None:
    """Text produced before the failure reaches the journal, and the error escapes."""
    first = "x" * 120
    journal = InMemoryRunJournal()
    model = FailingModel(first)
    service = RunsService(
        session=None,
        settings=Settings(APP_ENV="test"),
        llm=model,
        tools=FakeToolExecutor(),
        journal=journal,
    )
    config = agent_config()
    await journal.save_config(config)
    run = await service.create_run(RunCreateDTO(config_id=config.id))

    with pytest.raises(RuntimeError, match="provider dropped the stream"):
        await service.process(run.id, observer=RecordingObserver())

    steps = await journal.list_steps(run.id)
    deltas = [
        step.payload["delta"]
        for step in steps
        if step.event_type == "message_delta"
    ]
    assert deltas == [first]
    numbers = [step.step_number for step in steps]
    assert numbers == sorted(numbers)
    assert len(numbers) == len(set(numbers))

