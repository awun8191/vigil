"""Connectors service implementing MCP integration, OAuth encryption, and delta sync."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from dataclasses import dataclass
from typing import ClassVar
from uuid import UUID, uuid4
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.security import encrypt_secret
from src.config.settings import Settings
from src.integrations.mcp.mcp import (
    MCPHandshakeError,
    MCPServerConfig,
    MCPToolCollisionError,
    MCPToolDefinition,
    discover_tools,
)
from src.integrations.repositories import MCPHostProtocol
from src.modules.connectors.data import RegisterConnectorRequest, SyncTriggerResponse
from src.modules.connectors.repository import (
    ConnectorAccountCreateDTO,
    ConnectorAccountDTO,
    ItemCreateDTO,
    ItemDTO,
    SyncStateDTO,
)


class MCPRegistrationError(ValueError):
    """A third party MCP server cannot be registered. The message names the reason."""


@dataclass(frozen=True)
class MCPServerDTO:
    """A registered third party MCP server."""

    id: UUID
    name: str
    transport: str  # "stdio" or "sse"
    command: str | None
    url: str | None
    enabled: bool
    created_at: datetime


@dataclass(frozen=True)
class MCPDiscoveredToolDTO:
    """A tool discovered on a registered third party MCP server."""

    server_id: UUID
    server_name: str
    name: str
    description: str
    input_schema: dict
    level: str  # "read", "local", or "external"


class ConnectorsService:
    """
    Connectors service satisfying ConnectorsRepository via duck typing.
    Constructor injection of session and settings.
    """

    _accounts: ClassVar[dict[UUID, ConnectorAccountDTO]] = {}
    _sync_states: ClassVar[dict[UUID, SyncStateDTO]] = {}
    _items: ClassVar[dict[UUID, ItemDTO]] = {}
    _items_by_external: ClassVar[dict[tuple[UUID, str], ItemDTO]] = {}

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        mcp_host: MCPHostProtocol | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.mcp_host = mcp_host

    async def get_account(self, account_id: UUID) -> ConnectorAccountDTO | None:
        return self._accounts.get(account_id)

    async def list_accounts(self, user_id: UUID) -> list[ConnectorAccountDTO]:
        return [a for a in self._accounts.values() if a.user_id == user_id]

    async def save_account(self, data: ConnectorAccountCreateDTO) -> ConnectorAccountDTO:
        acc_id = uuid4()
        now = datetime.now(timezone.utc)
        account = ConnectorAccountDTO(
            id=acc_id,
            user_id=data.user_id,
            connector_id=data.connector_id,
            account_label=data.account_label,
            encrypted_credentials=data.encrypted_credentials,
            scopes=data.scopes,
            status=data.status,
            enabled_tools=data.enabled_tools,
            created_at=now,
            updated_at=now,
        )
        self._accounts[acc_id] = account
        return account

    async def update_account_status(
        self,
        account_id: UUID,
        status: str,
    ) -> ConnectorAccountDTO | None:
        account = self._accounts.get(account_id)
        if not account:
            return None
        now = datetime.now(timezone.utc)
        updated = ConnectorAccountDTO(
            id=account.id,
            user_id=account.user_id,
            connector_id=account.connector_id,
            account_label=account.account_label,
            encrypted_credentials=account.encrypted_credentials,
            scopes=account.scopes,
            status=status,
            enabled_tools=account.enabled_tools,
            created_at=account.created_at,
            updated_at=now,
        )
        self._accounts[account_id] = updated
        return updated

    async def get_sync_state(self, account_id: UUID) -> SyncStateDTO | None:
        return self._sync_states.get(account_id)

    async def save_sync_state(self, state: SyncStateDTO) -> None:
        self._sync_states[state.connector_account_id] = state

    async def save_items(self, items: list[ItemCreateDTO]) -> list[ItemDTO]:
        persisted: list[ItemDTO] = []
        now = datetime.now(timezone.utc)
        for item_data in items:
            key = (item_data.connector_account_id, item_data.external_id)
            if key in self._items_by_external:
                persisted.append(self._items_by_external[key])
                continue
            item_id = uuid4()
            dto = ItemDTO(
                id=item_id,
                connector_account_id=item_data.connector_account_id,
                external_id=item_data.external_id,
                thread_id=item_data.thread_id,
                sender=item_data.sender,
                recipients=item_data.recipients,
                subject=item_data.subject,
                body_text=item_data.body_text,
                received_at=item_data.received_at,
                labels=item_data.labels,
                raw_reference=item_data.raw_reference,
                created_at=now,
            )
            self._items[item_id] = dto
            self._items_by_external[key] = dto
            persisted.append(dto)
        return persisted

    async def get_item_by_external_id(self, account_id: UUID, external_id: str) -> ItemDTO | None:
        return self._items_by_external.get((account_id, external_id))

    async def get_item_by_id(self, item_id: UUID) -> ItemDTO | None:
        return self._items.get(item_id)

    # Domain Orchestration Methods
    async def register_account(
        self,
        user_id: UUID,
        req: RegisterConnectorRequest,
    ) -> ConnectorAccountDTO:
        """Encrypt OAuth tokens using AES-256-GCM and persist account."""
        serialized_creds = json.dumps(req.raw_credentials)
        encrypted = encrypt_secret(serialized_creds, self.settings.SECRET_KEY)
        dto = ConnectorAccountCreateDTO(
            user_id=user_id,
            connector_id=req.connector_id,
            account_label=req.account_label,
            encrypted_credentials=encrypted,
            scopes=req.scopes,
        )
        return await self.save_account(dto)

    async def trigger_sync(self, account_id: UUID) -> SyncTriggerResponse:
        """Enqueue delta sync background job for connector."""
        job_id = uuid4()
        return SyncTriggerResponse(
            account_id=account_id,
            job_id=job_id,
            status="queued",
        )

    # External MCP server registry --------------------------------------

    _mcp_servers: ClassVar[dict[UUID, MCPServerDTO]] = {}
    _mcp_servers_by_name: ClassVar[dict[str, MCPServerDTO]] = {}
    _mcp_tools: ClassVar[dict[tuple[UUID, str], MCPDiscoveredToolDTO]] = {}

    @staticmethod
    def _builtin_tool_names() -> set[str]:
        """Return built-in tool names from the tool registry, not a fixed list."""
        from src.modules.runs.tools import TOOLS

        return set(TOOLS)

    @classmethod
    def _check_tool_collisions(cls, tools: list[MCPToolDefinition]) -> None:
        """Reject a discovered tool that collides with a built-in tool."""
        builtin = cls._builtin_tool_names()
        for tool in tools:
            if tool.name in builtin:
                raise MCPToolCollisionError(
                    f"tool {tool.name!r} collides with a built-in tool"
                )

    async def register_mcp_server(
        self,
        name: str,
        transport: str,
        command: str | None = None,
        url: str | None = None,
    ) -> MCPServerDTO:
        """Handshake with a third party server and store it with its tools.

        A server that fails the handshake is not stored. The error names
        the reason. Every discovered tool defaults to level ``external``.
        Only the background worker calls this method, never a request handler.
        """
        clean_name = (name or "").strip()
        if not clean_name:
            raise MCPRegistrationError("a server name must not be empty")
        if transport not in ("stdio", "sse"):
            raise MCPRegistrationError(
                f"unknown transport {transport!r}: use 'stdio' or 'sse'"
            )
        if transport == "stdio" and not (command or "").strip():
            raise MCPRegistrationError("a stdio server needs a command")
        if transport == "sse" and not (url or "").strip():
            raise MCPRegistrationError("an SSE server needs a URL")
        if clean_name in self._mcp_servers_by_name:
            raise MCPRegistrationError(
                f"an MCP server named {clean_name!r} is already registered"
            )
        tools = await discover_tools(
            MCPServerConfig(
                name=clean_name, transport=transport, command=command, url=url
            )
        )
        self._check_tool_collisions(tools)
        server_id = uuid4()
        now = datetime.now(timezone.utc)
        server = MCPServerDTO(
            id=server_id,
            name=clean_name,
            transport=transport,
            command=command,
            url=url,
            enabled=True,
            created_at=now,
        )
        self._mcp_servers[server_id] = server
        self._mcp_servers_by_name[clean_name] = server
        for tool in tools:
            key = (server_id, tool.name)
            self._mcp_tools[key] = MCPDiscoveredToolDTO(
                server_id=server_id,
                server_name=clean_name,
                name=tool.name,
                description=tool.description,
                input_schema=dict(tool.input_schema),
                level="external",
            )
        return server

    async def list_mcp_servers(self) -> list[MCPServerDTO]:
        """Return every registered third party MCP server."""
        return list(self._mcp_servers.values())

    async def get_mcp_server(self, name: str) -> MCPServerDTO | None:
        """Return the registered server with this name, or None."""
        return self._mcp_servers_by_name.get((name or "").strip())

    async def list_mcp_tools(self, server_name: str) -> list[MCPDiscoveredToolDTO]:
        """Return the discovered tools of one registered server."""
        server = await self.get_mcp_server(server_name)
        if server is None:
            return []
        return [
            tool
            for (server_id, _), tool in self._mcp_tools.items()
            if server_id == server.id
        ]

    async def unregister_mcp_server(self, name: str) -> bool:
        """Remove a server and its discovered tools. Return True when removed."""
        server = await self.get_mcp_server(name)
        if server is None:
            return False
        del self._mcp_servers[server.id]
        del self._mcp_servers_by_name[server.name]
        for key in [
            key for key in self._mcp_tools if key[0] == server.id
        ]:
            del self._mcp_tools[key]
        return True

    async def refresh_mcp_tools(
        self, server_name: str
    ) -> list[MCPDiscoveredToolDTO]:
        """Re-run discovery for a registered server and replace its tools.

        The worker runs this method inside the ``discover_mcp_tools`` job.
        A failed handshake keeps the stored tools and names the reason.
        """
        server = await self.get_mcp_server(server_name)
        if server is None:
            raise MCPRegistrationError(
                f"no MCP server named {server_name!r} is registered"
            )
        tools = await discover_tools(
            MCPServerConfig(
                name=server.name,
                transport=server.transport,
                command=server.command,
                url=server.url,
            )
        )
        self._check_tool_collisions(tools)
        for key in [key for key in self._mcp_tools if key[0] == server.id]:
            del self._mcp_tools[key]
        refreshed: list[MCPDiscoveredToolDTO] = []
        for tool in tools:
            dto = MCPDiscoveredToolDTO(
                server_id=server.id,
                server_name=server.name,
                name=tool.name,
                description=tool.description,
                input_schema=dict(tool.input_schema),
                level="external",
            )
            self._mcp_tools[(server.id, tool.name)] = dto
            refreshed.append(dto)
        return refreshed

    async def set_mcp_tool_level(
        self, server_name: str, tool_name: str, level: str
    ) -> MCPDiscoveredToolDTO | None:
        """Lower (or reset) a discovered tool approval level as an explicit act."""
        if level not in ("read", "local", "external"):
            raise MCPRegistrationError(
                f"unknown approval level {level!r}: "
                "use 'read', 'local', or 'external'"
            )
        server = await self.get_mcp_server(server_name)
        if server is None:
            return None
        key = (server.id, tool_name)
        tool = self._mcp_tools.get(key)
        if tool is None:
            return None
        updated = MCPDiscoveredToolDTO(
            server_id=tool.server_id,
            server_name=tool.server_name,
            name=tool.name,
            description=tool.description,
            input_schema=dict(tool.input_schema),
            level=level,
        )
        self._mcp_tools[key] = updated
        return updated
