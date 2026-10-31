"""Cards router factory providing Action Card feed management endpoints."""
from uuid import UUID
from fastapi import APIRouter, Depends, Query, status

from src.config.exceptions import NotFoundError
from src.config.settings import Settings
from src.dependencies import get_cards_service, get_current_user
from src.modules.auth.repository import UserDTO
from src.modules.cards.data import (
    CardCreateRequest,
    CardFeedbackRequest,
    CardListResponse,
    CardResponse,
    CardUpdateRequest,
)
from src.modules.cards.repository import (
    CardActionDTO,
    CardCreateDTO,
    CardsRepository,
    CardUpdateDTO,
)


def build_cards_router(settings: Settings) -> APIRouter:
    """
    Build and configure cards APIRouter instance.
    Endpoints:
      - GET /api/v1/cards
      - POST /api/v1/cards
      - GET /api/v1/cards/{id}
      - PATCH /api/v1/cards/{id}
      - POST /api/v1/cards/{id}/feedback
      - POST /api/v1/cards/{id}/dismiss
    """
    router = APIRouter(prefix="/cards", tags=["Cards"])

    @router.get(
        "",
        response_model=CardListResponse,
        status_code=status.HTTP_200_OK,
        summary="List Action Cards",
    )
    async def list_cards(
        status_filter: str | None = Query(default=None, alias="status"),
        urgency_filter: str | None = Query(default=None, alias="urgency"),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        service: CardsRepository = Depends(get_cards_service),
        user: UserDTO = Depends(get_current_user),
    ) -> CardListResponse:
        cards = await service.list_cards_by_user(
            user_id=user.id,
            status=status_filter,
            urgency=urgency_filter,
            limit=limit,
            offset=offset,
        )
        total = await service.count_cards(
            user_id=user.id,
            status=status_filter,
            urgency=urgency_filter,
        )
        items = [CardResponse.model_validate(c) for c in cards]
        return CardListResponse(items=items, total=total, limit=limit, offset=offset)

    @router.post(
        "",
        response_model=CardResponse,
        status_code=status.HTTP_201_CREATED,
        summary="Create Card",
    )
    async def create_card(
        payload: CardCreateRequest,
        service: CardsRepository = Depends(get_cards_service),
        user: UserDTO = Depends(get_current_user),
    ) -> CardResponse:
        dto = CardCreateDTO(
            user_id=user.id,
            type=payload.type,
            urgency=payload.urgency,
            title=payload.title,
            summary=payload.summary,
            thread_id=payload.thread_id,
            due_at=payload.due_at,
            actions=[CardActionDTO(**a.model_dump()) for a in payload.actions],
        )
        created = await service.create_card(dto)
        return CardResponse.model_validate(created)

    @router.get(
        "/{card_id}",
        response_model=CardResponse,
        status_code=status.HTTP_200_OK,
        summary="Get Card Details",
    )
    async def get_card(
        card_id: UUID,
        service: CardsRepository = Depends(get_cards_service),
        _user: UserDTO = Depends(get_current_user),
    ) -> CardResponse:
        card = await service.get_card_by_id(card_id)
        if not card:
            raise NotFoundError(f"Card {card_id} not found")
        return CardResponse.model_validate(card)

    @router.patch(
        "/{card_id}",
        response_model=CardResponse,
        status_code=status.HTTP_200_OK,
        summary="Update Card",
    )
    async def update_card(
        card_id: UUID,
        payload: CardUpdateRequest,
        service: CardsRepository = Depends(get_cards_service),
        _user: UserDTO = Depends(get_current_user),
    ) -> CardResponse:
        updated = await service.update_card(
            card_id,
            CardUpdateDTO(
                status=payload.status,
                summary=payload.summary,
                due_at=payload.due_at,
            ),
        )
        if not updated:
            raise NotFoundError(f"Card {card_id} not found")
        return CardResponse.model_validate(updated)

    @router.post(
        "/{card_id}/feedback",
        response_model=CardResponse,
        status_code=status.HTTP_200_OK,
        summary="Record Card Feedback",
    )
    async def record_feedback(
        card_id: UUID,
        payload: CardFeedbackRequest,
        service: CardsRepository = Depends(get_cards_service),
        _user: UserDTO = Depends(get_current_user),
    ) -> CardResponse:
        card = await service.record_feedback(card_id, payload.feedback)
        if not card:
            raise NotFoundError(f"Card {card_id} not found")
        return CardResponse.model_validate(card)

    @router.post(
        "/{card_id}/dismiss",
        response_model=CardResponse,
        status_code=status.HTTP_200_OK,
        summary="Dismiss Card",
    )
    async def dismiss_card(
        card_id: UUID,
        service: CardsRepository = Depends(get_cards_service),
        _user: UserDTO = Depends(get_current_user),
    ) -> CardResponse:
        card = await service.update_card(card_id, CardUpdateDTO(status="dismissed"))
        if not card:
            raise NotFoundError(f"Card {card_id} not found")
        return CardResponse.model_validate(card)

    return router
