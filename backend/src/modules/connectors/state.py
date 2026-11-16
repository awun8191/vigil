"""Connector stores: durable SQL for linked accounts, sync state, items, and MCP servers.

The service keeps its orchestration and the store swaps underneath it, exactly
like the auth and chat modules: SQL over the request database session, and
process-local dicts when the service is built without a session (tests and the
CLI harness).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.connectors.errors import MCPRegistrationError
from src.modules.connectors.repository import (
    ConnectorAccountCreateDTO,
    ConnectorAccountDTO,
    ItemCreateDTO,
    ItemDTO,
    MCPServerDTO,
    SyncStateDTO,
)
from src.schemas.connectors import (
    ConnectedAccountModel,
    ConnectorItemModel,
    ConnectorSyncStateModel,
    MCPServerModel,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@runtime_checkable
class ConnectorStore(Protocol):
    """Storage contract behind ConnectorsService for accounts, sync state, items, and MCP servers."""

    async def get_account(self, account_id: UUID) -> ConnectorAccountDTO | None: ...
    async def list_accounts(self, user_id: UUID) -> list[ConnectorAccountDTO]: ...
    async def list_active_accounts(self, connector_id: str | None = None) -> list[ConnectorAccountDTO]: ...
    async def save_account(self, data: ConnectorAccountCreateDTO) -> ConnectorAccountDTO: ...
    async def update_account_status(
        self, account_id: UUID, status: str
    ) -> ConnectorAccountDTO | None: ...
    async def update_account_credentials(
        self, account_id: UUID, encrypted_credentials: str, status: str = "active"
    ) -> ConnectorAccountDTO | None: ...
    async def delete_account(self, account_id: UUID, user_id: UUID) -> bool: ...
    async def get_sync_state(self, account_id: UUID) -> SyncStateDTO | None: ...
    async def save_sync_state(self, state: SyncStateDTO) -> None: ...
    async def save_items(self, items: list[ItemCreateDTO]) -> list[ItemDTO]: ...
    async def list_items(self, account_id: UUID, limit: int = 50, offset: int = 0) -> list[ItemDTO]: ...
    async def get_item_by_external_id(self, account_id: UUID, external_id: str) -> ItemDTO | None: ...
    async def get_item_by_id(self, item_id: UUID) -> ItemDTO | None: ...
    async def register_server(self, server: MCPServerDTO) -> MCPServerDTO: ...
    async def get_server(self, name: str) -> MCPServerDTO | None: ...
    async def list_servers(self) -> list[MCPServerDTO]: ...
    async def update_server_tools(
        self, name: str, tools: list[dict[str, Any]]
    ) -> MCPServerDTO | None: ...
    async def delete_server(self, name: str) -> bool: ...


class InMemoryConnectorStore:
    """Process-local store for accounts, sync state, items, and MCP servers, owned by one instance."""

    def __init__(self) -> None:
        self.accounts: dict[UUID, ConnectorAccountDTO] = {}
        self.sync_states: dict[UUID, SyncStateDTO] = {}
        self.items: dict[UUID, ItemDTO] = {}
        self.items_by_external: dict[tuple[UUID, str], ItemDTO] = {}
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

    async def list_active_accounts(self, connector_id: str | None = None) -> list[ConnectorAccountDTO]:
        return [
            account
            for account in self.accounts.values()
            if account.status == "active"
            and (connector_id is None or account.connector_id == connector_id)
        ]

    async def save_account(self, data: ConnectorAccountCreateDTO) -> ConnectorAccountDTO:
        now = _now()
        for existing in self.accounts.values():
            if (
                existing.user_id == data.user_id
                and existing.connector_id == data.connector_id
                and existing.account_label == data.account_label
            ):
                updated = ConnectorAccountDTO(
                    id=existing.id,
                    user_id=existing.user_id,
                    connector_id=existing.connector_id,
                    account_label=existing.account_label,
                    encrypted_credentials=data.encrypted_credentials,
                    scopes=list(data.scopes),
                    status=data.status,
                    enabled_tools=list(data.enabled_tools),
                    created_at=existing.created_at,
                    updated_at=now,
                )
                self.accounts[existing.id] = updated
                return updated

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

    async def update_account_credentials(
        self,
        account_id: UUID,
        encrypted_credentials: str,
        status: str = "active",
    ) -> ConnectorAccountDTO | None:
        account = self.accounts.get(account_id)
        if account is None:
            return None
        updated = ConnectorAccountDTO(
            id=account.id,
            user_id=account.user_id,
            connector_id=account.connector_id,
            account_label=account.account_label,
            encrypted_credentials=encrypted_credentials,
            scopes=list(account.scopes),
            status=status,
            enabled_tools=list(account.enabled_tools),
            created_at=account.created_at,
            updated_at=_now(),
        )
        self.accounts[account_id] = updated
        return updated

    async def delete_account(self, account_id: UUID, user_id: UUID) -> bool:
        account = self.accounts.get(account_id)
        if account is None or account.user_id != user_id:
            return False
        self.accounts.pop(account_id, None)
        return True

    async def get_sync_state(self, account_id: UUID) -> SyncStateDTO | None:
        return self.sync_states.get(account_id)

    async def save_sync_state(self, state: SyncStateDTO) -> None:
        self.sync_states[state.connector_account_id] = state

    async def save_items(self, items: list[ItemCreateDTO]) -> list[ItemDTO]:
        persisted: list[ItemDTO] = []
        now = _now()
        for item_data in items:
            key = (item_data.connector_account_id, item_data.external_id)
            if key in self.items_by_external:
                persisted.append(self.items_by_external[key])
                continue
            item_id = uuid4()
            dto = ItemDTO(
                id=item_id,
                connector_account_id=item_data.connector_account_id,
                external_id=item_data.external_id,
                thread_id=item_data.thread_id,
                sender=item_data.sender,
                recipients=list(item_data.recipients),
                subject=item_data.subject,
                body_text=item_data.body_text,
                received_at=item_data.received_at,
                labels=list(item_data.labels),
                raw_reference=item_data.raw_reference,
                created_at=now,
            )
            self.items[item_id] = dto
            self.items_by_external[key] = dto
            persisted.append(dto)
        return persisted

    async def list_items(
        self, account_id: UUID, limit: int = 50, offset: int = 0
    ) -> list[ItemDTO]:
        matching = [
            item for item in self.items.values()
            if item.connector_account_id == account_id
        ]
        matching.sort(key=lambda item: item.received_at, reverse=True)
        return matching[offset : offset + limit]

    async def get_item_by_external_id(self, account_id: UUID, external_id: str) -> ItemDTO | None:
        return self.items_by_external.get((account_id, external_id))

    async def get_item_by_id(self, item_id: UUID) -> ItemDTO | None:
        return self.items.get(item_id)

    async def register_server(self, server: MCPServerDTO) -> MCPServerDTO:
        self.servers[server.id] = server
        self.servers_by_name[server.name] = server
        return server

    async def get_server(self, name: str) -> MCPServerDTO | None:
        return self.servers_by_name.get(name)

    async def list_servers(self) -> list[MCPServerDTO]:
        return list(self.servers.values())

    async def update_server_tools(
        self, name: str, tools: list[dict[str, Any]]
    ) -> MCPServerDTO | None:
        server = self.servers_by_name.get(name)
        if server is None:
            return None
        updated = MCPServerDTO(
            id=server.id,
            name=server.name,
            transport=server.transport,
            command=server.command,
            url=server.url,
            enabled=server.enabled,
            created_at=server.created_at,
            tools=[dict(t) for t in tools],
            headers=dict(server.headers),
        )
        self.servers[updated.id] = updated
        self.servers_by_name[updated.name] = updated
        return updated

    async def delete_server(self, name: str) -> bool:
        server = self.servers_by_name.pop(name, None)
        if server is None:
            return False
        self.servers.pop(server.id, None)
        return True


class SqlConnectorStore:
    """Durable store over ``connected_accounts``, ``connector_sync_state``, ``connector_items``, and ``mcp_servers``."""

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
            enabled_tools=[],
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    @staticmethod
    def _sync_state_dto(model: ConnectorSyncStateModel) -> SyncStateDTO:
        return SyncStateDTO(
            connector_account_id=model.connector_account_id,
            cursor=model.cursor,
            last_success_at=model.last_success_at,
            last_error=model.last_error,
        )

    @staticmethod
    def _item_dto(model: ConnectorItemModel) -> ItemDTO:
        return ItemDTO(
            id=model.id,
            connector_account_id=model.connector_account_id,
            external_id=model.external_id,
            thread_id=model.thread_id,
            sender=model.sender,
            recipients=list(model.recipients or []),
            subject=model.subject,
            body_text=model.body_text,
            received_at=model.received_at,
            labels=list(model.labels or []),
            raw_reference=model.raw_reference,
            created_at=model.created_at,
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
            tools=list(getattr(model, "tools_json", None) or []),
            headers=dict(getattr(model, "headers_json", None) or {}),
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

    async def list_active_accounts(self, connector_id: str | None = None) -> list[ConnectorAccountDTO]:
        stmt = select(ConnectedAccountModel).where(ConnectedAccountModel.status == "active")
        if connector_id is not None:
            stmt = stmt.where(ConnectedAccountModel.connector_id == connector_id)
        stmt = stmt.order_by(ConnectedAccountModel.created_at.asc())
        result = await self.session.execute(stmt)
        return [self._account_dto(model) for model in result.scalars().all()]

    async def save_account(self, data: ConnectorAccountCreateDTO) -> ConnectorAccountDTO:
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
        await self.session.flush()
        model = await self.session.get(ConnectedAccountModel, account_id)
        if model is None:
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
        return self._account_dto(model)

    async def update_account_credentials(
        self,
        account_id: UUID,
        encrypted_credentials: str,
        status: str = "active",
    ) -> ConnectorAccountDTO | None:
        model = await self.session.get(ConnectedAccountModel, account_id)
        if model is None:
            return None
        model.encrypted_credentials = encrypted_credentials
        model.status = status
        model.updated_at = _now()
        await self.session.flush()
        return self._account_dto(model)

    async def delete_account(self, account_id: UUID, user_id: UUID) -> bool:
        model = await self.session.get(ConnectedAccountModel, account_id)
        if model is None or model.user_id != user_id:
            return False
        await self.session.delete(model)
        await self.session.flush()
        return True

    async def get_sync_state(self, account_id: UUID) -> SyncStateDTO | None:
        model = await self.session.get(ConnectorSyncStateModel, account_id)
        return None if model is None else self._sync_state_dto(model)

    async def save_sync_state(self, state: SyncStateDTO) -> None:
        now = _now()
        stmt = (
            pg_insert(ConnectorSyncStateModel)
            .values(
                connector_account_id=state.connector_account_id,
                cursor=state.cursor,
                last_success_at=state.last_success_at,
                last_error=state.last_error,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=[ConnectorSyncStateModel.connector_account_id],
                set_={
                    "cursor": state.cursor,
                    "last_success_at": state.last_success_at,
                    "last_error": state.last_error,
                    "updated_at": now,
                },
            )
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def save_items(self, items: list[ItemCreateDTO]) -> list[ItemDTO]:
        persisted: list[ItemDTO] = []
        now = _now()
        for item_data in items:
            existing = await self.get_item_by_external_id(
                item_data.connector_account_id, item_data.external_id
            )
            if existing is not None:
                persisted.append(existing)
                continue
            model = ConnectorItemModel(
                id=uuid4(),
                connector_account_id=item_data.connector_account_id,
                external_id=item_data.external_id,
                thread_id=item_data.thread_id,
                sender=item_data.sender,
                recipients=list(item_data.recipients),
                subject=item_data.subject,
                body_text=item_data.body_text,
                received_at=item_data.received_at,
                labels=list(item_data.labels),
                raw_reference=item_data.raw_reference,
                created_at=now,
            )
            self.session.add(model)
            await self.session.flush()
            persisted.append(self._item_dto(model))
        return persisted

    async def list_items(
        self, account_id: UUID, limit: int = 50, offset: int = 0
    ) -> list[ItemDTO]:
        result = await self.session.execute(
            select(ConnectorItemModel)
            .where(ConnectorItemModel.connector_account_id == account_id)
            .order_by(ConnectorItemModel.received_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return [self._item_dto(model) for model in result.scalars().all()]

    async def get_item_by_external_id(self, account_id: UUID, external_id: str) -> ItemDTO | None:
        result = await self.session.execute(
            select(ConnectorItemModel).where(
                ConnectorItemModel.connector_account_id == account_id,
                ConnectorItemModel.external_id == external_id,
            )
        )
        model = result.scalars().first()
        return None if model is None else self._item_dto(model)

    async def get_item_by_id(self, item_id: UUID) -> ItemDTO | None:
        model = await self.session.get(ConnectorItemModel, item_id)
        return None if model is None else self._item_dto(model)

    async def register_server(self, server: MCPServerDTO) -> MCPServerDTO:
        model = MCPServerModel(
            id=server.id,
            name=server.name,
            transport=server.transport,
            command=server.command,
            url=server.url,
            enabled=server.enabled,
            tools_json=list(server.tools),
            headers_json=dict(server.headers),
            created_at=server.created_at,
        )
        self.session.add(model)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise MCPRegistrationError(
                f"an MCP server named {server.name!r} is already registered"
            ) from exc
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

    async def update_server_tools(
        self, name: str, tools: list[dict[str, Any]]
    ) -> MCPServerDTO | None:
        result = await self.session.execute(
            select(MCPServerModel).where(MCPServerModel.name == name)
        )
        model = result.scalars().first()
        if model is None:
            return None
        model.tools_json = [dict(t) for t in tools]
        await self.session.flush()
        return self._server_dto(model)

    async def delete_server(self, name: str) -> bool:
        result = await self.session.execute(
            select(MCPServerModel).where(MCPServerModel.name == name)
        )
        model = result.scalars().first()
        if model is None:
            return False
        await self.session.delete(model)
        await self.session.flush()
        return True


__all__ = [
    "ConnectorStore",
    "InMemoryConnectorStore",
    "SqlConnectorStore",
]
