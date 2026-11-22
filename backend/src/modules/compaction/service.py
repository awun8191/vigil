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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings
from src.integrations.repositories import LLMClientProtocol
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
    strip_thinking_blocks,
)
from src.modules.compaction.tiers import (
    HistoryUnit,
    Tier0Selection,
    Tier1Result,
    TokenCounter,
    UnitKind,
    Verdict,
    lock_tier0_units,
    resolve_verdicts,
    score_units_deterministic,
    units_from_messages,
)
from src.modules.compaction.verify import (
    VerificationReport,
    verify_compacted_history,
)
from src.modules.runs.repository import RunStepCreateDTO, RunStepDTO
from src.schemas.runtime import MessageModel, ThreadModel

# A run compacts when its history passes this share of the token window.
COMPACTION_TRIGGER_RATIO = 0.7

# Default interaction barrier: keep the last 8 user/assistant interactions verbatim.
DEFAULT_INTERACTION_BARRIER = 8
MIN_INTERACTION_BARRIER = 5
DEFAULT_OVERFLOW_INTERACTIONS = 4

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


@dataclass(frozen=True)
class SessionCompactionOutcome:
    """Result of one session-level rolling compaction pass."""

    rolling_summary: str
    compacted_through_message_id: UUID
    compacted_through_at: Any
    kept_messages: tuple[Any, ...]
    interactions_compacted: int
    summary_tokens: int


def _message_field(msg: Any, name: str, default: Any = None) -> Any:
    if isinstance(msg, Mapping):
        return msg.get(name, default)
    return getattr(msg, name, default)


def group_interactions(messages: Sequence[Any]) -> list[list[Any]]:
    """Group clean conversational prose turns into discrete user/assistant interactions.

    Tool calls, tool results, system messages, and empty turns are excluded.
    """
    prose: list[Any] = []
    for msg in messages:
        role = str(_message_field(msg, "role", ""))
        if role not in ("user", "assistant"):
            continue
        if role == "assistant" and _message_field(msg, "tool_calls"):
            continue
        cleaned = strip_thinking_blocks(str(_message_field(msg, "content") or ""))
        if not cleaned:
            continue
        prose.append(msg)

    interactions: list[list[Any]] = []
    current: list[Any] = []
    for msg in prose:
        role = str(_message_field(msg, "role", ""))
        if role == "user":
            if current:
                interactions.append(current)
            current = [msg]
        else:
            if not current:
                current = [msg]
            else:
                current.append(msg)
    if current:
        interactions.append(current)
    return interactions


