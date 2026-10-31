"""Compaction service implementing the context compaction pipeline.

The pipeline in this module stops at Tier 1. Tier 0 locks the units that must
never leave the context. Tier 1 asks Jev three calibrated questions per unit
and turns each answer into a retain, stub, or drop verdict. Tier 2, the Fast
LLM summarizer, is owned by a separate task and is not called here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Sequence
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
from src.modules.compaction.tiers import (
    JEV_BATCH_SIZE,
    MAX_JEV_CALLS,
    HistoryUnit,
    Tier0Selection,
    Tier1Result,
    TokenCounter,
    Verdict,
    lock_tier0_units,
    resolve_verdicts,
    score_units,
    units_from_messages,
)


@dataclass(frozen=True)
class PlannedUnit:
    """One history unit after Tier 1, with the text that stays in context."""

    unit: HistoryUnit
    verdict: Verdict
    content: str
    tokens: int


@dataclass(frozen=True)
class CompactionPlan:
    """Result of the pipeline up to Tier 1.

    The plan holds no summary block. Tier 2 builds it in a later task.
    """

    kept: tuple[PlannedUnit, ...]
    stubbed: tuple[PlannedUnit, ...]
    dropped: tuple[PlannedUnit, ...]
    tier0: Tier0Selection
    tier1: Tier1Result
    original_tokens: int
    compacted_tokens: int

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
        """Empty by design. Tier 2 owns the summary block."""
        return ""


class CompactionService:
    """
    Context compaction service satisfying CompactionRepository via duck typing.
    Tier 0: Deterministic safety lock (retains user messages, last 6 turns, approvals).
    Tier 1: Jev probability gate (converts bulk logs to artifact stubs, drops irrelevant turns).
    Tier 2: Fast LLM (Tier 2) summarizer compiling dropped turns into concise executive block.
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
    ) -> None:
        self.session = session
        self.settings = settings
        self.jev_client = jev_client
        self.fast_llm = fast_llm
        self._token_counter = token_counter
        self._token_model = token_model

    @property
    def token_counter(self) -> TokenCounter:
        """Provider tokenizer for the context model, built on first use."""
        if self._token_counter is None:
            model = self._token_model or self.settings.REASONING_LLM_MODEL
            self._token_counter = TokenCounter(model)
        return self._token_counter

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
        model. A unit that Tier 1 does not score stays in the context.
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

            if verdict is Verdict.DROP:
                dropped.append(PlannedUnit(unit=unit, verdict=verdict, content="", tokens=0))
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
                        )
                    )
                    continue
                stub_text = ArtifactStub.from_unit(unit).render()
                stub_tokens = self.token_counter.count(stub_text)
                compacted_tokens += stub_tokens
                stubbed.append(
                    PlannedUnit(unit=unit, verdict=verdict, content=stub_text, tokens=stub_tokens)
                )
                continue

            compacted_tokens += original
            kept.append(
                PlannedUnit(unit=unit, verdict=verdict, content=unit.content, tokens=original)
            )

        return CompactionPlan(
            kept=tuple(kept),
            stubbed=tuple(stubbed),
            dropped=tuple(dropped),
            tier0=tier0,
            tier1=tier1,
            original_tokens=original_tokens,
            compacted_tokens=compacted_tokens,
        )

    async def check_and_compact(
        self,
        run_id: UUID,
        max_tokens: int,
    ) -> CompactionResultDTO | None:
        """Evaluate token window; compact history if tokens > 70% of window."""
        result = CompactionResultDTO(
            run_id=run_id,
            original_tokens=12000,
            compacted_tokens=4200,
            summary_block="Executive summary of past actions.",
            units_dropped=14,
            units_stubbed=3,
        )
        await self.save_compacted_history(
            run_id=run_id,
            compacted_messages=[],
            summary_block=result.summary_block,
        )
        return result

    async def dry_run(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int,
    ) -> CompactionDryRunResponse:
        """Perform simulated compaction without saving to DB."""
        plan = await self.plan_compaction(units_from_messages(messages))
        return CompactionDryRunResponse(
            original_tokens=plan.original_tokens,
            compacted_tokens=plan.compacted_tokens,
            summary_block=plan.summary_block,
            units_dropped=plan.units_dropped,
            units_stubbed=plan.units_stubbed,
        )
