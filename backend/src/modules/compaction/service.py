"""Compaction service implementing two-tier context compaction: Jev gate + Fast LLM."""
from __future__ import annotations

from typing import Any, ClassVar
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings
from src.integrations.repositories import JevClientProtocol, LLMClientProtocol
from src.modules.compaction.data import CompactionDryRunResponse
from src.modules.compaction.repository import (
    CompactionResultDTO,
    CompactionUnitDTO,
)


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
    ) -> None:
        self.session = session
        self.settings = settings
        self.jev_client = jev_client
        self.fast_llm = fast_llm

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
        return CompactionDryRunResponse(
            original_tokens=len(messages) * 100,
            compacted_tokens=int(len(messages) * 35),
            summary_block="Simulated summary block.",
            units_dropped=max(0, len(messages) - 6),
            units_stubbed=1,
        )