class CompactionService:
    """
    Context compaction service satisfying CompactionRepository via duck typing.
    Handles JEV-free session-level rolling compaction (8-interaction barrier,
    clean prose summarizer input) and deterministic intra-run tool-stubbing.
    """

    _compacted_history: ClassVar[dict[UUID, tuple[list[dict[str, Any]], str]]] = {}

    def __init__(
        self,
        session: AsyncSession | None,
        settings: Settings,
        fast_llm: LLMClientProtocol | None = None,
        token_counter: TokenCounter | None = None,
        token_model: str | None = None,
        summarizer: CompactionSummarizer | None = None,
        run_steps: RunStepRecorder | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
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
        """Load the messages of a run as compaction units, in row order.

        The lock flags come from Tier 0, so the HTTP endpoint and the pipeline
        read the same rules. A service without a session holds no history.
        """
        if self.session is None:
            return []
        statement = (
            select(MessageModel)
            .where(MessageModel.run_id == run_id)
            .order_by(MessageModel.created_at.asc(), MessageModel.id.asc())
        )
        rows = (await self.session.execute(statement)).scalars().all()
        if not rows:
            return []
        messages = [
            {"id": str(row.id), "role": row.role, "content": row.content or ""}
            for row in rows
        ]
        locked = lock_tier0_units(units_from_messages(messages)).locked_ids
        return [
            CompactionUnitDTO(
                message_id=row.id,
                role=row.role,
                content=row.content or "",
                token_count=self.token_counter.count(row.content or ""),
                is_locked=str(row.id) in locked,
            )
            for row in rows
        ]

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
    ) -> CompactionPlan:
        """Build a deterministic intra-run compaction plan without calling Jev.

        Tier 0 locks user messages, approvals, decisions, recent turns, and
        final tool-chain results. Unlocked bulk tool outputs become artifact
        stubs when they carry an artifact reference, or are dropped so Tier 2
        can summarize them.
        """
        tier0 = lock_tier0_units(units)
        tier1 = score_units_deterministic(units, tier0)
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
        if plan.units_dropped == 0 and plan.units_stubbed == 0:
            return CompactedContext(
                run_id=run_id,
                messages=assembled,
                plan=plan,
                verification=verification,
                compacted_tokens=compacted_tokens,
                step=None,
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

    async def compact_session_history(
        self,
        session_id: UUID,
        *,
        rolling_summary: str | None,
        uncompacted_messages: Sequence[Any],
        interaction_barrier: int | None = None,
        overflow_threshold: int | None = None,
        max_tokens: int | None = None,
        trigger_ratio: float = COMPACTION_TRIGGER_RATIO,
    ) -> SessionCompactionOutcome | None:
        """Roll older interactions above the verbatim tail barrier into `rolling_summary`.

        Only clean user and final assistant prose enters the summarizer; tool
        calls, tool results, and thinking blocks are never passed. Returns None
        when the uncompacted history does not cross the barrier trigger.
        """
        barrier = (
            interaction_barrier
            if interaction_barrier is not None
            else getattr(self.settings, "COMPACTION_INTERACTION_BARRIER", DEFAULT_INTERACTION_BARRIER)
        )
        overflow_min = (
            overflow_threshold
            if overflow_threshold is not None
            else getattr(self.settings, "COMPACTION_OVERFLOW_INTERACTIONS", DEFAULT_OVERFLOW_INTERACTIONS)
        )
        token_limit = (
            max_tokens
            if max_tokens is not None
            else getattr(self.settings, "CONTEXT_TOKEN_LIMIT", 8_000)
        )

        interactions = group_interactions(uncompacted_messages)
        if len(interactions) <= MIN_INTERACTION_BARRIER:
            return None

        history_texts = [
            strip_thinking_blocks(str(_message_field(msg, "content") or ""))
            for group in interactions
            for msg in group
        ]
        if rolling_summary:
            history_texts.insert(0, rolling_summary)
        estimated_tokens = self.token_counter.count_many(history_texts)
        over_token_budget = estimated_tokens > int(token_limit * trigger_ratio)
        overflow_count = len(interactions) - barrier

        if overflow_count < overflow_min and not (overflow_count > 0 and over_token_budget):
            if not over_token_budget:
                return None

        # Keep up to `barrier` interactions verbatim; if even `barrier` exceeds the
        # token trigger, step down to at most `MIN_INTERACTION_BARRIER` (5) interactions.
        keep_count = barrier
        if over_token_budget and len(interactions) > MIN_INTERACTION_BARRIER:
            while keep_count > MIN_INTERACTION_BARRIER:
                tail_texts = [
                    strip_thinking_blocks(str(_message_field(msg, "content") or ""))
                    for group in interactions[-keep_count:]
                    for msg in group
                ]
                if self.token_counter.count_many(tail_texts) <= int(token_limit * trigger_ratio):
                    break
                keep_count -= 1

        split_idx = max(0, len(interactions) - keep_count)
        if split_idx == 0:
            return None

        outgoing_groups = interactions[:split_idx]
        kept_groups = interactions[split_idx:]
        sources: list[SummarySource] = []
        last_rolled_msg: Any = None

        for group in outgoing_groups:
            for msg in group:
                last_rolled_msg = msg
                role = str(_message_field(msg, "role", "assistant"))
                cleaned = strip_thinking_blocks(str(_message_field(msg, "content") or ""))
                if not cleaned:
                    continue
                unit_kind = UnitKind.USER if role == "user" else UnitKind.ASSISTANT
                sources.append(
                    SummarySource(
                        unit_id=str(_message_field(msg, "id", "")),
                        kind=unit_kind,
                        role=role,
                        content=cleaned,
                        tokens=self.token_counter.count(cleaned),
                        verdict="drop",
                    )
                )

        if not sources or last_rolled_msg is None:
            return None

        summary_block = await self.summarizer.summarize_rolling(rolling_summary, sources)
        if summary_block is None:
            return None

        cursor_id = _message_field(last_rolled_msg, "id")
        cursor_at = _message_field(last_rolled_msg, "created_at")
        if self.session is not None and cursor_id is not None:
            thread = await self.session.get(ThreadModel, session_id)
            if thread is not None:
                thread.rolling_summary = summary_block.text
                thread.compacted_through_message_id = cursor_id
                thread.compacted_through_at = cursor_at
                await self.session.flush()

        kept_messages = tuple(msg for group in kept_groups for msg in group)
        return SessionCompactionOutcome(
            rolling_summary=summary_block.text,
            compacted_through_message_id=cursor_id,
            compacted_through_at=cursor_at,
            kept_messages=kept_messages,
            interactions_compacted=len(outgoing_groups),
            summary_tokens=summary_block.tokens,
        )

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
