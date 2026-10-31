"""Connectors router factory providing MCP connector management and sync triggers."""
from uuid import UUID
from fastapi import APIRouter, Depends, status

from src.config.exceptions import NotFoundError
from src.config.settings import Settings
from src.dependencies import get_connectors_service, get_current_user
from src.modules.auth.repository import UserDTO
from src.modules.connectors.data import (
    ConnectorAccountResponse,
    ItemResponse,
    RegisterConnectorRequest,
    SyncTriggerResponse,
)
from src.modules.connectors.repository import ConnectorsRepository


def build_connectors_router(settings: Settings) -> APIRouter:
    """
    Build and configure connectors APIRouter instance.
    Endpoints:
      - GET /api/v1/connectors/accounts
      - POST /api/v1/connectors/accounts
      - GET /api/v1/connectors/accounts/{id}
      - POST /api/v1/connectors/accounts/{id}/sync
      - GET /api/v1/connectors/accounts/{id}/items
    """
    router = APIRouter(prefix="/connectors", tags=["Connectors"])

    @router.get(
        "/accounts",
        response_model=list[ConnectorAccountResponse],
        status_code=status.HTTP_200_OK,
        summary="List User Connector Accounts",
    )
    async def list_accounts(
        service: ConnectorsRepository = Depends(get_connectors_service),
        user: UserDTO = Depends(get_current_user),
    ) -> list[ConnectorAccountResponse]:
        accounts = await service.list_accounts(user.id)
        return [ConnectorAccountResponse.model_validate(a) for a in accounts]

    @router.post(
        "/accounts",
        response_model=ConnectorAccountResponse,
        status_code=status.HTTP_201_CREATED,
        summary="Register Connector Account",
    )
    async def register_account(
        payload: RegisterConnectorRequest,
        service: ConnectorsRepository = Depends(get_connectors_service),
        user: UserDTO = Depends(get_current_user),
    ) -> ConnectorAccountResponse:
        account = await service.register_account(user.id, payload)  # type: ignore[attr-defined]
        return ConnectorAccountResponse.model_validate(account)

    @router.get(
        "/accounts/{account_id}",
        response_model=ConnectorAccountResponse,
        status_code=status.HTTP_200_OK,
        summary="Get Account Details",
    )
    async def get_account(
        account_id: UUID,
        service: ConnectorsRepository = Depends(get_connectors_service),
        _user: UserDTO = Depends(get_current_user),
    ) -> ConnectorAccountResponse:
        account = await service.get_account(account_id)
        if not account:
            raise NotFoundError(f"Connector account {account_id} not found")
        return ConnectorAccountResponse.model_validate(account)

    @router.post(
        "/accounts/{account_id}/sync",
        response_model=SyncTriggerResponse,
        status_code=status.HTTP_200_OK,
        summary="Trigger Delta Sync",
    )
    async def trigger_sync(
        account_id: UUID,
        service: ConnectorsRepository = Depends(get_connectors_service),
        _user: UserDTO = Depends(get_current_user),
    ) -> SyncTriggerResponse:
        return await service.trigger_sync(account_id)  # type: ignore[attr-defined]

    @router.get(
        "/accounts/{account_id}/items",
        response_model=list[ItemResponse],
        status_code=status.HTTP_200_OK,
        summary="List Ingested Items",
    )
    async def list_items(
        account_id: UUID,
        service: ConnectorsRepository = Depends(get_connectors_service),
        _user: UserDTO = Depends(get_current_user),
    ) -> list[ItemResponse]:
        return []

    return router
