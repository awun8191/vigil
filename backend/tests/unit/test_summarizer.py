"""Tests for the Tier 2 summarizer, the assembly, the trace, and the reduction."""
from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from src.config.settings import Settings
from src.integrations.repositories import JevEvaluationResultDTO, LLMResponseDTO
from src.modules.compaction.service import (
    COMPACTION_STEP_TYPE,
    SUMMARY_MESSAGE_ID,
    CompactionService,
)
from src.modules.compaction.summarizer import (
    EMPTY_SECTION_TEXT,
    HEADING_DECISIONS,
    HEADING_OPEN_ITEMS,
    HEADING_WHAT_HAPPENED,
    MAX_PROMPT_CHARS,
    SOURCE_CHARS_LIMIT,
    SUMMARY_TIER,
    SUMMARY_TITLE,
    TRUNCATION_MARK,
    CompactionSummarizer,
    SummarizerError,
    SummaryInputError,
    SummarySource,
    build_prompt,
    parse_sections,
    truncate_to_tokens,
)
from src.modules.compaction.tiers import TokenCounter, UnitKind
from src.modules.runs.service import RunsService

CANNED_ANSWER = (
    "What happened:\n"
    "The agent ran the test suite three times and read the failure log.\n"
    "Decisions:\n"
    "Ship the fix on Friday after the migration review.\n"
    "Open items:\n"
    "The database migration is still open.\n"
)

LONG_ANSWER = (
    "What happened:\n"
    + ("The agent read a long build log and retried the job. " * 120)
    + "\nDecisions:\n"
    + ("Keep the retry loop and the current timeout. " * 120)
    + "\nOpen items:\n"
    + ("Fix the database migration before the release. " * 120)
)

BULK_LOG = "\n".join(
    f"tests/unit/test_case_{index:03d}.py PASSED                              [ {index}%]"
    for index in range(1, 121)
)

BULK_LINE = (
    "2026-10-31T12:00:00Z INFO worker-07 batch=000123 item=000456 "
    "status=ok elapsed=12ms\n"
)

DROP_ALL = {"needed": 0.05, "user_fact": 0.05, "bulk": 0.1}


class ScriptedFastLLM:
    """Tier 2 test double. It records each call and returns one fixed text."""

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        tier: str = "reasoning",
        max_tokens: int | None = None,
    ) -> LLMResponseDTO:
        self.calls.append(
            {
                "messages": messages,
                "tools": tools,
                "temperature": temperature,
                "tier": tier,
                "max_tokens": max_tokens,
            }
        )
        return LLMResponseDTO(content=self.answer, model="gpt-4o-mini")


class ScriptedJev:
    """Tier 1 test double that returns one fixed probability set for all units."""

    def __init__(self, probabilities: dict[str, float]) -> None:
        self.probabilities = probabilities
        self.batch_sizes: list[int] = []

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


def removed_sources() -> list[SummarySource]:
    """One dropped tool log and one stubbed assistant draft."""
    return [
        SummarySource(
            unit_id="turn-1-bulk",
            kind=UnitKind.TOOL_RESULT,
            role="tool",
            content=BULK_LOG,
            tokens=1200,
            verdict="drop",
        ),
        SummarySource(
            unit_id="turn-2-bulk",
            kind=UnitKind.ASSISTANT,
            role="assistant",
            content="Intermediate draft of the plan. " * 40,
            tokens=300,
            verdict="stub",
        ),
    ]


def small_history() -> list[dict[str, Any]]:
    """A history with one old bulk turn and six later turns.

    The bulk turn sits outside the last six turns, so Tier 1 can drop it.
    """
    messages: list[dict[str, Any]] = [
        {"id": "u-0", "role": "user", "content": "Start the migration task."},
        {"id": "bulk-0", "role": "assistant", "content": BULK_LOG},
    ]
    for turn in range(1, 8):
        messages.append({"id": f"u-{turn}", "role": "user", "content": f"Follow up {turn}."})
        messages.append({"id": f"a-{turn}", "role": "assistant", "content": f"Answer {turn}."})
    return messages


