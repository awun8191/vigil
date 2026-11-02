"""Connectors router factory providing MCP connector management and sync triggers."""
from datetime import datetime
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field

from src.config.exceptions import ConflictError, NotFoundError
from src.config.settings import Settings
from src.dependencies import get_connectors_service, get_current_user, get_jobs_service
from src.modules.auth.repository import UserDTO
from src.modules.connectors.data import (
    ConnectorAccountResponse,
    ItemResponse,
    RegisterConnectorRequest,
    SyncTriggerResponse,
)
from src.modules.connectors.repository import ConnectorsRepository
from src.modules.jobs.repository import JobCreateDTO, JobsRepository


class RegisterMCPServerRequest(BaseModel):
    """Inbound request to attach a third party MCP server."""

    name: str = Field(..., min_length=1, max_length=64)
    transport: Literal["stdio", "sse"] = Field(...)
    command: str | None = Field(default=None)
    url: str | None = Field(default=None)


class MCPServerResponse(BaseModel):
    """Outbound representation of a registered third party MCP server."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    transport: str
    command: str | None = None
    url: str | None = None
    enabled: bool
    created_at: datetime


class MCPToolResponse(BaseModel):
    """Outbound representation of a discovered third party tool."""

    model_config = ConfigDict(from_attributes=True)

    server_name: str
    name: str
    description: str
    level: str


class MCPDiscoveryResponse(BaseModel):
    """Outbound confirmation that tool discovery was dispatched."""

    server_name: str
    job_id: UUID
    status: str


def build_connectors_router(settings: Settings) -> APIRouter:
    """
    Build and configure connectors APIRouter instance.
    Endpoints:
      - GET /api/v1/connectors/accounts
      - POST /api/v1/connectors/accounts
      - GET /api/v1/connectors/accounts/{id}
      - POST /api/v1/connectors/accounts/{id}/sync
      - GET /api/v1/connectors/accounts/{id}/items
      - POST /api/v1/connectors/mcp-servers (202, enqueues discover_mcp_tools)
      - GET /api/v1/connectors/mcp-servers
      - DELETE /api/v1/connectors/mcp-servers/{name}
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

    @router.post(
        "/mcp-servers",
        response_model=MCPDiscoveryResponse,
        status_code=status.HTTP_202_ACCEPTED,
        summary="Register External MCP Server",
    )
    async def register_mcp_server(
        payload: RegisterMCPServerRequest,
        service: ConnectorsRepository = Depends(get_connectors_service),
        jobs: JobsRepository = Depends(get_jobs_service),
        _user: UserDTO = Depends(get_current_user),
    ) -> MCPDiscoveryResponse:
        """Enqueue tool discovery for a third party server without running it.

        The handler never spawns a process. It validates the name and
        enqueues a ``discover_mcp_tools`` job. The worker runs the
        handshake there and stores the server only after it succeeds.
        """
        existing = await service.get_mcp_server(payload.name)  # type: ignore[attr-defined]
        if existing is not None:
            raise ConflictError(
                f"an MCP server named {payload.name!r} is already registered"
            )
        job = await jobs.enqueue(
            JobCreateDTO(
                type="discover_mcp_tools",
                payload={
                    "name": payload.name,
                    "transport": payload.transport,
                    "command": payload.command,
                    "url": payload.url,
                },
            )
        )
        return MCPDiscoveryResponse(
            server_name=payload.name, job_id=job.id, status="queued"
        )

    @router.get(
        "/mcp-servers",
        response_model=list[MCPServerResponse],
        status_code=status.HTTP_200_OK,
        summary="List External MCP Servers",
    )
    async def list_mcp_servers(
        service: ConnectorsRepository = Depends(get_connectors_service),
        _user: UserDTO = Depends(get_current_user),
    ) -> list[MCPServerResponse]:
        servers = await service.list_mcp_servers()  # type: ignore[attr-defined]
        return [MCPServerResponse.model_validate(s) for s in servers]

    @router.delete(
        "/mcp-servers/{name}",
        status_code=status.HTTP_204_NO_CONTENT,
        summary="Unregister External MCP Server",
    )
    async def unregister_mcp_server(
        name: str,
        service: ConnectorsRepository = Depends(get_connectors_service),
        _user: UserDTO = Depends(get_current_user),
    ) -> None:
        removed = await service.unregister_mcp_server(name)  # type: ignore[attr-defined]
        if not removed:
            raise NotFoundError(f"MCP server {name!r} not found")

    return router
