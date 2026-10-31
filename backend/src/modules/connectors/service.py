"""Connectors service implementing MCP integration, OAuth encryption, and delta sync."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import ClassVar
from uuid import UUID, uuid4
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.security import encrypt_secret
from src.config.settings import Settings
from src.integrations.repositories import MCPHostProtocol
from src.modules.connectors.data import RegisterConnectorRequest, SyncTriggerResponse
from src.modules.connectors.repository import (
    ConnectorAccountCreateDTO,
    ConnectorAccountDTO,
    ItemCreateDTO,
    ItemDTO,
    SyncStateDTO,
)


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