def service_with(
    answer: str = CANNED_ANSWER,
    probabilities: dict[str, float] | None = DROP_ALL,
    run_steps: RunsService | None = None,
) -> tuple[CompactionService, ScriptedFastLLM]:
    """Build a compaction service with a scripted Tier 1 and Tier 2."""
    settings = Settings()
    llm = ScriptedFastLLM(answer)
    service = CompactionService(
        session=None,
        settings=settings,
        jev_client=None if probabilities is None else ScriptedJev(probabilities),
        fast_llm=llm,
        token_model=settings.REASONING_LLM_MODEL,
        run_steps=run_steps,
    )
    return service, llm


# =============================================================================
# Criterion 2: the summary shape
# =============================================================================

@pytest.mark.asyncio
async def test_summary_shape() -> None:
    """The block carries decisions and open items and fits the token budget."""
    settings = Settings()
    llm = ScriptedFastLLM(CANNED_ANSWER)
    budget = 300
    summarizer = CompactionSummarizer(
        llm=llm,
        model=settings.FAST_LLM_MODEL,
        max_tokens=budget,
    )

    block = await summarizer.summarize(removed_sources())

    assert block is not None
    assert block.fits_budget is True
    assert block.tokens <= budget
    assert block.budget_tokens == budget
    assert block.truncated is False

    headings = [section.heading for section in block.sections]
    assert headings == [HEADING_WHAT_HAPPENED, HEADING_DECISIONS, HEADING_OPEN_ITEMS]
    assert block.text.startswith(SUMMARY_TITLE)
    assert "Decisions:" in block.text
    assert "Open items:" in block.text

    assert "three times" in block.happened
    assert "Ship the fix on Friday" in block.decisions
    assert "database migration is still open" in block.open_items

    assert block.source_unit_ids == ("turn-1-bulk", "turn-2-bulk")
    assert block.source_tokens == 1500

    # Tier 2 is the Fast tier from settings, at a deterministic temperature.
    assert block.model == settings.FAST_LLM_MODEL
    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert call["tier"] == SUMMARY_TIER == "fast"
    assert call["max_tokens"] == budget
    assert call["temperature"] == 0.0

    # The prompt carries the original text of both removed units, not a stub.
    prompt = call["messages"][-1]["content"]
    assert "turn-1-bulk" in prompt
    assert "turn-2-bulk" in prompt
    assert "[drop]" in prompt
    assert "[stub]" in prompt
    assert BULK_LOG.splitlines()[0] in prompt


@pytest.mark.asyncio
async def test_summary_trims_to_budget() -> None:
    """A long answer is trimmed, and the block still fits its budget."""
    llm = ScriptedFastLLM(LONG_ANSWER)
    budget = 150
    summarizer = CompactionSummarizer(llm=llm, model=Settings().FAST_LLM_MODEL, max_tokens=budget)

    block = await summarizer.summarize(removed_sources())

    assert block is not None
    assert block.fits_budget is True
    assert block.tokens <= budget
    assert block.truncated is True
    assert len(block.text) < len(LONG_ANSWER)
    assert TRUNCATION_MARK in block.text
    assert [section.heading for section in block.sections] == [
        HEADING_WHAT_HAPPENED,
        HEADING_DECISIONS,
        HEADING_OPEN_ITEMS,
    ]


@pytest.mark.asyncio
async def test_missing_section_gets_empty_text() -> None:
    """A model answer with one section still produces all three headings."""
    llm = ScriptedFastLLM("What happened:\nThe agent read the log.\n")
    summarizer = CompactionSummarizer(llm=llm, model=Settings().FAST_LLM_MODEL)

    block = await summarizer.summarize(removed_sources())

    assert block is not None
    assert block.happened == "The agent read the log."
    assert block.decisions == EMPTY_SECTION_TEXT
    assert block.open_items == EMPTY_SECTION_TEXT


def test_parse_sections_accepts_markdown() -> None:
    """Markdown marks around a heading do not hide the section."""
    sections = parse_sections(
        "## What happened\nA.\n**Decisions**\nB.\n### Open items\nC.\n"
    )

    assert [section.body for section in sections] == ["A.", "B.", "C."]


@pytest.mark.asyncio
async def test_summarizer_rejects_user_message() -> None:
    """Tier 2 never summarizes a user message."""
    llm = ScriptedFastLLM(CANNED_ANSWER)
    summarizer = CompactionSummarizer(llm=llm, model=Settings().FAST_LLM_MODEL)
    source = SummarySource(
        unit_id="u-1",
        kind=UnitKind.USER,
        role="user",
        content="Remember that the key is in the vault.",
        tokens=12,
        verdict="drop",
    )

    with pytest.raises(SummaryInputError):
        await summarizer.summarize([source])

    assert llm.calls == []


