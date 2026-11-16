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
from src.modules.audit.repository import AuditRepository
from src.modules.audit.service import (
    ACTION_CONNECTOR_CONNECT,
    AuditService,
    record_event,
)
from src.modules.connectors.data import RegisterConnectorRequest, SyncTriggerResponse
from src.modules.connectors.errors import MCPRegistrationError
from src.modules.connectors.repository import (
    ConnectorAccountCreateDTO,
    ConnectorAccountDTO,
    ItemCreateDTO,
    ItemDTO,
    MCPServerDTO,
    SyncStateDTO,
)
from src.modules.connectors.state import (
    ConnectorStore,
    InMemoryConnectorStore,
    SqlConnectorStore,
)

# MCPRegistrationError is defined in errors.py so the store can raise it without
# importing this module, and it stays importable from here as it always was.


@dataclass(frozen=True)
class MCPDiscoveredToolDTO:
    """A tool discovered on a registered third party MCP server."""

    server_id: UUID
    server_name: str
    name: str
    description: str
    input_schema: dict
    level: str  # "read", "local", or "external"
    remote_name: str | None = None


class ConnectorsService:
    """
    Connectors service satisfying ConnectorsRepository via duck typing.

    The constructor picks the store: the SQL implementation over
    ``connected_accounts`` and ``mcp_servers`` on a request database session,
    the in-memory one when the service is built without a session (tests, the
    CLI harness). Accounts and MCP servers therefore survive a restart.

    The ClassVar dicts below are a read-through cache, not the source of
    truth. ``MCPHostClient`` in ``integrations/mcp/mcp.py`` reads
    ``_mcp_servers_by_name`` and ``_mcp_tools`` directly, so every store read
    and write refreshes them.
    """

    def __init__(
        self,
        session: AsyncSession | None,
        settings: Settings,
        mcp_host: MCPHostProtocol | None = None,
        audit: AuditRepository | None = None,
        repository: ConnectorStore | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.mcp_host = mcp_host
        self.audit = audit or AuditService(session=session, settings=settings)
        if repository is not None:
            self.repository: ConnectorStore = repository
        elif isinstance(session, AsyncSession):
            self.repository = SqlConnectorStore(session)
        else:
            self.repository = InMemoryConnectorStore()

    async def get_account(self, account_id: UUID) -> ConnectorAccountDTO | None:
        return await self.repository.get_account(account_id)

    async def list_accounts(self, user_id: UUID) -> list[ConnectorAccountDTO]:
        return await self.repository.list_accounts(user_id)

    async def list_active_accounts(self, connector_id: str | None = None) -> list[ConnectorAccountDTO]:
        return await self.repository.list_active_accounts(connector_id)

    async def save_account(self, data: ConnectorAccountCreateDTO) -> ConnectorAccountDTO:
        return await self.repository.save_account(data)

    async def update_account_status(
        self,
        account_id: UUID,
        status: str,
    ) -> ConnectorAccountDTO | None:
        return await self.repository.update_account_status(account_id, status)

    async def update_account_credentials(
        self,
        account_id: UUID,
        encrypted_credentials: str,
        status: str = "active",
    ) -> ConnectorAccountDTO | None:
        return await self.repository.update_account_credentials(
            account_id, encrypted_credentials, status=status
        )

    async def delete_account(self, account_id: UUID, user_id: UUID) -> bool:
        return await self.repository.delete_account(account_id, user_id)

    async def get_sync_state(self, account_id: UUID) -> SyncStateDTO | None:
        return await self.repository.get_sync_state(account_id)

    async def save_sync_state(self, state: SyncStateDTO) -> None:
        await self.repository.save_sync_state(state)

    async def save_items(self, items: list[ItemCreateDTO]) -> list[ItemDTO]:
        return await self.repository.save_items(items)

    async def list_items(
        self, account_id: UUID, limit: int = 50, offset: int = 0
    ) -> list[ItemDTO]:
        return await self.repository.list_items(account_id, limit=limit, offset=offset)

    async def get_item_by_external_id(self, account_id: UUID, external_id: str) -> ItemDTO | None:
        return await self.repository.get_item_by_external_id(account_id, external_id)

    async def get_item_by_id(self, item_id: UUID) -> ItemDTO | None:
        return await self.repository.get_item_by_id(item_id)

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
        account = await self.save_account(dto)

        # The account is saved now. The audit row names the account and the
        # connector. It never carries the credentials or the ciphertext.
        # record_event logs a failure and returns, so the connect still succeeds.
        await record_event(
            self.audit,
            actor="user",
            action=ACTION_CONNECTOR_CONNECT,
            subject=str(account.id),
            details={
                "user_id": str(user_id),
                "connector_id": req.connector_id,
                "account_label": req.account_label,
                "scopes": list(req.scopes),
            },
        )
        return account

    async def trigger_sync(self, account_id: UUID) -> SyncTriggerResponse:
        """Enqueue delta sync background job for connector."""
        from src.modules.jobs.repository import JobCreateDTO
        from src.modules.jobs.service import JobsService

        jobs = JobsService(session=self.session, settings=self.settings)
        job = await jobs.enqueue(
            JobCreateDTO(
                type="poll_connector",
                payload={"account_id": str(account_id)},
            )
        )
        return SyncTriggerResponse(
            account_id=account_id,
            job_id=job.id,
            status="queued",
        )

    # External MCP server registry --------------------------------------

    _mcp_servers: ClassVar[dict[UUID, MCPServerDTO]] = {}
    _mcp_servers_by_name: ClassVar[dict[str, MCPServerDTO]] = {}
    _mcp_tools: ClassVar[dict[tuple[UUID, str], MCPDiscoveredToolDTO]] = {}

    @classmethod
    def _cache_server(cls, server: MCPServerDTO) -> None:
        """Publish one stored server and its persisted tools into the read-through cache."""
        cls._mcp_servers[server.id] = server
        cls._mcp_servers_by_name[server.name] = server
        if server.tools:
            for key in [k for k in cls._mcp_tools if k[0] == server.id]:
                del cls._mcp_tools[key]
            for raw in server.tools:
                if not isinstance(raw, dict):
                    continue
                tname = str(raw.get("name") or "").strip()
                if not tname:
                    continue
                cls._mcp_tools[(server.id, tname)] = MCPDiscoveredToolDTO(
                    server_id=server.id,
                    server_name=server.name,
                    name=tname,
                    description=str(raw.get("description") or ""),
                    input_schema=dict(raw.get("input_schema") or {}),
                    level=str(raw.get("level") or "external"),
                    remote_name=raw.get("remote_name"),
                )

    @classmethod
    def _forget_server(cls, server: MCPServerDTO) -> None:
        """Drop one server and its cached tools from the read-through cache."""
        cls._mcp_servers.pop(server.id, None)
        cls._mcp_servers_by_name.pop(server.name, None)
        for key in [key for key in cls._mcp_tools if key[0] == server.id]:
            del cls._mcp_tools[key]

    @classmethod
    def reset_mcp_cache(cls) -> None:
        """Empty the read-through cache mcp.py reads.

        The cache is process-local, so a test that simulates a restart, or one
        that needs a clean slate, empties it through this call rather than by
        poking three class attributes.
        """
        cls._mcp_servers.clear()
        cls._mcp_servers_by_name.clear()
        cls._mcp_tools.clear()

    @staticmethod
    def _builtin_tool_names() -> set[str]:
        """Return built-in tool names from the tool registry, not a fixed list."""
        from src.modules.runs.tools import TOOLS

        return set(TOOLS)

    @staticmethod
    def _browser_tool_names() -> set[str]:
        """Return built-in browser tool names that external browser MCP servers may alias."""
        from src.modules.runs.tools.browser import BROWSER_TOOLS

        return {tool.name for tool in BROWSER_TOOLS}

    @classmethod
    def _check_tool_collisions(cls, tools: list[MCPToolDefinition]) -> None:
        """Reject a discovered tool that collides with a core built-in tool."""
        builtin = cls._builtin_tool_names()
        for tool in tools:
            if tool.name in builtin:
                raise MCPToolCollisionError(
                    f"tool {tool.name!r} collides with a built-in tool"
                )

    @classmethod
    def _prepare_discovered_tools(
        cls,
        server_id: UUID,
        server_name: str,
        tools: list[MCPToolDefinition],
    ) -> list[MCPDiscoveredToolDTO]:
        """Validate collisions (aliasing colliding browser_* tools) and build DTOs."""
        builtin = cls._builtin_tool_names()
        browser_names = cls._browser_tool_names()
        dtos: list[MCPDiscoveredToolDTO] = []
        for tool in tools:
            resolved_name = tool.name
            remote_name = tool.remote_name
            if tool.name in builtin:
                if tool.name in browser_names:
                    resolved_name = f"{server_name}__{tool.name}"
                    remote_name = remote_name or tool.name
                else:
                    raise MCPToolCollisionError(
                        f"tool {tool.name!r} collides with a built-in tool"
                    )
            dtos.append(
                MCPDiscoveredToolDTO(
                    server_id=server_id,
                    server_name=server_name,
                    name=resolved_name,
                    description=tool.description,
                    input_schema=dict(tool.input_schema),
                    level=tool.level if tool.level in ("read", "local", "external") else "external",
                    remote_name=remote_name,
                )
            )
        return dtos

    @staticmethod
    def _serialize_tools(tools: list[MCPDiscoveredToolDTO]) -> list[dict]:
        """Serialize discovered tools into JSON-safe dictionaries for DB persistence."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": dict(t.input_schema),
                "level": t.level,
                **({"remote_name": t.remote_name} if t.remote_name else {}),
            }
            for t in tools
        ]

    async def register_mcp_server(
        self,
        name: str,
        transport: str,
        command: str | None = None,
        url: str | None = None,
        *,
        env: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
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
        if await self.get_mcp_server(clean_name) is not None:
            raise MCPRegistrationError(
                f"an MCP server named {clean_name!r} is already registered"
            )
        tools = await discover_tools(
            MCPServerConfig(
                name=clean_name,
                transport=transport,
                command=command,
                url=url,
                env=dict(env or {}),
                headers=dict(headers or {}),
            )
        )
        server_id = uuid4()
        prepared = self._prepare_discovered_tools(server_id, clean_name, tools)
        serialized = self._serialize_tools(prepared)
        now = datetime.now(timezone.utc)
        stored = await self.repository.register_server(
            MCPServerDTO(
                id=server_id,
                name=clean_name,
                transport=transport,
                command=command,
                url=url,
                enabled=True,
                created_at=now,
                tools=serialized,
                headers=dict(headers or {}),
            )
        )
        self._cache_server(stored)
        for dto in prepared:
            self._mcp_tools[(server_id, dto.name)] = dto
        return stored

    async def list_mcp_servers(self) -> list[MCPServerDTO]:
        """Return every registered third party MCP server."""
        servers = await self.repository.list_servers()
        live = {server.id for server in servers}
        for server in servers:
            self._cache_server(server)
        for server_id in [sid for sid in self._mcp_servers if sid not in live]:
            self._forget_server(self._mcp_servers[server_id])
        return servers

    async def get_mcp_server(self, name: str) -> MCPServerDTO | None:
        """Return the registered server with this name, or None."""
        clean_name = (name or "").strip()
        server = await self.repository.get_server(clean_name)
        if server is None:
            cached = self._mcp_servers_by_name.get(clean_name)
            if cached is not None:
                self._forget_server(cached)
            return None
        self._cache_server(server)
        return server

    async def list_mcp_tools(self, server_name: str) -> list[MCPDiscoveredToolDTO]:
        """Return the discovered tools of one registered server."""
        server = await self.get_mcp_server(server_name)
        if server is None:
            return []
        cached = [
            tool
            for (server_id, _), tool in self._mcp_tools.items()
            if server_id == server.id
        ]
        if not cached and server.tools:
            self._cache_server(server)
            cached = [
                tool
                for (server_id, _), tool in self._mcp_tools.items()
                if server_id == server.id
            ]
        return cached

    async def unregister_mcp_server(self, name: str) -> bool:
        """Remove a server and its discovered tools. Return True when removed."""
        clean_name = (name or "").strip()
        server = await self.get_mcp_server(clean_name)
        if server is None:
            cached = self._mcp_servers_by_name.get(clean_name)
            if cached is not None:
                self._forget_server(cached)
            return False
        removed = await self.repository.delete_server(server.name)
        if removed:
            self._forget_server(server)
        return removed

    async def refresh_mcp_tools(
        self,
        server_name: str,
        *,
        env: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
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
        merged_headers = dict(server.headers or {})
        if headers:
            merged_headers.update(headers)
        tools = await discover_tools(
            MCPServerConfig(
                name=server.name,
                transport=server.transport,
                command=server.command,
                url=server.url,
                env=dict(env or {}),
                headers=merged_headers,
            )
        )
        previous_levels = {
            tool.name: tool.level
            for (sid, _), tool in self._mcp_tools.items()
            if sid == server.id
        }
        prepared = self._prepare_discovered_tools(server.id, server.name, tools)
        refreshed: list[MCPDiscoveredToolDTO] = []
        for dto in prepared:
            kept_level = previous_levels.get(dto.name, dto.level)
            refreshed.append(
                MCPDiscoveredToolDTO(
                    server_id=dto.server_id,
                    server_name=dto.server_name,
                    name=dto.name,
                    description=dto.description,
                    input_schema=dto.input_schema,
                    level=kept_level,
                    remote_name=dto.remote_name,
                )
            )
        for key in [key for key in self._mcp_tools if key[0] == server.id]:
            del self._mcp_tools[key]
        for dto in refreshed:
            self._mcp_tools[(server.id, dto.name)] = dto
        updated = await self.repository.update_server_tools(
            server.name, self._serialize_tools(refreshed)
        )
        if updated is not None:
            self._cache_server(updated)
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
            remote_name=tool.remote_name,
        )
        self._mcp_tools[key] = updated
        server_tools = [
            dto
            for (sid, _), dto in self._mcp_tools.items()
            if sid == server.id
        ]
        saved = await self.repository.update_server_tools(
            server.name, self._serialize_tools(server_tools)
        )
        if saved is not None:
            self._cache_server(saved)
        return updated
