"""Compaction service implementing the three-tier context pipeline.

Tier 0 locks the units that must never leave the context. Tier 1 asks Jev
three calibrated questions per unit and turns each answer into a retain, stub,
or drop verdict. Tier 2 turns the dropped and stubbed units into one bounded
summary block with the Fast LLM.

Then the service assembles the compacted history and verifies it. A failed
verification raises and persists nothing, so a broken history never reaches
the model.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, ClassVar, Mapping, Protocol, Sequence
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings
from src.integrations.repositories import JevClientProtocol, LLMClientProtocol
from src.modules.compaction.data import CompactionDryRunResponse
from src.modules.compaction.repository import (
    CompactionResultDTO,
    CompactionUnitDTO,
)
from src.modules.compaction.stub import ArtifactStub
from src.modules.compaction.summarizer import (
    DEFAULT_SUMMARY_TOKEN_BUDGET,
    CompactionSummarizer,
    SummaryBlock,
    SummarySource,
)
from src.modules.compaction.tiers import (
    JEV_BATCH_SIZE,
    MAX_JEV_CALLS,
    HistoryUnit,
    Tier0Selection,
    Tier1Result,
    TokenCounter,
    UnitKind,
    Verdict,
    lock_tier0_units,
    resolve_verdicts,
    score_units,
    units_from_messages,
)
from src.modules.compaction.verify import (
    VerificationReport,
    verify_compacted_history,
)
from src.modules.runs.repository import RunStepCreateDTO, RunStepDTO

# A run compacts when its history passes this share of the token window.
COMPACTION_TRIGGER_RATIO = 0.7

# Event type of the run step that records the summary.
COMPACTION_STEP_TYPE = "compaction"

# Id of the system message that carries the summary block.
SUMMARY_MESSAGE_ID = "compaction-summary"

# Keys that the assembly copies from the source message. The tool call id and
# the tool calls keep a tool chain legal after a stub.
PRESERVED_MESSAGE_KEYS: tuple[str, ...] = (
    "tool_calls",
    "tool_call_id",
    "name",
    "kind",
    "status",
    "decision",
    "approval_id",
)


class RunStepRecorder(Protocol):
    """Recorder that stores one run step row."""

    async def save_run_step(self, data: RunStepCreateDTO) -> RunStepDTO:
        """Store one step and return it."""
        ...


@dataclass(frozen=True)
class PlannedUnit:
    """One history unit after Tier 1, with the text that stays in context.

    `tokens` counts `content`, which is the stub text for a stubbed unit.
    `source_tokens` counts the original text, because Tier 2 needs the size of
    the removed text, not the size of the stub.
    """

    unit: HistoryUnit
    verdict: Verdict
    content: str
    tokens: int
    source_tokens: int = 0


@dataclass(frozen=True)
class CompactionPlan:
    """Result of the pipeline up to Tier 1, plus the Tier 2 block when set."""

    kept: tuple[PlannedUnit, ...]
    stubbed: tuple[PlannedUnit, ...]
    dropped: tuple[PlannedUnit, ...]
    tier0: Tier0Selection
    tier1: Tier1Result
    original_tokens: int
    compacted_tokens: int
    order: tuple[str, ...] = ()
    summary: SummaryBlock | None = None

    @property
    def units_dropped(self) -> int:
        """Number of units removed from the active context."""
        return len(self.dropped)

    @property
    def units_stubbed(self) -> int:
        """Number of units replaced by an artifact stub."""
        return len(self.stubbed)

    @property
    def summary_block(self) -> str:
        """Text of the Tier 2 block. Empty when Tier 2 did not run."""
        return "" if self.summary is None else self.summary.text

    def summary_sources(self) -> tuple[SummarySource, ...]:
        """Return the removed units that Tier 2 must summarize."""
        return tuple(
            SummarySource(
                unit_id=item.unit.unit_id,
                kind=item.unit.kind,
                role=item.unit.role,
                content=item.unit.content,
                tokens=item.source_tokens,
                verdict=item.verdict.value,
            )
            for item in (*self.dropped, *self.stubbed)
        )


@dataclass(frozen=True)
class CompactedContext:
    """The assembled and verified history for one run."""

    run_id: UUID
    messages: list[dict[str, Any]]
    plan: CompactionPlan
    verification: VerificationReport
    compacted_tokens: int
    step: RunStepDTO | None = None

    @property
    def summary(self) -> SummaryBlock | None:
        """The Tier 2 block, or None when nothing left the context."""
        return self.plan.summary

    @property
    def original_tokens(self) -> int:
        """Token count of the source history."""
        return self.plan.original_tokens

    @property
    def summary_block(self) -> str:
        """Text of the summary block."""
        return self.plan.summary_block

    @property
    def units_dropped(self) -> int:
        """Number of units removed from the active context."""
        return self.plan.units_dropped

    @property
    def units_stubbed(self) -> int:
        """Number of units replaced by an artifact stub."""
        return self.plan.units_stubbed

    def to_result(self) -> CompactionResultDTO:
        """Return the persisted result summary."""
        return CompactionResultDTO(
            run_id=self.run_id,
            original_tokens=self.original_tokens,
            compacted_tokens=self.compacted_tokens,
            summary_block=self.summary_block,
            units_dropped=self.units_dropped,
            units_stubbed=self.units_stubbed,
        )


class CompactionService:
    """
    Context compaction service satisfying CompactionRepository via duck typing.
    Tier 0: Deterministic safety lock (retains user messages, last 6 turns, approvals).
    Tier 1: Jev probability gate (converts bulk logs to artifact stubs, drops irrelevant turns).
    Tier 2: Fast LLM summarizer compiling dropped turns into concise executive block.
    """

    _compacted_history: ClassVar[dict[UUID, tuple[list[dict[str, Any]], str]]] = {}

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        jev_client: JevClientProtocol | None = None,
        fast_llm: LLMClientProtocol | None = None,
        token_counter: TokenCounter | None = None,
        token_model: str | None = None,
        summarizer: CompactionSummarizer | None = None,
        run_steps: RunStepRecorder | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.jev_client = jev_client
        self.fast_llm = fast_llm
        self._token_counter = token_counter
        self._token_model = token_model
        self._summarizer = summarizer
        self.run_steps = run_steps

    @property
    def token_counter(self) -> TokenCounter:
        """Provider tokenizer for the context model, built on first use."""
        if self._token_counter is None:
            model = self._token_model or self.settings.REASONING_LLM_MODEL
            self._token_counter = TokenCounter(model)
        return self._token_counter

    @property
    def summarizer(self) -> CompactionSummarizer:
        """Tier 2 summarizer on the Fast model from settings."""
        if self._summarizer is None:
            self._summarizer = CompactionSummarizer(
                llm=self.fast_llm,
                model=self.settings.FAST_LLM_MODEL,
            )
        return self._summarizer

    async def load_run_history_for_compaction(self, run_id: UUID) -> list[CompactionUnitDTO]:
        return []

    async def save_compacted_history(
        self,
        run_id: UUID,
        compacted_messages: list[dict[str, Any]],
        summary_block: str,
    ) -> None:
        self._compacted_history[run_id] = (compacted_messages, summary_block)

    # Domain Orchestration Methods
    async def plan_compaction(
        self,
        units: Sequence[HistoryUnit],
        batch_size: int = JEV_BATCH_SIZE,
        max_calls: int = MAX_JEV_CALLS,
    ) -> CompactionPlan:
        """Run Tier 0 and Tier 1 over a history and return the plan.

        Every token count comes from the provider tokenizer of the configured
        model. A unit that Tier 1 does not score stays in the context. A system
        message always stays, because the system prompt is not history.
        """
        tier0 = lock_tier0_units(units)
        tier1 = await score_units(
            units,
            self.jev_client,
            batch_size=batch_size,
            max_calls=max_calls,
        )
        verdicts = resolve_verdicts(units, tier1, tier0)

        kept: list[PlannedUnit] = []
        stubbed: list[PlannedUnit] = []
        dropped: list[PlannedUnit] = []
        original_tokens = 0
        compacted_tokens = 0

        for unit in units:
            original = self.token_counter.count(unit.content)
            original_tokens += original
            verdict = verdicts[unit.unit_id]
            if unit.kind is UnitKind.SYSTEM:
                # The system prompt is not history. Tier 2 must never summarize it.
                verdict = Verdict.RETAIN

            if verdict is Verdict.DROP:
                dropped.append(
                    PlannedUnit(
                        unit=unit,
                        verdict=verdict,
                        content="",
                        tokens=0,
                        source_tokens=original,
                    )
                )
                continue

            if verdict is Verdict.STUB:
                if not unit.artifact_id:
                    # A stub must point at an artifact. Without one the unit
                    # stays in full, because a lost pointer is worse than bulk.
                    compacted_tokens += original
                    kept.append(
                        PlannedUnit(
                            unit=unit,
                            verdict=Verdict.RETAIN,
                            content=unit.content,
                            tokens=original,
                            source_tokens=original,
                        )
                    )
                    continue
                stub_text = ArtifactStub.from_unit(unit).render()
                stub_tokens = self.token_counter.count(stub_text)
                compacted_tokens += stub_tokens
                stubbed.append(
                    PlannedUnit(
                        unit=unit,
                        verdict=verdict,
                        content=stub_text,
                        tokens=stub_tokens,
                        source_tokens=original,
                    )
                )
                continue

            compacted_tokens += original
            kept.append(
                PlannedUnit(
                    unit=unit,
                    verdict=verdict,
                    content=unit.content,
                    tokens=original,
                    source_tokens=original,
                )
            )

        return CompactionPlan(
            kept=tuple(kept),
            stubbed=tuple(stubbed),
            dropped=tuple(dropped),
            tier0=tier0,
            tier1=tier1,
            original_tokens=original_tokens,
            compacted_tokens=compacted_tokens,
            order=tuple(unit.unit_id for unit in units),
        )

    async def summarize_plan(self, plan: CompactionPlan) -> CompactionPlan:
        """Run Tier 2 over the removed units and return the plan with the block.

        Tier 2 is skipped when nothing left the context, so a history that fits
        the window costs no model call.
        """
        sources = plan.summary_sources()
        if not sources:
            return plan
        summary = await self.summarizer.summarize(sources)
        return replace(plan, summary=summary)

    def assemble_context(
        self,
        plan: CompactionPlan,
        originals: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Assemble the compacted message list in the source order.

        The summary block comes first, as a system message. Then every kept and
        stubbed unit follows in the order of the source history.
        """
        sources = originals or {}
        messages: list[dict[str, Any]] = []
        if plan.summary is not None:
            messages.append(
                {
                    "role": "system",
                    "content": plan.summary.text,
                    "id": SUMMARY_MESSAGE_ID,
                    "kind": "summary",
                }
            )

        order = plan.order or tuple(
            item.unit.unit_id for item in (*plan.kept, *plan.stubbed)
        )
        planned = {item.unit.unit_id: item for item in (*plan.kept, *plan.stubbed)}
        for unit_id in order:
            item = planned.get(unit_id)
            if item is None:
                continue
            message: dict[str, Any] = {
                "role": item.unit.role,
                "content": item.content,
                "id": unit_id,
            }
            original = sources.get(unit_id)
            if original is not None:
                for key in PRESERVED_MESSAGE_KEYS:
                    if key in original:
                        message[key] = original[key]
            messages.append(message)
        return messages

    async def compact(
        self,
        run_id: UUID,
        messages: Sequence[Mapping[str, Any]],
        step_number: int | None = None,
    ) -> CompactedContext:
        """Run the three tiers, assemble the history, verify it, and trace it.

        A failed verification raises `CompactionVerificationError`. Nothing is
        saved in that case, so the run fails before a model call.
        """
        units = units_from_messages(messages)
        originals = {unit.unit_id: message for unit, message in zip(units, messages)}

        plan = await self.plan_compaction(units)
        plan = await self.summarize_plan(plan)
        assembled = self.assemble_context(plan, originals)
        verification = verify_compacted_history(assembled, messages)

        compacted_tokens = self.token_counter.count_many(
            [str(message.get("content") or "") for message in assembled]
        )
        await self.save_compacted_history(run_id, assembled, plan.summary_block)
        step = await self.record_compaction_step(
            run_id=run_id,
            plan=plan,
            verification=verification,
            compacted_tokens=compacted_tokens,
            step_number=step_number,
        )

        return CompactedContext(
            run_id=run_id,
            messages=assembled,
            plan=plan,
            verification=verification,
            compacted_tokens=compacted_tokens,
            step=step,
        )

    async def record_compaction_step(
        self,
        run_id: UUID,
        plan: CompactionPlan,
        verification: VerificationReport,
        compacted_tokens: int,
        step_number: int | None = None,
    ) -> RunStepDTO | None:
        """Store the summary chain as one `compaction` run step.

        Returns None when no step recorder is wired, so the pipeline can run
        without the runs repository.
        """
        if self.run_steps is None:
            return None

        if step_number is None:
            step_number = await self.next_step_number(run_id)

        summary = plan.summary
        payload: dict[str, Any] = {
            "summary_block": plan.summary_block,
            "summary_tokens": 0 if summary is None else summary.tokens,
            "summary_budget_tokens": (
                DEFAULT_SUMMARY_TOKEN_BUDGET if summary is None else summary.budget_tokens
            ),
            "summary_model": self.settings.FAST_LLM_MODEL,
            "summary_truncated": False if summary is None else summary.truncated,
            "summary_sources": [
                {"unit_id": source.unit_id, "verdict": source.verdict, "tokens": source.tokens}
                for source in plan.summary_sources()
            ],
            "original_tokens": plan.original_tokens,
            "compacted_tokens": compacted_tokens,
            "units_kept": len(plan.kept),
            "units_stubbed": plan.units_stubbed,
            "units_dropped": plan.units_dropped,
            "jev_calls": plan.tier1.jev_calls,
            "verified": True,
            "approvals_checked": list(verification.approval_ids),
            "tool_calls_checked": list(verification.tool_call_ids),
        }
        return await self.run_steps.save_run_step(
            RunStepCreateDTO(
                run_id=run_id,
                step_number=step_number,
                event_type=COMPACTION_STEP_TYPE,
                payload=payload,
            )
        )

    async def next_step_number(self, run_id: UUID) -> int:
        """Return the next step number for a run."""
        recorder = self.run_steps
        reader = getattr(recorder, "get_steps_since", None)
        if reader is None:
            return 1
        steps = await reader(run_id, 0)
        return max((step.step_number for step in steps), default=0) + 1

    async def check_and_compact(
        self,
        run_id: UUID,
        max_tokens: int,
        messages: Sequence[Mapping[str, Any]] | None = None,
    ) -> CompactionResultDTO | None:
        """Compact the run history when it passes 70 percent of the window.

        Returns None when the history is inside the window, because no
        compaction is needed.
        """
        source = list(messages) if messages is not None else await self.load_messages(run_id)
        if not source:
            return None

        original_tokens = self.token_counter.count_many(
            [str(message.get("content") or "") for message in source]
        )
        if original_tokens <= max_tokens * COMPACTION_TRIGGER_RATIO:
            return None

        context = await self.compact(run_id, source)
        return context.to_result()

    async def load_messages(self, run_id: UUID) -> list[dict[str, Any]]:
        """Load the run history as messages, from the repository units."""
        units = await self.load_run_history_for_compaction(run_id)
        return [
            {"id": str(unit.message_id), "role": unit.role, "content": unit.content}
            for unit in units
        ]

    async def dry_run(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int,
    ) -> CompactionDryRunResponse:
        """Perform simulated compaction without saving to DB.

        A dry run stops at Tier 1. Tier 2 costs a model call, so only a real
        compaction asks for the summary block.
        """
        plan = await self.plan_compaction(units_from_messages(messages))
        return CompactionDryRunResponse(
            original_tokens=plan.original_tokens,
            compacted_tokens=plan.compacted_tokens,
            summary_block=plan.summary_block,
            units_dropped=plan.units_dropped,
            units_stubbed=plan.units_stubbed,
        )