@pytest.mark.asyncio
async def test_summarizer_rejects_approval() -> None:
    """Tier 2 never summarizes an approval record."""
    summarizer = CompactionSummarizer(
        llm=ScriptedFastLLM(CANNED_ANSWER), model=Settings().FAST_LLM_MODEL
    )
    source = SummarySource(
        unit_id="ap-1",
        kind=UnitKind.APPROVAL,
        role="approval",
        content="Approve the migration.",
        tokens=8,
        verdict="drop",
    )

    with pytest.raises(SummaryInputError):
        await summarizer.summarize([source])


@pytest.mark.asyncio
async def test_no_sources_no_call() -> None:
    """An empty input costs no model call and returns no block."""
    llm = ScriptedFastLLM(CANNED_ANSWER)
    summarizer = CompactionSummarizer(llm=llm, model=Settings().FAST_LLM_MODEL)

    assert await summarizer.summarize([]) is None
    assert llm.calls == []


@pytest.mark.asyncio
async def test_fast_tier_is_required() -> None:
    """Without the Fast tier client Tier 2 fails loudly."""
    summarizer = CompactionSummarizer(llm=None, model=Settings().FAST_LLM_MODEL)

    with pytest.raises(SummarizerError):
        await summarizer.summarize(removed_sources())


@pytest.mark.asyncio
async def test_budget_below_headings_is_rejected() -> None:
    """A budget that cannot hold the three headings fails loudly."""
    summarizer = CompactionSummarizer(
        llm=ScriptedFastLLM(CANNED_ANSWER), model=Settings().FAST_LLM_MODEL, max_tokens=4
    )

    with pytest.raises(SummarizerError):
        await summarizer.summarize(removed_sources())


def test_truncate_to_tokens_respects_the_budget() -> None:
    """The truncation returns the longest prefix that fits."""
    counter = TokenCounter(Settings().FAST_LLM_MODEL)
    text = "word " * 5000

    result = truncate_to_tokens(text, counter, 40)

    assert counter.count(result) <= 40
    assert result.endswith(TRUNCATION_MARK)
    assert text.startswith(result[: -len(TRUNCATION_MARK)].rstrip())


def test_prompt_is_bounded_per_source() -> None:
    """One huge source cannot make the Tier 2 prompt unbounded."""
    source = SummarySource(
        unit_id="bulk-x",
        kind=UnitKind.TOOL_RESULT,
        role="tool",
        content="L" * (SOURCE_CHARS_LIMIT * 5),
        tokens=25000,
        verdict="drop",
    )

    prompt = build_prompt([source])

    assert "characters omitted" in prompt
    assert len(prompt) <= SOURCE_CHARS_LIMIT + 200


def test_prompt_is_bounded_in_total() -> None:
    """Many large sources keep the prompt inside the total limit, newest first."""
    sources = [
        SummarySource(
            unit_id=f"bulk-{index:02d}",
            kind=UnitKind.TOOL_RESULT,
            role="tool",
            content=str(index) * SOURCE_CHARS_LIMIT,
            tokens=1000,
            verdict="drop",
        )
        for index in range(20)
    ]

    prompt = build_prompt(sources)

    assert len(prompt) <= MAX_PROMPT_CHARS + 400
    assert "earlier removed units omitted from this prompt" in prompt
    assert "bulk-19" in prompt
    assert "bulk-00" not in prompt


# =============================================================================
# Criterion 5: the summary is traced in run_steps
# =============================================================================

