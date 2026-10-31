"""Triage repository protocol and data transfer objects."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID


@dataclass(frozen=True)
class TriageResultDTO:
    """Persisted record of an item triage classification."""
    id: UUID
    item_id: UUID
    answers: dict[str, str]
    probabilities: dict[str, float]
    decision: str  # card_created, discarded, review_required
    model_version: str
    created_at: datetime


@dataclass(frozen=True)
class TriageResultCreateDTO:
    """Payload for saving an item triage classification."""
    item_id: UUID
    answers: dict[str, str]
    probabilities: dict[str, float]
    decision: str
    model_version: str


@runtime_checkable
class TriageRepository(Protocol):
    """Protocol contract for persisting triage results."""

    async def save_triage_result(self, data: TriageResultCreateDTO) -> TriageResultDTO:
        """
        Save the output of a triage pipeline execution.

        Args:
            data: Triage result creation DTO.

        Returns:
            The persisted TriageResultDTO.
        """
        ...

    async def get_triage_result_by_item(self, item_id: UUID) -> TriageResultDTO | None:
        """
        Retrieve triage classification for a specific ingested item.

        Args:
            item_id: UUID of the source item.

        Returns:
            TriageResultDTO if item has been triaged, None otherwise.
        """
        ...

    async def list_recent_triage(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TriageResultDTO]:
        """
        List recent triage decisions for auditing and telemetry.

        Args:
            limit: Maximum items to return.
            offset: Pagination offset.

        Returns:
            List of TriageResultDTOs ordered by creation time descending.
        """
        ...
