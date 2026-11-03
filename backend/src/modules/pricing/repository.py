"""Pricing contracts: DTOs and the repository protocol. No SQL lives here."""
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID


@dataclass(frozen=True)
class ModelPriceDTO:
    """The rate of one model, in integer micros per million tokens."""

    id: UUID
    model: str
    prompt_micros_per_mtok: int
    completion_micros_per_mtok: int
    currency: str
    updated_at: datetime


@dataclass(frozen=True)
class ModelPriceUpsertDTO:
    """A rate to write. ``model`` identifies the row."""

    model: str
    prompt_micros_per_mtok: int
    completion_micros_per_mtok: int
    currency: str = "USD"


@runtime_checkable
class PricingRepository(Protocol):
    """Storage contract for model prices."""

    async def get_by_model(self, model: str) -> ModelPriceDTO | None:
        """Return the price of one model, or None when it has no row."""
        ...

    async def list_prices(self) -> list[ModelPriceDTO]:
        """Return every stored price."""
        ...

    async def upsert(self, data: ModelPriceUpsertDTO) -> ModelPriceDTO:
        """Insert a price, or replace the row of the same model."""
        ...


@runtime_checkable
class CostCharger(Protocol):
    """What the loop needs to turn one model call into money.

    The loop knows tokens, not rates. It hands the call to this port, which
    resolves the rate and adds the result to the run.
    """

    async def charge(
        self,
        run_id: UUID,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> int:
        """Add the cost of one call to a run and return the micros charged."""
        ...
