"""Connector stores: durable SQL for linked accounts and MCP servers.

The service keeps its orchestration and the store swaps underneath it, exactly
like the auth and chat modules: SQL over the request database session, and
process-local dicts when the service is built without a session (tests and the
CLI harness). A restart no longer loses a linked account or a registered MCP
server, because the rows live in ``connected_accounts`` and ``mcp_servers``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.connectors.repository import (
    ConnectorAccountCreateDTO,
    ConnectorAccountDTO,
    MCPServerDTO,
)
from src.schemas.connectors import ConnectedAccountModel, MCPServerModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


@runtime_checkable
class ConnectorStore(Protocol):
    """Storage contract behind ConnectorsService for accounts and MCP servers."""

    async def get_account(self, account_id: UUID) -> ConnectorAccountDTO | None: ...
    async def list_accounts(self, user_id: UUID) -> list[ConnectorAccountDTO]: ...
    async def save_account(self, data: ConnectorAccountCreateDTO) -> ConnectorAccountDTO: ...
    async def update_account_status(
        self, account_id: UUID, status: str
    ) -> ConnectorAccountDTO | None: ...
    async def register_server(self, server: MCPServerDTO) -> MCPServerDTO: ...
    async def get_server(self, name: str) -> MCPServerDTO | None: ...
    async def list_servers(self) -> list[MCPServerDTO]: ...
    async def delete_server(self, name: str) -> bool: ...


class InMemoryConnectorStore:
    """Process-local store for accounts and MCP servers, owned by one instance.

    Every store owns its own rows. Nothing is held in class attributes, so a
    service built without a session cannot read a row that another such service
    wrote, and one test cannot see the registration another test left behind.
    That is what lets two sessionless services register the same server name
    without tripping over each other.
    """

    def __init__(self) -> None:
        self.accounts: dict[UUID, ConnectorAccountDTO] = {}
        self.servers: dict[UUID, MCPServerDTO] = {}
        self.servers_by_name: dict[str, MCPServerDTO] = {}

    async def get_account(self, account_id: UUID) -> ConnectorAccountDTO | None:
        return self.accounts.get(account_id)

    async def list_accounts(self, user_id: UUID) -> list[ConnectorAccountDTO]:
        return [
            account
            for account in self.accounts.values()
            if account.user_id == user_id
        ]

    async def save_account(self, data: ConnectorAccountCreateDTO) -> ConnectorAccountDTO:
        now = _now()
        account = ConnectorAccountDTO(
            id=uuid4(),
            user_id=data.user_id,
            connector_id=data.connector_id,
            account_label=data.account_label,
            encrypted_credentials=data.encrypted_credentials,
            scopes=list(data.scopes),
            status=data.status,
            enabled_tools=list(data.enabled_tools),
            created_at=now,
            updated_at=now,
        )
        self.accounts[account.id] = account
        return account

    async def update_account_status(
        self,
        account_id: UUID,
        status: str,
    ) -> ConnectorAccountDTO | None:
        account = self.accounts.get(account_id)
        if account is None:
            return None
        updated = ConnectorAccountDTO(
            id=account.id,
            user_id=account.user_id,
            connector_id=account.connector_id,
            account_label=account.account_label,
            encrypted_credentials=account.encrypted_credentials,
            scopes=list(account.scopes),
            status=status,
            enabled_tools=list(account.enabled_tools),
            created_at=account.created_at,
            updated_at=_now(),
        )
        self.accounts[account_id] = updated
        return updated

    async def register_server(self, server: MCPServerDTO) -> MCPServerDTO:
        self.servers[server.id] = server
        self.servers_by_name[server.name] = server
        return server

    async def get_server(self, name: str) -> MCPServerDTO | None:
        return self.servers_by_name.get(name)

    async def list_servers(self) -> list[MCPServerDTO]:
        return list(self.servers.values())

    async def delete_server(self, name: str) -> bool:
        server = self.servers_by_name.pop(name, None)
        if server is None:
            return False
        self.servers.pop(server.id, None)
        return True


class SqlConnectorStore:
    """Durable store over the ``connected_accounts`` and ``mcp_servers`` tables.

    The credential blob is written and read back unchanged. Nothing here logs
    it, and the response DTO already excludes it.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _account_dto(model: ConnectedAccountModel) -> ConnectorAccountDTO:
        return ConnectorAccountDTO(
            id=model.id,
            user_id=model.user_id,
            connector_id=model.connector_id,
            account_label=model.account_label,
            encrypted_credentials=model.encrypted_credentials,
            scopes=list(model.scopes),
            status=model.status,
            # Tool enablement lives in connector_tools; this column set has no
            # enabled_tools field on the account row.
            enabled_tools=[],
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    @staticmethod
    def _server_dto(model: MCPServerModel) -> MCPServerDTO:
        return MCPServerDTO(
            id=model.id,
            name=model.name,
            transport=model.transport,
            command=model.command,
            url=model.url,
            enabled=model.enabled,
            created_at=model.created_at,
        )

    async def get_account(self, account_id: UUID) -> ConnectorAccountDTO | None:
        model = await self.session.get(ConnectedAccountModel, account_id)
        return None if model is None else self._account_dto(model)

    async def list_accounts(self, user_id: UUID) -> list[ConnectorAccountDTO]:
        result = await self.session.execute(
            select(ConnectedAccountModel)
            .where(ConnectedAccountModel.user_id == user_id)
            .order_by(
                ConnectedAccountModel.created_at.asc(),
                ConnectedAccountModel.id.asc(),
            )
        )
        return [self._account_dto(model) for model in result.scalars().all()]

    async def save_account(self, data: ConnectorAccountCreateDTO) -> ConnectorAccountDTO:
        """Insert the account, or refresh the row with the same user, kind and label.

        The table's unique key makes a reconnect the same account, not a second
        one: the new credential blob and scopes replace the old ones and the
        account id stays put.
        """
        now = _now()
        statement = (
            pg_insert(ConnectedAccountModel)
            .values(
                id=uuid4(),
                user_id=data.user_id,
                connector_id=data.connector_id,
                account_label=data.account_label,
                encrypted_credentials=data.encrypted_credentials,
                scopes=list(data.scopes),
                status=data.status,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                constraint="uq_connected_accounts_user_conn_label",
                set_={
                    "encrypted_credentials": data.encrypted_credentials,
                    "scopes": list(data.scopes),
                    "status": data.status,
                    "updated_at": now,
                },
            )
            .returning(ConnectedAccountModel.id)
        )
        account_id = (await self.session.execute(statement)).scalar_one()
        await self.session.commit()
        model = await self.session.get(ConnectedAccountModel, account_id)
        if model is None:
            # The upsert returned an id and committed, so the row must be
            # readable. Nothing sensible is left to return if it is not.
            raise RuntimeError(
                f"connected account {account_id} is missing after a successful upsert"
            )
        return self._account_dto(model)

    async def update_account_status(
        self,
        account_id: UUID,
        status: str,
    ) -> ConnectorAccountDTO | None:
        model = await self.session.get(ConnectedAccountModel, account_id)
        if model is None:
            return None
        model.status = status
        model.updated_at = _now()
        await self.session.flush()
        await self.session.commit()
        return self._account_dto(model)

    async def register_server(self, server: MCPServerDTO) -> MCPServerDTO:
        model = MCPServerModel(
            id=server.id,
            name=server.name,
            transport=server.transport,
            command=server.command,
            url=server.url,
            enabled=server.enabled,
            created_at=server.created_at,
        )
        self.session.add(model)
        await self.session.flush()
        await self.session.commit()
        return self._server_dto(model)

    async def get_server(self, name: str) -> MCPServerDTO | None:
        result = await self.session.execute(
            select(MCPServerModel).where(MCPServerModel.name == name)
        )
        model = result.scalars().first()
        return None if model is None else self._server_dto(model)

    async def list_servers(self) -> list[MCPServerDTO]:
        result = await self.session.execute(
            select(MCPServerModel).order_by(
                MCPServerModel.created_at.asc(), MCPServerModel.id.asc()
            )
        )
        return [self._server_dto(model) for model in result.scalars().all()]

    async def delete_server(self, name: str) -> bool:
        result = await self.session.execute(
            select(MCPServerModel).where(MCPServerModel.name == name)
        )
        model = result.scalars().first()
        if model is None:
            return False
        await self.session.delete(model)
        await self.session.flush()
        await self.session.commit()
        return True


__all__ = [
    "ConnectorStore",
    "InMemoryConnectorStore",
    "SqlConnectorStore",
]
