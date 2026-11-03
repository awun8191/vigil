"""Pricing tests: integer arithmetic, the zero-cost default, and charging a run."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.config.settings import Settings
from src.modules.pricing.repository import ModelPriceDTO, ModelPriceUpsertDTO
from src.modules.pricing.service import PricingService
from src.modules.runs.repository import RunCreateDTO
from src.modules.runs.service import RunsService
from src.modules.runs.state import InMemoryRunJournal


class FakePricingRepository:
    """An in-memory pricing repository."""

    def __init__(self) -> None:
        self.rows: dict[str, ModelPriceDTO] = {}

    async def get_by_model(self, model: str) -> ModelPriceDTO | None:
        return self.rows.get(model)

    async def list_prices(self) -> list[ModelPriceDTO]:
        return list(self.rows.values())

    async def upsert(self, data: ModelPriceUpsertDTO) -> ModelPriceDTO:
        row = ModelPriceDTO(
            id=uuid4(),
            model=data.model,
            prompt_micros_per_mtok=data.prompt_micros_per_mtok,
            completion_micros_per_mtok=data.completion_micros_per_mtok,
            currency=data.currency,
            updated_at=datetime.now(timezone.utc),
        )
        self.rows[data.model] = row
        return row


async def _seeded(prompt_rate: int, completion_rate: int) -> PricingService:
    repository = FakePricingRepository()
    service = PricingService(repository=repository)
    await service.set_price(
        ModelPriceUpsertDTO(
            model="priced-model",
            prompt_micros_per_mtok=prompt_rate,
            completion_micros_per_mtok=completion_rate,
        )
    )
    return service


@pytest.mark.asyncio
async def test_cost_of_a_call_is_integer_micros() -> None:
    """Three dollars per million prompt tokens and fifteen per completion."""
    service = await _seeded(3_000_000, 15_000_000)

    cost = await service.cost_micros(
        "priced-model", prompt_tokens=1_000, completion_tokens=500
    )

    # 1000 tokens at 3_000_000 micros per M is 3000 micros.
    # 500 tokens at 15_000_000 micros per M is 7500 micros.
    assert cost == 10_500
    assert isinstance(cost, int)


@pytest.mark.asyncio
async def test_a_model_with_no_price_costs_zero() -> None:
    """An unknown model keeps its token counts and adds no invented money."""
    service = await _seeded(3_000_000, 15_000_000)

    assert await service.cost_micros("unpriced-model", 1_000, 500) == 0


@pytest.mark.asyncio
async def test_a_negative_token_count_is_treated_as_zero() -> None:
    """A provider bug cannot produce a negative charge."""
    service = await _seeded(3_000_000, 15_000_000)

    assert await service.cost_micros("priced-model", -50, 0) == 0


@pytest.mark.asyncio
async def test_setting_a_price_drops_the_cached_value() -> None:
    """A new rate applies to the next call, not after a restart."""
    service = await _seeded(3_000_000, 15_000_000)
    assert await service.cost_micros("priced-model", 1_000, 0) == 3_000

    await service.set_price(
        ModelPriceUpsertDTO(
            model="priced-model",
            prompt_micros_per_mtok=6_000_000,
            completion_micros_per_mtok=30_000_000,
        )
    )

    assert await service.cost_micros("priced-model", 1_000, 0) == 6_000


@pytest.mark.asyncio
async def test_charge_adds_the_cost_to_the_run() -> None:
    """The runs service turns one call into money on the run row."""
    journal = InMemoryRunJournal()
    run = await journal.create_run(RunCreateDTO(config_id=uuid4()))
    pricing = await _seeded(3_000_000, 15_000_000)
    service = RunsService(
        session=None,
        settings=Settings(APP_ENV="test"),
        journal=journal,
        pricing=pricing,
    )

    charged = await service.charge(run.id, "priced-model", 1_000, 500)

    assert charged == 10_500
    assert await journal.get_cost_micros(run.id) == 10_500
    stored = await journal.get_run(run.id)
    assert stored is not None
    assert stored.cost_micros == 10_500
    assert stored.cost_usd == pytest.approx(0.0105)


@pytest.mark.asyncio
async def test_charge_without_a_pricing_service_does_nothing() -> None:
    """A service built without pricing records tokens only."""
    journal = InMemoryRunJournal()
    run = await journal.create_run(RunCreateDTO(config_id=uuid4()))
    service = RunsService(
        session=None, settings=Settings(APP_ENV="test"), journal=journal
    )

    assert await service.charge(run.id, "any-model", 10, 10) == 0
    assert await journal.get_cost_micros(run.id) == 0
