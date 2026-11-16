"""Connectors router factory providing MCP connector management and sync triggers."""
from datetime import datetime
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from src.config.exceptions import ConflictError, NotFoundError, ValidationError
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


class GoogleAuthorizeResponse(BaseModel):
    """Outbound OAuth2 authorization URL for Google Workspace connectors."""

    authorization_url: str


class MCPToolLevelUpdateRequest(BaseModel):
    """Inbound request to update an MCP tool's approval level."""

    level: Literal["read", "local", "external"]


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
    input_schema: dict = Field(default_factory=dict)


class UpdateMCPToolLevelRequest(BaseModel):
    """Inbound request to update the approval policy level of a discovered MCP tool."""

    level: Literal["read", "local", "external"]


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
        "/google/authorize",
        response_model=GoogleAuthorizeResponse,
        status_code=status.HTTP_200_OK,
        summary="Get Google OAuth2 Authorization URL",
    )
    async def google_authorize(
        connector_id: str = Query(default="gmail"),
        user: UserDTO = Depends(get_current_user),
    ) -> GoogleAuthorizeResponse:
        if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
            raise ValidationError(
                "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be configured on the server."
            )
        from connectors.gmail.auth import GmailAuthManager

        auth_mgr = GmailAuthManager(settings.SECRET_KEY)
        state = auth_mgr.create_oauth_state(str(user.id), connector_id=connector_id)
        url = auth_mgr.get_authorization_url(
            client_id=settings.GOOGLE_CLIENT_ID,
            redirect_uri=settings.GOOGLE_REDIRECT_URI,
            state=state,
        )
        return GoogleAuthorizeResponse(authorization_url=url)

    @router.get(
        "/google/callback",
        status_code=status.HTTP_302_FOUND,
        summary="Google OAuth2 Authorization Callback",
    )
    async def google_callback(
        code: str | None = Query(default=None),
        state: str | None = Query(default=None),
        error: str | None = Query(default=None),
        service: ConnectorsRepository = Depends(get_connectors_service),
    ) -> RedirectResponse:
        frontend_redirect = "/agent/settings/connectors"
        if error or not code or not state:
            reason = error or "missing_code_or_state"
            return RedirectResponse(url=f"{frontend_redirect}?oauth_error={reason}", status_code=302)

        from connectors.gmail.auth import GMAIL_SCOPES, GmailAuthManager

        auth_mgr = GmailAuthManager(settings.SECRET_KEY)
        try:
            payload = auth_mgr.verify_oauth_state(state)
            user_id = UUID(str(payload["uid"]))
            connector_id = str(payload.get("cid") or "gmail")
        except Exception as exc:
            return RedirectResponse(url=f"{frontend_redirect}?oauth_error={exc}", status_code=302)

        creds = await auth_mgr.exchange_code_for_tokens(
            code=code,
            client_id=settings.GOOGLE_CLIENT_ID,
            client_secret=settings.GOOGLE_CLIENT_SECRET,
            redirect_uri=settings.GOOGLE_REDIRECT_URI,
        )
        account_label = creds.email or f"{connector_id}-account"
        account = await service.register_account(  # type: ignore[attr-defined]
            user_id,
            RegisterConnectorRequest(
                connector_id=connector_id,
                account_label=account_label,
                raw_credentials=creds.to_dict(),
                scopes=list(GMAIL_SCOPES),
            ),
        )
        try:
            await service.trigger_sync(account.id)  # type: ignore[attr-defined]
        except Exception:
            pass
        return RedirectResponse(url=f"{frontend_redirect}?connected={connector_id}", status_code=302)

    @router.delete(
        "/accounts/{account_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        summary="Disconnect Connector Account",
    )
    async def delete_account(
        account_id: UUID,
        service: ConnectorsRepository = Depends(get_connectors_service),
        user: UserDTO = Depends(get_current_user),
    ) -> None:
        deleted = await service.delete_account(account_id, user.id)  # type: ignore[attr-defined]
        if not deleted:
            raise NotFoundError(f"Connector account {account_id} not found")

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
        items = await service.list_items(account_id)  # type: ignore[attr-defined]
        return [ItemResponse.model_validate(item) for item in items]

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
        user: UserDTO = Depends(get_current_user),
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
                    "user_id": str(user.id),
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

    @router.get(
        "/mcp-servers/{name}/tools",
        response_model=list[MCPToolResponse],
        status_code=status.HTTP_200_OK,
        summary="List Tools of an External MCP Server",
    )
    async def list_mcp_server_tools(
        name: str,
        service: ConnectorsRepository = Depends(get_connectors_service),
        _user: UserDTO = Depends(get_current_user),
    ) -> list[MCPToolResponse]:
        server = await service.get_mcp_server(name)  # type: ignore[attr-defined]
        if server is None:
            raise NotFoundError(f"MCP server {name!r} not found")
        tools = await service.list_mcp_tools(name)  # type: ignore[attr-defined]
        return [MCPToolResponse.model_validate(t) for t in tools]

    @router.patch(
        "/mcp-servers/{name}/tools/{tool_name}",
        response_model=MCPToolResponse,
        status_code=status.HTTP_200_OK,
        summary="Update Approval Level of a Discovered MCP Tool",
    )
    async def update_mcp_tool_level(
        name: str,
        tool_name: str,
        payload: UpdateMCPToolLevelRequest,
        service: ConnectorsRepository = Depends(get_connectors_service),
        _user: UserDTO = Depends(get_current_user),
    ) -> MCPToolResponse:
        updated = await service.set_mcp_tool_level(  # type: ignore[attr-defined]
            name, tool_name, payload.level
        )
        if updated is None:
            raise NotFoundError(
                f"MCP tool {tool_name!r} on server {name!r} not found"
            )
        return MCPToolResponse.model_validate(updated)

    @router.post(
        "/mcp-servers/{name}/refresh",
        response_model=MCPDiscoveryResponse,
        status_code=status.HTTP_202_ACCEPTED,
        summary="Refresh Tools of an External MCP Server",
    )
    async def refresh_mcp_server_tools(
        name: str,
        service: ConnectorsRepository = Depends(get_connectors_service),
        jobs: JobsRepository = Depends(get_jobs_service),
        user: UserDTO = Depends(get_current_user),
    ) -> MCPDiscoveryResponse:
        server = await service.get_mcp_server(name)  # type: ignore[attr-defined]
        if server is None:
            raise NotFoundError(f"MCP server {name!r} not found")
        job = await jobs.enqueue(
            JobCreateDTO(
                type="discover_mcp_tools",
                payload={
                    "name": server.name,
                    "transport": server.transport,
                    "command": server.command,
                    "url": server.url,
                    "user_id": str(user.id),
                },
            )
        )
        return MCPDiscoveryResponse(
            server_name=server.name, job_id=job.id, status="queued"
        )

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

    @router.get(
        "/mcp-servers/{name}/tools",
        response_model=list[MCPToolResponse],
        status_code=status.HTTP_200_OK,
        summary="List Discovered Tools of an MCP Server",
    )
    async def list_mcp_server_tools(
        name: str,
        service: ConnectorsRepository = Depends(get_connectors_service),
        _user: UserDTO = Depends(get_current_user),
    ) -> list[MCPToolResponse]:
        tools = await service.list_mcp_tools(name)  # type: ignore[attr-defined]
        return [MCPToolResponse.model_validate(t) for t in tools]

    @router.patch(
        "/mcp-servers/{name}/tools/{tool_name}",
        response_model=MCPToolResponse,
        status_code=status.HTTP_200_OK,
        summary="Update MCP Tool Approval Level",
    )
    async def update_mcp_tool_level(
        name: str,
        tool_name: str,
        payload: MCPToolLevelUpdateRequest,
        service: ConnectorsRepository = Depends(get_connectors_service),
        _user: UserDTO = Depends(get_current_user),
    ) -> MCPToolResponse:
        updated = await service.set_mcp_tool_level(name, tool_name, payload.level)  # type: ignore[attr-defined]
        if updated is None:
            raise NotFoundError(f"MCP tool {tool_name!r} on server {name!r} not found")
        return MCPToolResponse.model_validate(updated)

    return router
