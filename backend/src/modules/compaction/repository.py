"""Compaction repository protocol and data transfer objects."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable
from uuid import UUID


@dataclass(frozen=True)
class CompactionUnitDTO:
    """Represents a conversation turn evaluated for compaction retention or pruning."""
    message_id: UUID
    role: str
    content: str
    token_count: int
    is_locked: bool


@dataclass(frozen=True)
class CompactionResultDTO:
    """Summary of a compaction cycle execution."""
    run_id: UUID
    original_tokens: int
    compacted_tokens: int
    summary_block: str
    units_dropped: int
    units_stubbed: int


@runtime_checkable
class CompactionRepository(Protocol):
    """Protocol contract for loading history and saving compacted turn contexts."""

    async def load_run_history_for_compaction(self, run_id: UUID) -> list[CompactionUnitDTO]:
        """
        Load messages for a run and annotate locked units (user turns, last 6 turns, decisions).

        Args:
            run_id: UUID of the run.

        Returns:
            List of CompactionUnitDTOs.
        """
        ...

    async def save_compacted_history(
        self,
        run_id: UUID,
        compacted_messages: list[dict[str, Any]],
        summary_block: str,
    ) -> None:
        """
        Persist the compacted message list and summary block for an active run.

        Args:
            run_id: UUID of the run.
            compacted_messages: Serialized message dictionaries forming the new context.
            summary_block: Executive summary block generated for pruned content.
        """
        ...