@pytest.mark.asyncio
async def test_summary_traced() -> None:
    """The summary chain is one run_steps row with type compaction."""
    settings = Settings()
    runs = RunsService(session=None, settings=settings)
    run_id = uuid4()
    service, _ = service_with(run_steps=runs)

    context = await service.compact(run_id, small_history())

    steps = await runs.get_steps_since(run_id, 0)
    assert len(steps) == 1
    step = steps[0]
    assert step.run_id == run_id
    assert step.step_number == 1
    assert step.event_type == COMPACTION_STEP_TYPE == "compaction"

    summary = context.summary
    assert summary is not None
    payload = step.payload
    assert payload["summary_block"] == context.summary_block == summary.text
    assert payload["summary_tokens"] == summary.tokens > 0
    assert payload["summary_model"] == settings.FAST_LLM_MODEL
    assert payload["summary_truncated"] is False
    assert payload["verified"] is True
    assert payload["original_tokens"] == context.original_tokens
    assert payload["compacted_tokens"] == context.compacted_tokens
    assert payload["units_dropped"] == context.plan.units_dropped

    dropped_ids = {item.unit.unit_id for item in context.plan.dropped}
    traced_ids = {entry["unit_id"] for entry in payload["summary_sources"]}
    assert "bulk-0" in dropped_ids
    assert dropped_ids <= traced_ids

    assert CompactionService._compacted_history[run_id][1] == summary.text


@pytest.mark.asyncio
async def test_second_compaction_gets_next_step_number() -> None:
    """A second compaction on the same run does not reuse a step number."""
    runs = RunsService(session=None, settings=Settings())
    run_id = uuid4()
    service, _ = service_with(run_steps=runs)

    await service.compact(run_id, small_history())
    await service.compact(run_id, small_history())

    steps = await runs.get_steps_since(run_id, 0)
    assert [step.step_number for step in steps] == [1, 2]


@pytest.mark.asyncio
async def test_no_tier2_call_when_nothing_removed() -> None:
    """A history that needs no removal keeps the empty summary contract."""
    service, llm = service_with(probabilities=None)

    context = await service.compact(uuid4(), small_history())

    assert llm.calls == []
    assert context.summary is None
    assert context.summary_block == ""
    assert context.plan.summary_block == ""
    assert [message["content"] for message in context.messages] == [
        message["content"] for message in small_history()
    ]


@pytest.mark.asyncio
async def test_assembly_keeps_source_order() -> None:
    """The assembly keeps the order of the source history after the summary."""
    service, _ = service_with()
    messages = small_history()

    context = await service.compact(uuid4(), messages)

    ids = [message["id"] for message in context.messages]
    assert ids[0] == SUMMARY_MESSAGE_ID
    source_ids = [message["id"] for message in messages]
    kept_ids = [unit_id for unit_id in source_ids if unit_id in set(ids)]
    assert ids[1:] == kept_ids
    assert "bulk-0" not in ids


# =============================================================================
# Criterion 6: token reduction on a 40k token fixture
# =============================================================================

def bulk_text(counter: TokenCounter, target_tokens: int) -> str:
    """Return a deterministic log line repeated to about `target_tokens`."""
    per_line = counter.count(BULK_LINE)
    repeats = max(1, target_tokens // per_line + 1)
    return BULK_LINE * repeats


def large_history(pairs: int = 30, target_tokens: int = 1400) -> list[dict[str, Any]]:
    """A history of `pairs` user turns, each followed by one large log message."""
    counter = TokenCounter(Settings().REASONING_LLM_MODEL)
    bulk = bulk_text(counter, target_tokens)
    messages: list[dict[str, Any]] = []
    for turn in range(pairs):
        messages.append(
            {
                "id": f"user-{turn}",
                "role": "user",
                "content": f"Turn {turn}: process batch {turn} and report status.",
            }
        )
        messages.append({"id": f"bulk-{turn}", "role": "assistant", "content": bulk})
    return messages


@pytest.mark.asyncio
async def test_token_reduction() -> None:
    """On a 40k token fixture the compacted prompt is at most 40 percent."""
    service, _ = service_with()
    messages = large_history()
    counter = service.token_counter

    original = counter.count_many([message["content"] for message in messages])
    assert original >= 40_000

    context = await service.compact(uuid4(), messages)

    compacted = counter.count_many([message["content"] for message in context.messages])
    assert compacted == context.compacted_tokens
    assert compacted <= 0.4 * original

    # The reduction comes from the removal of the old bulk turns, not from a
    # missing summary or a missing recent turn.
    assert context.plan.units_dropped >= 20
    assert context.summary is not None
    assert context.summary.fits_budget is True
    recent_ids = {f"bulk-{turn}" for turn in range(24, 30)}
    assembled_ids = {message["id"] for message in context.messages}
    assert recent_ids <= assembled_ids
