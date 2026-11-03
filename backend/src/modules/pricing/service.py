"""Pricing service: owns the SQL and the integer arithmetic that turns tokens into micros.

Money never touches a float here. A rate is micros per million tokens, so the
cost of a call is an integer division by one million.
"""
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.pricing.repository import (
    ModelPriceDTO,
    ModelPriceUpsertDTO,
    PricingRepository,
)
from src.schemas.system import ModelPriceModel

MICROS_PER_UNIT = 1_000_000
TOKENS_PER_MTOK = 1_000_000


class SqlPricingRepository:
    """A pricing repository over the application database session."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _dto(model: ModelPriceModel) -> ModelPriceDTO:
        return ModelPriceDTO(
            id=model.id,
            model=model.model,
            prompt_micros_per_mtok=int(model.prompt_micros_per_mtok),
            completion_micros_per_mtok=int(model.completion_micros_per_mtok),
            currency=model.currency,
            updated_at=model.updated_at,
        )

    async def get_by_model(self, model: str) -> ModelPriceDTO | None:
        """Return the price of one model, or None when it has no row."""
        found = await self.session.execute(
            select(ModelPriceModel).where(ModelPriceModel.model == model)
        )
        row = found.scalar_one_or_none()
        return self._dto(row) if row is not None else None

    async def list_prices(self) -> list[ModelPriceDTO]:
        """Return every stored price, ordered by model name."""
        found = await self.session.execute(
            select(ModelPriceModel).order_by(ModelPriceModel.model.asc())
        )
        return [self._dto(row) for row in found.scalars().all()]

    async def upsert(self, data: ModelPriceUpsertDTO) -> ModelPriceDTO:
        """Insert a price, or replace the row of the same model."""
        found = await self.session.execute(
            select(ModelPriceModel).where(ModelPriceModel.model == data.model)
        )
        row = found.scalar_one_or_none()
        if row is None:
            row = ModelPriceModel(
                model=data.model,
                prompt_micros_per_mtok=data.prompt_micros_per_mtok,
                completion_micros_per_mtok=data.completion_micros_per_mtok,
                currency=data.currency,
            )
            self.session.add(row)
        else:
            row.prompt_micros_per_mtok = data.prompt_micros_per_mtok
            row.completion_micros_per_mtok = data.completion_micros_per_mtok
            row.currency = data.currency
        await self.session.commit()
        await self.session.refresh(row)
        return self._dto(row)


class PricingService:
    """Resolve rates and compute the cost of a model call in integer micros."""

    def __init__(self, repository: PricingRepository) -> None:
        self.repository = repository
        self._cache: dict[str, ModelPriceDTO | None] = {}

    async def price_for(self, model: str) -> ModelPriceDTO | None:
        """Return the rate of a model, reading each model once per service lifetime."""
        if model not in self._cache:
            self._cache[model] = await self.repository.get_by_model(model)
        return self._cache[model]

    async def cost_micros(
        self, model: str, prompt_tokens: int, completion_tokens: int
    ) -> int:
        """Return the cost of one call in micros.

        Args:
            model: Model name of the call.
            prompt_tokens: Prompt tokens billed by the provider.
            completion_tokens: Completion tokens billed by the provider.

        Returns:
            Whole micros. A model with no price row costs zero, which keeps the
            token counts visible without inventing a rate.
        """
        price = await self.price_for(model)
        if price is None:
            return 0
        numerator = (
            max(0, prompt_tokens) * price.prompt_micros_per_mtok
            + max(0, completion_tokens) * price.completion_micros_per_mtok
        )
        return numerator // TOKENS_PER_MTOK

    async def list_prices(self) -> list[ModelPriceDTO]:
        """Return every stored price."""
        return await self.repository.list_prices()

    async def set_price(self, data: ModelPriceUpsertDTO) -> ModelPriceDTO:
        """Store a rate and drop any cached value for that model."""
        saved = await self.repository.upsert(data)
        self._cache[data.model] = saved
        return saved


def micros_to_display(micros: int) -> float:
    """Return a currency amount for display only. Never use it for arithmetic."""
    return micros / MICROS_PER_UNIT
