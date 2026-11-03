"""Pricing router factory: read the rate table and set a rate."""
from fastapi import APIRouter, Depends, status

from src.config.settings import Settings
from src.dependencies import get_current_device, get_pricing_service
from src.modules.auth.repository import DeviceDTO
from src.modules.pricing.data import ModelPriceResponse, ModelPriceUpsert
from src.modules.pricing.repository import ModelPriceUpsertDTO
from src.modules.pricing.service import PricingService


def build_pricing_router(settings: Settings) -> APIRouter:
    """Build and configure the pricing APIRouter.

    Endpoints:
      - GET /api/v1/pricing
      - PUT /api/v1/pricing/{model:path}
    """
    router = APIRouter(prefix="/pricing", tags=["Pricing"])

    @router.get(
        "",
        response_model=list[ModelPriceResponse],
        status_code=status.HTTP_200_OK,
        summary="List model prices",
    )
    async def list_prices(
        service: PricingService = Depends(get_pricing_service),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> list[ModelPriceResponse]:
        """Return every stored rate, in integer micros per million tokens."""
        prices = await service.list_prices()
        return [ModelPriceResponse.model_validate(price) for price in prices]

    @router.put(
        "/{model:path}",
        response_model=ModelPriceResponse,
        status_code=status.HTTP_200_OK,
        summary="Set a model price",
    )
    async def set_price(
        model: str,
        body: ModelPriceUpsert,
        service: PricingService = Depends(get_pricing_service),
        _device: DeviceDTO = Depends(get_current_device),
    ) -> ModelPriceResponse:
        """Insert or replace the rate of one model."""
        saved = await service.set_price(
            ModelPriceUpsertDTO(
                model=model,
                prompt_micros_per_mtok=body.prompt_micros_per_mtok,
                completion_micros_per_mtok=body.completion_micros_per_mtok,
                currency=body.currency,
            )
        )
        return ModelPriceResponse.model_validate(saved)

    return router
